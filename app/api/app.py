"""FastAPI application (PRD Phase 5).

Thin HTTP shell over :class:`~app.api.service.AnalysisService`: a browser UI, a
synchronous analyse endpoint, a server-sent-event stream for live pipeline
progress, the history list, and the report in HTML/JSON/Markdown. The service is
injected via ``app.state`` so the whole surface is testable with a fake pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

from app.api.service import AnalysisService
from app.data.symbol_search import search_companies

SearchFn = Callable[[str, int], list[dict[str, str]]]


def _service(request: Request) -> AnalysisService:
    service = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover — production wiring
        raise HTTPException(503, "analysis service not configured")
    return service


def create_app(service: AnalysisService, search: SearchFn = search_companies) -> FastAPI:
    app = FastAPI(title="Agentic Equity Research Analyst",
                  description="Educational / academic project — not investment advice.")
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/search")
    def search_endpoint(q: str = "", limit: int = 8) -> JSONResponse:
        return JSONResponse(search(q, max(1, min(limit, 20))))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.post("/api/analyze")
    def analyze(payload: dict[str, Any], request: Request) -> JSONResponse:
        query = (payload or {}).get("query", "").strip()
        if not query:
            raise HTTPException(400, "query is required")
        record = _service(request).run(query)
        return JSONResponse(_summary(record))

    @app.get("/api/analyze/stream")
    def analyze_stream(query: str, request: Request) -> StreamingResponse:
        if not query.strip():
            raise HTTPException(400, "query is required")
        service = _service(request)

        def events():
            for event in service.stream(query.strip()):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/history")
    def history(request: Request, limit: int = 50) -> JSONResponse:
        return JSONResponse(_service(request).history(limit))

    @app.get("/api/report/{analysis_id}")
    def report_json(analysis_id: str, request: Request) -> JSONResponse:
        row = _get_or_404(request, analysis_id)
        return JSONResponse({**_summary(row), "decision": json.loads(row.get("decision_json") or "{}")})

    @app.get("/api/report/{analysis_id}/html", response_class=HTMLResponse)
    def report_html(analysis_id: str, request: Request) -> str:
        row = _get_or_404(request, analysis_id)
        html = row.get("report_html")
        if not html:
            raise HTTPException(404, "no report was generated for this analysis")
        return html

    @app.get("/api/report/{analysis_id}/markdown", response_class=PlainTextResponse)
    def report_md(analysis_id: str, request: Request) -> str:
        row = _get_or_404(request, analysis_id)
        return row.get("report_md") or "# No report generated"

    return app


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {k: record.get(k) for k in
            ("id", "ticker", "company_name", "rating", "composite", "basis", "confidence", "created_at")}


def _get_or_404(request: Request, analysis_id: str) -> dict[str, Any]:
    row = _service(request).get(analysis_id)
    if row is None:
        raise HTTPException(404, "analysis not found")
    return row


_STEPS = ["resolve", "fetch_data", "compute_metrics", "news", "industry", "peer",
          "valuation_assumptions", "risk", "decide", "report"]

INDEX_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Equity Research Analyst</title>
<style>
:root{--ink:#1e293b;--muted:#64748b;--line:#e2e8f0;--accent:#2563eb;--ok:#059669;}
*{box-sizing:border-box}body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);margin:0;background:#f1f5f9}
header{background:#fff;border-bottom:1px solid var(--line);padding:16px 24px}
header h1{font-size:18px;margin:0}header .sub{color:var(--muted);font-size:12px}
.wrap{max-width:1100px;margin:0 auto;padding:20px 24px;display:grid;grid-template-columns:260px 1fr;gap:20px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}
input{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;font-size:14px}
button{margin-top:8px;width:100%;padding:9px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.banner{background:#fff7ed;border:1px solid #fdba74;border-radius:8px;padding:8px 12px;font-size:12px;margin-top:10px}
.steps{list-style:none;padding:0;margin:12px 0 0;font-size:13px}
.steps li{padding:4px 0;color:var(--muted)}.steps li.done{color:var(--ok)}.steps li.done:before{content:"✓ "}
.steps li.pending:before{content:"· "}
.hist{list-style:none;padding:0;margin:0}.hist li{padding:8px 0;border-top:1px solid var(--line);font-size:13px;cursor:pointer}
.hist a{color:var(--accent);text-decoration:none}
.result{min-height:300px}iframe{width:100%;height:80vh;border:1px solid var(--line);border-radius:8px;background:#fff}
.links a{margin-right:14px}
.ac{position:relative}
.ac-list{position:absolute;z-index:10;left:0;right:0;top:calc(100% + 4px);list-style:none;margin:0;padding:4px 0;
  background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px rgba(15,23,42,.12);max-height:320px;overflow-y:auto}
.ac-list li{padding:7px 11px;cursor:pointer;display:flex;flex-direction:column;gap:1px}
.ac-list li.active,.ac-list li:hover{background:#eff6ff}
.ac-list .nm{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ac-list .meta{font-size:11px;color:var(--muted)}
.ac-list .empty{color:var(--muted);font-size:12px;cursor:default}
</style></head><body>
<header><h1>Agentic Equity Research Analyst</h1>
<div class="sub">Educational / academic project — not investment advice.</div></header>
<div class="wrap">
  <div>
    <div class="card">
      <label for="q"><strong>Analyse a company</strong></label>
      <div class="ac">
        <input id="q" placeholder="Search a company, e.g. Tata or INFY.NS" autocomplete="off"
               role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="ac" />
        <ul class="ac-list" id="ac" role="listbox" hidden></ul>
      </div>
      <button id="go">Analyse</button>
      <ul class="steps" id="steps"></ul>
      <div class="banner" id="note" hidden></div>
    </div>
    <div class="card" style="margin-top:16px">
      <strong>History</strong>
      <ul class="hist" id="hist"></ul>
    </div>
  </div>
  <div class="card result" id="result"><em>Enter a company and press Analyse.</em></div>
</div>
<script>
const STEPS = %STEPS%;
const $ = s => document.querySelector(s);
function renderSteps(done){
  $("#steps").innerHTML = STEPS.map(s =>
    `<li class="${done.includes(s)?'done':'pending'}">${s.replace(/_/g,' ')}</li>`).join("");
}
async function loadHistory(){
  const rows = await (await fetch("/api/history")).json();
  $("#hist").innerHTML = rows.map(r =>
    `<li data-id="${r.id}"><a>${r.company_name||r.ticker}</a> — ${r.rating} (${r.composite??'n/a'})</li>`).join("")
    || "<li><em>none yet</em></li>";
  document.querySelectorAll("#hist li[data-id]").forEach(li =>
    li.onclick = () => showReport(li.dataset.id));
}
function showReport(id){
  $("#result").innerHTML =
    `<div class="links"><a href="/api/report/${id}/html" target="_blank">Open full report ↗</a>`+
    `<a href="/api/report/${id}/markdown" target="_blank">Markdown</a>`+
    `<a href="/api/report/${id}" target="_blank">JSON</a></div>`+
    `<iframe src="/api/report/${id}/html"></iframe>`;
}
$("#go").onclick = () => {
  const q = $("#q").value.trim(); if(!q) return;
  $("#go").disabled = true; $("#note").hidden = true;
  $("#result").innerHTML = "<em>Running the pipeline…</em>";
  renderSteps([]);
  const es = new EventSource("/api/analyze/stream?query="+encodeURIComponent(q));
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if(d.type==="progress"){ renderSteps(d.trace||[]); if((d.errors||[]).length){ $("#note").hidden=false; $("#note").textContent="Notes: "+d.errors.join("; "); } }
    else if(d.type==="done"){ es.close(); $("#go").disabled=false; renderSteps(STEPS);
      $("#result").innerHTML = `<h3>${d.company_name} — ${d.rating} (${d.composite??'n/a'}, ${d.basis})</h3>`; showReportInline(d.id); loadHistory(); }
  };
  es.onerror = () => { es.close(); $("#go").disabled=false; $("#result").innerHTML="<em>Stream error.</em>"; };
};
function showReportInline(id){
  const h = document.createElement("div");
  h.innerHTML = `<div class="links"><a href="/api/report/${id}/html" target="_blank">Open full report ↗</a>`+
    `<a href="/api/report/${id}/markdown" target="_blank">Markdown</a> <a href="/api/report/${id}" target="_blank">JSON</a></div>`+
    `<iframe src="/api/report/${id}/html"></iframe>`;
  $("#result").appendChild(h);
}
$("#q").addEventListener("keydown", e => { if(e.key==="Enter") $("#go").click(); });
loadHistory();
</script></body></html>""".replace("%STEPS%", json.dumps(_STEPS))
