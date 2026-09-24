"""Application service (PRD Phase 5).

Wraps the analysis pipeline and the history store: runs an analysis (or streams
its progress), renders and persists the report, and lists/opens past analyses.
The pipeline is injected, so the whole API can be exercised with a fake model
and a fake data service — no network, no key — while ``build_production_service``
wires the real thing for actually serving.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.api.store import HistoryStore
from app.report import render_html, render_markdown


class AnalysisService:
    def __init__(self, pipeline: Any, store: HistoryStore) -> None:
        self.pipeline = pipeline
        self.store = store

    def _record_from_state(self, state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("decision")
        report = state.get("report")
        html = md = None
        if report is not None:
            html = render_html(report, metric_set=state.get("metric_set"),
                               market=state.get("market"), news=state.get("news"))
            md = render_markdown(report)
        return {
            "id": uuid.uuid4().hex[:12],
            "ticker": (decision.ticker if decision else state.get("query", "?")),
            "company_name": (decision.company_name if decision else state.get("query", "?")),
            "rating": (decision.rating.value if decision else "NO_RATING"),
            "composite": (decision.composite_score if decision else None),
            "basis": (decision.basis.value if decision else "NO_RATING"),
            "confidence": (decision.confidence.value if decision else "LOW"),
            "decision_json": (decision.model_dump_json() if decision else "{}"),
            "report_html": html,
            "report_md": md,
        }

    def run(self, query: str) -> dict[str, Any]:
        state = self.pipeline.run(query)
        record = self._record_from_state(state)
        self.store.save(record)
        return record

    def stream(self, query: str):
        """Yield progress events, persist on completion, then a ``done`` event."""
        final_state: dict[str, Any] = {}
        for event in self.pipeline.stream(query):
            if event.get("type") == "final":
                final_state = event.get("state") or {}
            else:
                yield {"type": "progress", "trace": event.get("trace", []),
                       "errors": event.get("errors", [])}
        record = self._record_from_state(final_state)
        self.store.save(record)
        yield {"type": "done", "id": record["id"], "ticker": record["ticker"],
               "company_name": record["company_name"], "rating": record["rating"],
               "composite": record["composite"], "basis": record["basis"],
               "confidence": record["confidence"]}

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit)

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        return self.store.get(analysis_id)


def build_production_service(*, db_path: str | None = None, use_cache: bool = True) -> AnalysisService:
    """Construct the real, network-backed service. Imports the heavy pieces
    lazily so importing this module (and the tests) needs no keys."""
    from app.agents.llm import OpenRouterLLM  # noqa: PLC0415
    from app.agents.orchestrator import AnalysisPipeline  # noqa: PLC0415
    from app.data.news_service import NewsService  # noqa: PLC0415
    from app.data.service import DataService  # noqa: PLC0415
    from app.data.web_research import WebResearchService  # noqa: PLC0415

    pipeline = AnalysisPipeline(
        OpenRouterLLM(), DataService(use_cache=use_cache),
        news_source=NewsService(use_cache=use_cache).news_source,
        web_research=WebResearchService(use_cache=use_cache))
    return AnalysisService(pipeline, HistoryStore(db_path))
