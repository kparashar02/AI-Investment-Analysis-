"""The orchestrator — a LangGraph state graph (PRD 8.1, 22 Phase 3).

The orchestrator reasons about *workflow*, never about finance. It resolves the
company, fetches data, runs the deterministic ratio engine, fans the LLM agents
out in parallel, then hands everything to the deterministic decision engine.
Two rules from the PRD shape it:

* **Dependency order with parallel fan-out.** News, industry and peer analysis
  are independent and run in the same superstep; valuation-assumptions waits for
  industry; risk waits for news, valuation-assumptions and peer; the decision
  waits for all of them.
* **Critical vs. non-critical failure.** If financial data cannot be fetched the
  run aborts with a structured no-rating result. If a *non-critical* agent fails
  (news, industry, peer, risk), the graph records the gap and carries on — the
  decision engine already degrades gracefully to a QUANT_CORE rating and
  discloses the missing pillars.

Every LLM call goes through the injected seam, and every data dependency is
injected, so the whole graph runs offline under test with a fake model.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.industry import IndustryAgent
from app.agents.llm import StructuredLLM
from app.agents.narrative import NarrativeAgent
from app.agents.news import NewsAnalysisAgent
from app.agents.peer import PeerComparisonAgent
from app.agents.report_pipeline import produce_report
from app.agents.risk import RiskAgent
from app.agents.symbol_resolution import SymbolResolutionAgent
from app.agents.valuation_assumptions import ValuationAssumptionsAgent
from app.agents.verifier import VerifierAgent
from app.data.web_research import format_citation
from app.engine.registry import compute_metric_set
from app.engine.scoring import build_decision
from app.models.agent_io import (
    CompanyIdentity,
    IndustryAssessment,
    NewsAnalysis,
    PeerComparison,
    RiskAssessment,
    ValuationAssumptions,
)
from app.models.decision import InvestmentDecision, Rating, RatingBasis
from app.models.metrics import MetricSet
from app.models.report import ResearchReport
from app.models.statements import FinancialStatements, MarketData

NewsSource = Callable[[CompanyIdentity], list[dict[str, Any]]]
PeerFetch = Callable[[str], tuple[FinancialStatements | None, MarketData | None]]


class AnalysisState(TypedDict, total=False):
    """The shared state that flows through the graph.

    ``trace`` and ``errors`` are written by parallel nodes, so they carry an
    ``add`` reducer to merge concurrent appends; every other key is written by a
    single node."""

    query: str
    identity: CompanyIdentity | None
    statements: FinancialStatements | None
    market: MarketData | None
    metric_set: MetricSet | None
    news: NewsAnalysis | None
    industry: IndustryAssessment | None
    peer: PeerComparison | None
    peer_multiples: dict[str, list[float]] | None
    assumptions: ValuationAssumptions | None
    risk: RiskAssessment | None
    decision: InvestmentDecision | None
    report: ResearchReport | None
    trace: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


class AnalysisPipeline:
    """Builds and runs the analysis graph. All dependencies are injected."""

    def __init__(
        self,
        llm: StructuredLLM,
        data_service: Any,
        *,
        news_source: NewsSource | None = None,
        web_research: Any = None,
        peer_fetch: PeerFetch | None = None,
    ) -> None:
        self.llm = llm
        self.data_service = data_service
        self.news_source = news_source or (lambda identity: [])
        self.web_research = web_research   # a WebResearchService, or None for no research
        self.peer_fetch = peer_fetch or self._default_peer_fetch

        self.symbol_agent = SymbolResolutionAgent(llm)
        self.news_agent = NewsAnalysisAgent(llm)
        self.industry_agent = IndustryAgent(llm)
        self.peer_agent = PeerComparisonAgent(llm)
        self.valuation_agent = ValuationAssumptionsAgent(llm)
        self.risk_agent = RiskAgent(llm)
        self.narrative_agent = NarrativeAgent(llm)
        self.verifier = VerifierAgent(llm)
        self.graph = self._build_graph()

    # -- default wiring -----------------------------------------------------

    def _default_peer_fetch(self, ticker: str):
        statements = None
        market = None
        try:
            statements = self.data_service.get_statements(ticker)
        except Exception:  # noqa: BLE001
            statements = None
        try:
            market = self.data_service.get_market_or_none(ticker)
        except Exception:  # noqa: BLE001
            market = None
        return statements, market

    # -- nodes --------------------------------------------------------------

    def _resolve(self, state: AnalysisState) -> dict[str, Any]:
        query = state["query"]
        try:
            identity = self.symbol_agent.run(query)
            return {"identity": identity, "trace": ["resolve"]}
        except Exception as exc:  # noqa: BLE001 — resolution is best-effort
            # Fall back to treating the raw query as the ticker.
            identity = CompanyIdentity(ticker=query.strip().upper(), legal_name=query.strip())
            return {"identity": identity, "trace": ["resolve(fallback)"],
                    "errors": [f"symbol_resolution: {exc}"]}

    def _fetch_data(self, state: AnalysisState) -> dict[str, Any]:
        identity = state.get("identity")
        ticker = identity.ticker if identity else state["query"]
        update: dict[str, Any] = {"trace": ["fetch_data"]}
        try:
            statements = self.data_service.get_statements(
                ticker,
                company_name=(identity.legal_name if identity else None),
                sector=(identity.sector if identity else None),
                industry=(identity.industry if identity else None),
            )
            update["statements"] = statements
        except Exception as exc:  # noqa: BLE001 — critical failure, handled by the router
            update["statements"] = None
            update["errors"] = [f"financial_data (critical): {exc}"]
            return update
        try:
            update["market"] = self.data_service.get_market_or_none(ticker)
        except Exception as exc:  # noqa: BLE001 — market is non-critical
            update["market"] = None
            update["errors"] = [f"market_data: {exc}"]
        return update

    def _route_after_data(self, state: AnalysisState) -> str:
        return "metrics" if state.get("statements") is not None else "abort"

    def _compute_metrics(self, state: AnalysisState) -> dict[str, Any]:
        metric_set = compute_metric_set(state["statements"], state.get("market"))
        return {"metric_set": metric_set, "trace": ["compute_metrics"]}

    def _news(self, state: AnalysisState) -> dict[str, Any]:
        identity = state["identity"]
        try:
            raw = self.news_source(identity)
            analysis = self.news_agent.run(
                raw, company=identity.legal_name or identity.ticker)
            return {"news": analysis, "trace": ["news"]}
        except Exception as exc:  # noqa: BLE001 — non-critical
            return {"news": None, "trace": ["news(failed)"], "errors": [f"news: {exc}"]}

    def _industry(self, state: AnalysisState) -> dict[str, Any]:
        identity = state["identity"]
        try:
            snippets: list[str] = []
            citations = []
            if self.web_research is not None:
                research = self.web_research.research(identity.sector, identity.industry)
                snippets, citations = research.snippets, research.citations
            assessment = self.industry_agent.run(identity.sector, identity.industry, snippets)
            # Citations are authoritative: they are the sources actually fetched
            # from the allowlist, not whatever the model echoed back.
            if citations:
                assessment.citations = [format_citation(c) for c in citations]
            return {"industry": assessment, "trace": ["industry"]}
        except Exception as exc:  # noqa: BLE001
            return {"industry": None, "trace": ["industry(failed)"], "errors": [f"industry: {exc}"]}

    def _peer(self, state: AnalysisState) -> dict[str, Any]:
        identity = state["identity"]
        try:
            comparison = self.peer_agent.run(
                identity.ticker, identity.candidate_peers, self.peer_fetch)
            return {"peer": comparison, "peer_multiples": comparison.peer_multiples,
                    "trace": ["peer"]}
        except Exception as exc:  # noqa: BLE001
            return {"peer": None, "peer_multiples": None, "trace": ["peer(failed)"],
                    "errors": [f"peer: {exc}"]}

    def _valuation_assumptions(self, state: AnalysisState) -> dict[str, Any]:
        metric_set = state["metric_set"]
        industry = state.get("industry")
        context = {
            "revenue_cagr_pct": metric_set.value("revenue_cagr"),
            "ebitda_margin_pct": metric_set.value("ebitda_margin"),
            "ebit_margin_pct": metric_set.value("ebit_margin"),
            "industry_growth_outlook_1_5": industry.industry_growth_outlook if industry else None,
        }
        try:
            assumptions = self.valuation_agent.run(context)
            return {"assumptions": assumptions, "trace": ["valuation_assumptions"]}
        except Exception as exc:  # noqa: BLE001
            return {"assumptions": None, "trace": ["valuation_assumptions(failed)"],
                    "errors": [f"valuation_assumptions: {exc}"]}

    def _risk(self, state: AnalysisState) -> dict[str, Any]:
        metric_set = state["metric_set"]
        news = state.get("news")
        industry = state.get("industry")
        context = {
            "debt_to_equity": metric_set.value("debt_to_equity"),
            "net_debt_to_ebitda": metric_set.value("net_debt_to_ebitda"),
            "interest_coverage": metric_set.value("interest_coverage"),
            "current_ratio": metric_set.value("current_ratio"),
            "altman_z_score": metric_set.value("altman_z_score"),
            "news_score": (news.aggregate.news_score if news and news.aggregate else None),
            "industry_score": (industry.industry_score if industry else None),
        }
        try:
            assessment = self.risk_agent.run(context)
            return {"risk": assessment, "trace": ["risk"]}
        except Exception as exc:  # noqa: BLE001
            return {"risk": None, "trace": ["risk(failed)"], "errors": [f"risk: {exc}"]}

    def _decide(self, state: AnalysisState) -> dict[str, Any]:
        decision = build_decision(
            state["metric_set"], state["statements"], state.get("market"),
            peer_multiples=state.get("peer_multiples"),
            news=state.get("news"), industry=state.get("industry"),
            risk=state.get("risk"), assumptions=state.get("assumptions"),
        )
        return {"decision": decision, "trace": ["decide"]}

    def _report(self, state: AnalysisState) -> dict[str, Any]:
        market = state.get("market")
        as_of = getattr(market, "as_of", None) if market is not None else None
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            report, decision = produce_report(
                state["decision"], self.narrative_agent, self.verifier,
                metric_set=state["metric_set"], statements=state["statements"], market=market,
                industry=state.get("industry"), news=state.get("news"),
                as_of=as_of, generated_at=generated_at)
            # decision may have been V9-downgraded by a double verification failure.
            return {"report": report, "decision": decision, "trace": ["report"]}
        except Exception as exc:  # noqa: BLE001 — report generation is non-critical
            return {"report": None, "trace": ["report(failed)"], "errors": [f"report: {exc}"]}

    def _abort(self, state: AnalysisState) -> dict[str, Any]:
        identity = state.get("identity")
        decision = InvestmentDecision(
            ticker=(identity.ticker if identity else state["query"]),
            company_name=(identity.legal_name if identity else state["query"]),
            period="", rating=Rating.NO_RATING, basis=RatingBasis.NO_RATING,
            warnings=["Analysis aborted: financial statements could not be retrieved from any "
                      "source. No rating can be issued (PRD 8.1 critical-agent failure)."],
        )
        return {"decision": decision, "trace": ["abort"]}

    # -- graph --------------------------------------------------------------

    def _build_graph(self):
        g = StateGraph(AnalysisState)
        g.add_node("resolve", self._resolve)
        g.add_node("fetch_data", self._fetch_data)
        g.add_node("compute_metrics", self._compute_metrics)
        g.add_node("news", self._news)
        g.add_node("industry", self._industry)
        g.add_node("peer", self._peer)
        g.add_node("valuation_assumptions", self._valuation_assumptions)
        g.add_node("risk", self._risk)
        g.add_node("decide", self._decide)
        g.add_node("report", self._report)
        g.add_node("abort", self._abort)

        g.add_edge(START, "resolve")
        g.add_edge("resolve", "fetch_data")
        g.add_conditional_edges("fetch_data", self._route_after_data,
                                {"metrics": "compute_metrics", "abort": "abort"})
        # Fan the three independent agents out at the SAME depth from
        # compute_metrics, so they run in one superstep and join cleanly.
        g.add_edge("compute_metrics", "news")
        g.add_edge("compute_metrics", "industry")
        g.add_edge("compute_metrics", "peer")
        # valuation_assumptions is the join: it runs once, after news, industry
        # and peer have all completed (a same-depth fan-in barriers correctly;
        # an uneven-depth one would fire the join twice). It reads the industry
        # outlook from state.
        g.add_edge("news", "valuation_assumptions")
        g.add_edge("industry", "valuation_assumptions")
        g.add_edge("peer", "valuation_assumptions")
        # Then a linear tail: risk (reads news/industry/metrics) → decision.
        g.add_edge("valuation_assumptions", "risk")
        g.add_edge("risk", "decide")
        g.add_edge("decide", "report")
        g.add_edge("report", END)
        g.add_edge("abort", END)
        return g.compile()

    # -- public API ---------------------------------------------------------

    def run(self, query: str) -> AnalysisState:
        """Run the full pipeline for a company query; returns the final state."""
        initial: AnalysisState = {"query": query, "trace": [], "errors": []}
        return self.graph.invoke(initial)  # type: ignore[return-value]

    def stream(self, query: str):
        """Run the pipeline, yielding progress events as supersteps complete.

        Each event is ``{"type": "progress", "trace": [...], "errors": [...]}``
        with the accumulated trace so far; the final event is
        ``{"type": "final", "state": <AnalysisState>}`` carrying the decision and
        report. Used by the API for live server-sent-event progress."""
        initial: AnalysisState = {"query": query, "trace": [], "errors": []}
        last_state: AnalysisState = {}
        for state in self.graph.stream(initial, stream_mode="values"):
            last_state = state  # type: ignore[assignment]
            yield {"type": "progress",
                   "trace": list(state.get("trace", [])),
                   "errors": list(state.get("errors", []))}
        yield {"type": "final", "state": last_state}

    def analyze(self, query: str) -> InvestmentDecision:
        """Convenience: run and return just the decision."""
        state = self.run(query)
        decision = state.get("decision")
        assert decision is not None  # abort node guarantees a decision on every path
        return decision
