"""FastAPI backend, service and history store (app/api) — offline via a fake pipeline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents.llm import FakeLLM
from app.agents.narrative import NarrativeAgent
from app.api.app import create_app
from app.api.service import AnalysisService
from app.api.store import HistoryStore
from app.engine.scoring import build_decision


# --- store -----------------------------------------------------------------

def test_store_round_trip():
    store = HistoryStore(":memory:")
    store.save({"id": "abc", "ticker": "ACME.NS", "company_name": "Acme",
                "rating": "BUY", "composite": 82.5, "basis": "FULL",
                "confidence": "HIGH", "decision_json": "{}", "report_html": "<h1>r</h1>",
                "report_md": "# r"})
    assert store.get("abc")["rating"] == "BUY"
    rows = store.list()
    assert len(rows) == 1 and rows[0]["ticker"] == "ACME.NS"
    assert "report_html" not in rows[0]         # list returns summaries only


def test_store_missing_returns_none():
    assert HistoryStore(":memory:").get("nope") is None


# --- fake pipeline ---------------------------------------------------------

@pytest.fixture
def sample_state(acme, acme_metrics):
    decision = build_decision(acme_metrics, acme.statements, acme.market)
    report = NarrativeAgent(FakeLLM([{"executive_summary": ["Solid."], "rating_rationale": "r"}])).run(decision)
    return {"query": "Acme", "decision": decision, "report": report,
            "metric_set": acme_metrics, "market": acme.market, "news": None,
            "trace": ["resolve", "fetch_data", "compute_metrics", "news", "industry",
                      "peer", "valuation_assumptions", "risk", "decide", "report"],
            "errors": []}


class FakePipeline:
    def __init__(self, state):
        self._state = state
        self.llm = SimpleNamespace(available=lambda: True)

    def run(self, query):
        return self._state

    def stream(self, query):
        yield {"type": "progress", "trace": ["resolve"], "errors": []}
        yield {"type": "progress", "trace": self._state["trace"], "errors": []}
        yield {"type": "final", "state": self._state}


@pytest.fixture
def client(sample_state, tmp_path):
    service = AnalysisService(FakePipeline(sample_state), HistoryStore(tmp_path / "h.db"))
    return TestClient(create_app(service))


# --- service ---------------------------------------------------------------

def test_service_run_persists_a_report(sample_state, tmp_path):
    service = AnalysisService(FakePipeline(sample_state), HistoryStore(tmp_path / "h.db"))
    record = service.run("Acme")
    assert record["rating"] == "BUY"
    assert record["report_html"] and "<!DOCTYPE html>" in record["report_html"]
    assert service.get(record["id"])["composite"] is not None


def test_service_stream_emits_progress_then_done(sample_state, tmp_path):
    service = AnalysisService(FakePipeline(sample_state), HistoryStore(tmp_path / "h.db"))
    events = list(service.stream("Acme"))
    assert events[0]["type"] == "progress"
    assert events[-1]["type"] == "done"
    assert events[-1]["rating"] == "BUY"
    assert service.get(events[-1]["id"]) is not None     # persisted on completion


# --- API -------------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_index_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "Analyse" in r.text


def test_analyze_then_history_and_report(client):
    r = client.post("/api/analyze", json={"query": "Acme"})
    assert r.status_code == 200
    rec = r.json()
    assert rec["rating"] == "BUY"

    hist = client.get("/api/history").json()
    assert len(hist) == 1 and hist[0]["id"] == rec["id"]

    html = client.get(f"/api/report/{rec['id']}/html")
    assert html.status_code == 200 and "<!DOCTYPE html>" in html.text
    assert "not investment advice" in html.text.lower()

    dj = client.get(f"/api/report/{rec['id']}").json()
    assert dj["decision"]["rating"] == "BUY"
    assert client.get(f"/api/report/{rec['id']}/markdown").status_code == 200


def test_analyze_requires_query(client):
    assert client.post("/api/analyze", json={}).status_code == 400


def test_report_404_for_unknown_id(client):
    assert client.get("/api/report/nope/html").status_code == 404


def test_stream_endpoint_returns_sse(client):
    r = client.get("/api/analyze/stream?query=Acme")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert '"type": "done"' in r.text and '"type": "progress"' in r.text
