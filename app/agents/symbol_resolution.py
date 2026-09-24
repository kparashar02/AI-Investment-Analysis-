"""Symbol Resolution Agent (PRD 8.2).

Turns free-text company input ("TCS", "Tata Consultancy", "TCS.NS", "532540")
into a canonical :class:`CompanyIdentity`. In production this is backed by an
FMP symbol search and a static NSE/BSE map with the LLM only disambiguating; in
this first cut the LLM proposes the identity and Python normalises the ticker
suffix. Ambiguity handling (returning candidates rather than guessing) is a
follow-up once the static map is wired.
"""

from __future__ import annotations

from app.agents.base import Agent
from app.models.agent_io import CompanyIdentity

_SYSTEM = (
    "You resolve an Indian-listed company from free text to its canonical "
    "identity. Prefer NSE. Use the '.NS' suffix for NSE and '.BO' for BSE. "
    "Indian fiscal years end 31 March, so fiscal_year_end is '03-31' unless you "
    "are certain otherwise. Only report fields you are confident about; leave "
    "the rest null. Never invent an ISIN. candidate_peers should list 3-6 "
    "comparable Indian-listed companies by ticker if you know them."
)


class SymbolResolutionAgent(Agent):
    name = "symbol_resolution"
    model_tier = "sonnet"

    def run(self, query: str) -> CompanyIdentity:
        user = f"Resolve this to a listed-company identity: {query!r}"
        identity: CompanyIdentity = self._ask(_SYSTEM, user, CompanyIdentity)  # type: ignore[assignment]
        # Python owns the invariant that ``ticker`` is populated: fall back to
        # the NSE ticker, then the raw query, rather than trust an empty field.
        if not identity.ticker:
            identity.ticker = identity.ticker_nse or query.strip().upper()
        return identity
