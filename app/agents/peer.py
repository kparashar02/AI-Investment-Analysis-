"""Peer Comparison Agent (PRD 8.8).

Selection is deterministic (the candidate peers from symbol resolution), the
multiples and percentiles are pure Python (reusing the valuation engine's own
multiple computation), and the LLM's only job is to *review* the set and reject
an economically dissimilar peer with a stated reason. The output feeds the
valuation pillar as a ``peer_multiples`` map, so the composite's valuation
pillar becomes fully scorable.

Peer financials are fetched through an injected ``fetch_peer`` callable (backed
by the DataService in production, a fake in tests), keeping this agent testable
with no network.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from app.agents.base import Agent
from app.agents.llm import LLMError
from app.engine.relative_valuation import _company_multiples
from app.models.agent_io import PeerComparison, PeerData
from app.models.statements import FinancialStatements, MarketData

_MULTIPLE_KEYS = ("pe", "pb", "ev_ebitda", "ev_sales", "p_fcf")

PeerFetch = Callable[[str], tuple[FinancialStatements | None, MarketData | None]]

_REVIEW_SYSTEM = (
    "You are reviewing a proposed peer set for an equity comparison. For each "
    "candidate, decide keep=true unless it is economically dissimilar (different "
    "business model, wildly different scale, or a holding company), giving a "
    "short reason when you reject one. Do not add peers; only review the given list."
)


class _PeerVerdict(BaseModel):
    ticker: str
    keep: bool = True
    reason: str = ""


class _PeerReview(BaseModel):
    verdicts: list[_PeerVerdict] = Field(default_factory=list)


class PeerComparisonAgent(Agent):
    name = "peer"
    model_tier = "opus"

    def run(
        self,
        subject_ticker: str,
        candidate_peers: list[str],
        fetch_peer: PeerFetch,
    ) -> PeerComparison:
        peers: list[PeerData] = []
        for ticker in candidate_peers:
            statements, market = self._safe_fetch(fetch_peer, ticker)
            if statements is None or market is None:
                peers.append(PeerData(ticker=ticker, accepted=False, reject_reason="no data available"))
                continue
            multiples = self._multiples(statements, market)
            peers.append(PeerData(ticker=ticker, name=statements.company_name, multiples=multiples))

        self._apply_review(subject_ticker, peers)

        accepted = [p for p in peers if p.accepted]
        peer_multiples: dict[str, list[float]] = {}
        for key in _MULTIPLE_KEYS:
            values = [p.multiples[key] for p in accepted if key in p.multiples]
            if values:
                peer_multiples[key] = values

        notes: list[str] = []
        if len(accepted) < 3:
            notes.append(f"only {len(accepted)} valid peer(s); below the 3 needed for a stable "
                         f"median (veto V10 — valuation weight is redistributed).")

        return PeerComparison(
            subject_ticker=subject_ticker, peers=peers, peer_multiples=peer_multiples,
            valid_peer_count=len(accepted), notes=notes,
        )

    @staticmethod
    def _safe_fetch(fetch_peer: PeerFetch, ticker: str):
        try:
            return fetch_peer(ticker)
        except Exception:  # noqa: BLE001 — a bad peer fetch must not sink the analysis
            return None, None

    @staticmethod
    def _multiples(statements: FinancialStatements, market: MarketData) -> dict[str, float]:
        raw = _company_multiples(statements, market)
        return {key: raw[key]["value"] for key in _MULTIPLE_KEYS
                if raw.get(key, {}).get("value") is not None}

    def _apply_review(self, subject_ticker: str, peers: list[PeerData]) -> None:
        reviewable = [p for p in peers if p.accepted]
        if not reviewable:
            return
        listing = "\n".join(f"- {p.ticker} ({p.name or '?'}) multiples={p.multiples}"
                            for p in reviewable)
        user = f"Subject: {subject_ticker}\nCandidate peers:\n{listing}"
        try:
            review: _PeerReview = self._ask(_REVIEW_SYSTEM, user, _PeerReview)  # type: ignore[assignment]
        except LLMError:
            return  # no review available -> keep the deterministic set
        verdicts = {v.ticker: v for v in review.verdicts}
        for peer in reviewable:
            verdict = verdicts.get(peer.ticker)
            if verdict is not None and not verdict.keep:
                peer.accepted = False
                peer.reject_reason = verdict.reason or "rejected by peer review"
