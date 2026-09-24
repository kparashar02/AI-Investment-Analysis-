# Agentic AI Equity Research Analyst

Automated fundamental equity research for NSE/BSE-listed Indian companies.
Given a single ticker, a multi-agent system retrieves financial statements,
computes a full metrics suite, values the company, analyses recent news
article by article, researches its industry, benchmarks it against peers,
and produces an equity research report with a BUY/HOLD/SELL recommendation.

**The design principle the whole project rests on:**

> Numbers are computed. Judgements are scored. Only the narrative is generated.

The recommendation comes from a deterministic weighted scoring engine in
Python — not from a language model. The model's role is confined to what is
genuinely linguistic: extracting structured facts from prose, assessing
qualitative inputs into bounded scored judgements, and explaining the computed
result in analyst language. The same company analysed twice on the same data
returns the same recommendation.

See [PRD.md](PRD.md) for the full specification and
[docs/METHODOLOGY.md](docs/METHODOLOGY.md) for every formula, convention,
weight and threshold.

> ⚠️ **Educational and academic use only.** This is a postgraduate management
> project. It is not investment advice, and it is not a research report issued
> by a SEBI-registered Research Analyst or Investment Adviser. See PRD §20.

---

## Status

| Phase | Scope | Status |
|---|---|---|
| **1** | Deterministic computation layer — 54 metrics, 4 composite screens, normalisation, config validation | ✅ **complete** |
| **1b** | Data providers — hardened yfinance source (free, Indian-friendly), **manual-CSV import** as the reliable fallback, normalisation to the canonical schema, disk cache with per-source TTL, token-bucket rate limiter, data-quality report | ✅ **complete** (free/offline path; FMP not used — Indian fundamentals are paid-gated, see below) |
| **2** | Valuation (two-stage FCFF DCF + relative + reconciliation) and the deterministic decision engine — composite score, rating bands, 10 veto rules | ✅ **complete, no LLM in the loop** |
| **3** | Agent layer — LangGraph orchestration; symbol, news (per-article), industry, peer, valuation-assumption, risk agents; completes the seven-pillar composite. Live news feed (Alpha Vantage → NewsAPI) and **allowlisted industry web research** (Brave Search, filtered by `sources.yaml`, citation-gated), both cached | ✅ **complete** (runs offline under test) |
| **4** | Narrative agent (explains the fixed rating), verifier (deterministic numeric grounding + regenerate/V9 loop), 18-section report in Markdown **and self-contained HTML with 8 inline-SVG charts**, SEBI disclaimer, optional PDF | ✅ **complete** (PDF via WeasyPrint where its native libs exist; browser print-to-PDF otherwise) |
| **5** | FastAPI backend — analyse endpoint, **live SSE progress**, SQLite history, HTML/JSON/Markdown report endpoints, and a served single-page browser UI | ✅ **complete** (backend + minimal UI; score-explorer / comparison views to add) |
| **6** | Evaluation harness — six tracks (golden accuracy, directional, rubric, determinism, groundedness, injection) writing `docs/EVALUATION.md` | ✅ **complete** (Tracks 1/4/5/6 run offline; 2/3 provide scoring logic for live/judged runs) |

Phases 1 and 2 are deliberately front-loaded before any agent work. If the
project ran out of time here, the result would be a rigorous quantitative
equity-analysis engine — still a defensible deliverable. The reverse order
would risk an impressive demo built on unverified numbers.

---

## What works today

The computation layer runs with **no API key, no network and no language
model**. That is the Phase 1 exit gate: a complete, accurate ratio sheet
produced from normalised statements alone.

```bash
python -m app.cli list
```
```bash
python -m app.cli sheet acme_industries --pillars
```
```bash
python -m app.cli sheet acme_industries --working
```
```bash
python -m app.cli fingerprint acme_industries --runs 5
```
```bash
python -m app.cli json acme_industries
```

Sample output (abridged):

```
Acme Industries Ltd (SYNTHETIC)  [ACME.NS]
Capital Goods  ->  threshold curve: 'capital_goods'
Period: FY2026    thresholds.yaml v1.0.0

FUNDAMENTALS
  Return on Equity                     32.39%   score  93.0
  Return on Capital Employed           27.78%   score  86.7
  Debt to Equity                        0.40x   score  85.0
  Piotroski F-Score                      9.00   score 100.0

CASH FLOW & EARNINGS QUALITY
  OCF / PAT (Cash Conversion)           1.33x   score  93.6
  Capex Intensity                       7.00%   score  86.0
        note: Scored against the 'capital_goods' sector curve.

PILLAR PREVIEW  (Phase 1 pillars only — NOT a composite score)
  fundamentals             85.8 / 100   pillar weight 30%
        roe                        contributes  18.59
        roce                       contributes  17.33
  ...

DATA: 54/54 metrics computed (100% of the metric library)
FINGERPRINT: d88ab08421a76a11
```

This is the ratio sheet alone. The composite score, the DCF and the
BUY/HOLD/SELL rating are produced by the Phase 2 decision engine — see
[Scoring a company](#scoring-a-company-phase-2) below — which likewise runs with
no key, no network and no language model.

---

## Setup

Requires Python 3.11+ (developed on 3.14).

```bash
python -m venv .venv
```

Windows:
```bash
.\.venv\Scripts\python.exe -m pip install "pydantic>=2.7,<3.0" "PyYAML>=6.0" "pytest>=8.0"
```

macOS/Linux:
```bash
./.venv/bin/python -m pip install "pydantic>=2.7,<3.0" "PyYAML>=6.0" "pytest>=8.0"
```

Those three packages are all the computation layer needs. `requirements.txt`
lists the full stack grouped by phase; install the rest as each phase lands.

API keys are only needed from Phase 1b onward:

```bash
cp .env.example .env
```

---

## Running the tests

```bash
.\.venv\Scripts\python.exe -m pytest
```

```bash
.\.venv\Scripts\python.exe -m pytest -m golden -v
```

```bash
.\.venv\Scripts\python.exe -m pytest -m determinism -v
```

The full suite runs green with no skips. Track 1 validates the engine against
the synthetic golden's closed-form values; validation against a *real* company's
filing still awaits a golden hand-entered from an annual report
(`data/golden/TEMPLATE.csv`) — the one honest gap, documented in
`docs/EVALUATION.md`.

---

## Layout

```
├── PRD.md                      product requirements — the specification
├── docs/METHODOLOGY.md         every formula, convention, weight, threshold
├── app/
│   ├── config/
│   │   ├── weights.yaml        THE SCORING MODEL — pillar weights, rating bands, vetoes
│   │   ├── thresholds.yaml     normalisation curves + sector overrides
│   │   ├── valuation.yaml      DCF assumptions — WACC inputs, growth fade, terminal bounds
│   │   ├── sources.yaml        web research allowlist and source credibility tiers
│   │   └── settings.py         config loading with invariant validation
│   ├── models/                 Pydantic contracts shared across layers
│   ├── engine/                 ★ deterministic computation — no LLM, no network
│   │   ├── helpers.py          safe_div, CAGR guards, OLS, interpolation
│   │   ├── ratios.py           profitability, returns, leverage, liquidity, efficiency
│   │   ├── growth.py           CAGRs, consistency, margin trend
│   │   ├── cashflow_quality.py OCF/PAT and the earnings-quality streak
│   │   ├── screens.py          Piotroski, Altman, DuPont
│   │   ├── normalisation.py    value → 0-100 score, pillar assembly
│   │   ├── dcf.py              ★ two-stage FCFF DCF, WACC build-up, sensitivity grid
│   │   ├── relative_valuation.py peer & own-history multiples (Phase 3 inputs)
│   │   ├── valuation.py        reconciliation → fair value range + valuation pillar
│   │   ├── scoring.py          ★ composite, rating bands, the decision
│   │   ├── guardrails.py       the 10 veto rules
│   │   ├── agent_scoring.py    news/industry/risk pillars from bounded agent output
│   │   ├── verification.py     deterministic numeric grounding of narrative prose
│   │   └── registry.py         the Phase 1 entry point
│   ├── agents/                 ★ Layer 3 — LLM agents (may call the model)
│   │   ├── llm.py              StructuredLLM seam: FakeLLM + OpenRouterLLM
│   │   ├── orchestrator.py     the LangGraph pipeline (fan-out + failure gate)
│   │   ├── symbol_resolution.py · news.py · industry.py
│   │   ├── peer.py · valuation_assumptions.py · risk.py
│   │   ├── narrative.py        explains the fixed rating (cannot change it)
│   │   ├── verifier.py · report_pipeline.py   verify → regenerate → V9 loop
│   │   └── base.py
│   ├── report/                 ★ Layer 4 — rendering
│   │   ├── markdown.py         the 18-section document
│   │   ├── html.py             self-contained HTML (print-ready)
│   │   ├── charts.py           8 inline-SVG charts (no chart dependency)
│   │   └── pdf.py              optional WeasyPrint PDF (graceful fallback)
│   ├── api/                    ★ Layer 5 — FastAPI app
│   │   ├── app.py              routes + SSE stream + served single-page UI
│   │   ├── service.py          AnalysisService (injectable pipeline)
│   │   └── store.py            SQLite history (stdlib sqlite3)
│   └── eval/                   ★ Phase 6 — six-track evaluation harness
│       ├── tracks.py           golden, directional, determinism, groundedness, injection
│       ├── rubric.py           report-quality rubric + LLM judge
│       └── harness.py          runs the tracks → docs/EVALUATION.md
│   ├── data/                   ★ Layer 1 — sources (may touch the network)
│   │   ├── providers/
│   │   │   ├── base.py         provider contract + raw-payload schema
│   │   │   ├── yfinance_fallback.py
│   │   │   └── news.py         Alpha Vantage + NewsAPI article providers
│   │   ├── normalise.py        provider payload → canonical schema
│   │   ├── cache.py            disk cache, per-source TTL
│   │   ├── rate_limiter.py     token bucket + backoff/jitter retry
│   │   ├── service.py          DataService — statements/market fallback chain
│   │   ├── news_service.py     NewsService — news fallback chain + cache
│   │   ├── allowlist.py        source allowlist classifier (tiers / blocked)
│   │   ├── web_research.py     WebResearchService — allowlisted industry search
│   │   ├── csv_import.py       manual CSV → statements (hand-entry fallback)
│   │   └── fixtures.py         golden-file loader
│   └── cli.py
├── data/golden/
│   ├── acme_industries.json    SYNTHETIC — healthy company, exact ratio values
│   ├── leveraged_cyclicals.json SYNTHETIC — distressed, sits on the veto boundaries
│   └── TEMPLATE.json           for REAL company golden values (fill in by hand)
└── tests/unit/                 176 tests
```

The scoring model lives in YAML rather than in code so that an evaluator can
read and argue with it without reading Python — and so the config version can
be stamped onto every report.

---

## The layer boundary

```
app/data    →  provide facts        (APIs, fixtures)
app/engine  →  perform calculation  (deterministic Python, unit-tested)
app/agents  →  perform analysis     (LLM, on unstructured input only)
app/report  →  interpret            (narrative, cannot change the rating)
```

`app/engine/` may not import an agent, an LLM client, the network, or a source
of randomness. `tests/unit/test_layering.py` parses the engine's AST and fails
if it ever does. It is a cheap test protecting an expensive claim: once the
engine can call a model, "the recommendation is computed, not generated" stops
being true, and no amount of documentation restores it.

---

## Test fixtures

Two synthetic companies, both clearly labelled as fabricated:

| Fixture | Purpose |
|---|---|
| `acme_industries` | Healthy. Figures chosen so every ratio has an exact closed-form value; Piotroski 9/9 exercises all nine positive branches. |
| `leveraged_cyclicals` | Distressed. Sits on the V2, V3 and V4 veto boundaries — net debt/EBITDA 5.76×, interest coverage 1.25×, a three-year cash-conversion streak, Altman Z of 1.14 — and has negative FCF, so the multiple-suppression rules are exercised. |

**Synthetic fixtures test the arithmetic. They do not validate the system
against reality.** That requires a real company's statements hand-entered from
its annual report, with the expected ratios computed independently in Excel —
see `data/golden/TEMPLATE.json`. Until at least one exists, the real-company
test reports as skipped, which is the honest signal.

---

## Fetching a live company (Phase 1b)

The data layer can now retrieve a real NSE/BSE company through `yfinance`,
normalise it into the canonical schema, and score it with the Phase 1 engine.
This needs the network and one extra package:

```bash
.\.venv\Scripts\python.exe -m pip install yfinance
```

```bash
python -m app.cli fetch TCS.NS --sheet --pillars
```

`fetch` prints a **data-quality report** first — completeness percentage,
missing core fields, balance-sheet checks, and whether the data clears the
70% / 3-year bar for veto V1 — before the ratio sheet. Repeat fetches are
served from the on-disk cache (`data/cache/`, per-source TTLs) so they cost no
network calls and reproduce exactly.

Everything else in the system still runs with no key, no network and no model;
`yfinance` is imported lazily and only `fetch` touches it.

## Scoring a company (Phase 2)

The decision engine turns the metric sheet into a valuation, a composite score
and a rating — **with no language model in the loop**. It runs on the golden
fixtures with no key or network:

```bash
python -m app.cli score acme_industries
```

```bash
python -m app.cli score leveraged_cyclicals
```

It prints the full decomposition: each pillar's contribution, the two-stage
FCFF **DCF** (WACC build-up, terminal value, a reconciled fair-value range and
upside/downside to CMP), every **veto** rule with the values that triggered it,
and the final **BUY / ACCUMULATE / HOLD / REDUCE / SELL** rating.

Two design points worth knowing:

- **The recommendation is computed, not generated.** The rating comes from a
  weighted score and a set of veto rules in YAML and Python. `acme_industries`
  scores a BUY; `leveraged_cyclicals` is capped to SELL by vetoes V2/V3/V4
  (severe leverage, poor cash conversion, Altman distress) *regardless* of its
  DCF upside — a weighted average must not wash out a fact that should be
  decisive on its own.
- **A partial model that says so.** Three of the seven pillars (industry, news,
  risk) need the Phase 3 agents, and valuation needs a peer set. Until then the
  composite is renormalised over the pillars actually scored and labelled
  `QUANT_CORE`, with the missing pillars and the covered weight disclosed on
  every run. It is deliberately **not** presented as the full seven-pillar
  score.

The same decision is available on a live ticker: `fetch TCS.NS --score`.

## Analyzing a company end to end (Phase 3)

The agent layer supplies the three pillars the deterministic core cannot
(industry, news, risk) plus the peer set, turning a `QUANT_CORE` score into the
full **seven-pillar** composite. A LangGraph pipeline resolves the company,
fetches data, runs the ratio engine, fans the agents out in parallel, and hands
everything to the same deterministic decision engine.

```bash
python -m app.cli analyze "TCS"
```

This needs the network, `yfinance`, and an OpenRouter key (a **free** model is
fine for development):

```bash
pip install yfinance
```

Set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` in `.env` (see `.env.example`).
For real news, also set `ALPHA_VANTAGE_API_KEY` (primary) and/or `NEWSAPI_KEY`
(fallback); the news feed is cached for 6 hours and paced under the free-tier
rate limit. With no news key set, the news pillar simply scores a neutral 50 and
says so — the run still completes. For live industry research and citations, set
`BRAVE_API_KEY`; every search result is filtered through the `sources.yaml`
allowlist (tiered domains in, tip sites and forums out) before the industry
agent sees it, and only the sources actually admitted become the report's
citations — the model cannot invent them.

The design principle is unchanged and is applied at the finest grain here: **the
model emits only bounded, structured judgements** — an article's materiality is
an integer 1–5, an industry driver is 1–5, a risk dimension is 0–100 — and every
score is still computed in Python. The recommendation remains computed, not
generated. Agents fail soft: if a non-critical agent (news, industry, peer,
risk) fails, the pipeline records the gap and degrades to a disclosed
`QUANT_CORE` rating; only a failure to fetch financial statements aborts.

A live run additionally writes a **research report** — Markdown and a
self-contained, print-ready **HTML** file with 8 charts (`--pdf` also renders a
PDF where WeasyPrint's native libraries are available): the narrative agent
explains the fixed rating and the verifier checks it — see below.
The whole pipeline is unit-tested offline with a fake model and a fake data
service — no key or network needed to run the suite.

## The research report (Phase 4)

The last stage writes an 18-section report. Its design is the project's thesis
made concrete:

- **The narrative explains a rating it cannot change.** The narrative agent is
  handed the rating, the composite score and every valuation number as *fixed
  facts*. The report object carries no rating field it could write — the rating
  lives on the decision — so even a successful prompt injection can only change
  the prose, never the recommendation. If the agent disagrees with the score, it
  says so in an `analyst_caveat`, which is surfaced, not suppressed.
- **Every number is verified against the data layer.** The verifier's core check
  is deterministic Python (`app/engine/verification.py`): it extracts each number
  from the prose and grounds it against what the engine actually computed. If a
  number can't be grounded, the report is regenerated once with the specific
  problems fed back; if it still fails, the report ships with a visible
  **unverified-claims banner** and the confidence is downgraded (veto V9). The
  failure is never hidden.
- Every report stamps the mandatory SEBI **educational-use disclaimer** (PRD 20).

**Output formats.** The report renders to Markdown and to a single self-contained
HTML file (inline CSS, 8 inline-SVG charts, no external assets or JavaScript) that
prints cleanly to PDF from any browser. `analyze --pdf` renders a PDF directly
where WeasyPrint's native libraries are installed; otherwise it points you to
browser print-to-PDF. The charts — pillar radar, revenue/PAT, margin trend, OCF
vs PAT, peer multiples, price-vs-fair-value band, DCF sensitivity heatmap, and
news-over-time — are hand-generated SVG, so they are deterministic and need no
charting library.

## A note on Indian data coverage

Free, reliable Indian *fundamentals* are the hard part. `yfinance` is the best
free source and is the effective primary here; **FMP was evaluated and not used**
— its free tier is US-centric and Indian statements are gated to paid plans (the
PRD's R2 risk). When live coverage is thin or unavailable, the **manual-CSV
import** is the answer: hand-enter a company's five years from its annual report
once, and it runs through the whole pipeline.

```bash
python -m app.cli import-csv data/golden/mycompany.csv --save mycompany --score
```

Values are entered in the models' own conventions — ₹ **crore**, natural
(positive) signs — so nothing is converted, which is what makes a hand-entered
file a trustworthy golden reference (and the way to create the real-company
golden the evaluation needs). See `data/golden/TEMPLATE.csv`. If reliable API
fundamentals are ever needed, **EOD Historical Data** is the recommended paid
option (better India coverage than FMP); the provider chain is built to slot it
in ahead of `yfinance` with one field map.

## The web app (Phase 5)

A FastAPI backend serves a browser UI, streams live pipeline progress, persists a
history of analyses to SQLite, and exposes the report as HTML/JSON/Markdown.

```bash
python -m app.cli serve
```

Then open `http://127.0.0.1:8000`. Enter a company, watch the pipeline light up
node-by-node over **server-sent events**, and read the rendered report inline
(with links to the full HTML, Markdown and JSON). Past analyses are listed and
re-openable without re-running. A live analysis needs the same keys as `analyze`
(OpenRouter, and optionally the data/news/research keys); the API surface itself
is fully unit-tested offline with a fake pipeline — no key or network to run the
suite.

Notable choices, in keeping with the project's small-dependency story: progress
streaming uses Starlette's `StreamingResponse` (no `sse-starlette`), history uses
the standard-library `sqlite3` (no ORM), and the UI is one self-contained page
served by FastAPI (no Streamlit).

## Evaluation harness (Phase 6)

Six tracks of evidence that the system works, not just a demo that ran once
(PRD §19), written to `docs/EVALUATION.md`:

```bash
python -m app.cli eval
```

| # | Track | What it proves |
|---|---|---|
| 1 | Computational accuracy | Engine matches golden closed-form values within 0.5% |
| 2 | Directional vs. consensus | Rating-class agreement (consensus is a reference, not truth) |
| 3 | Report quality | 10-criterion rubric scored by an independent LLM judge (`--judge`) |
| 4 | Determinism & variance | Composite is **bit-identical** across repeated runs |
| 5 | Groundedness audit | Every number in the narrative traces to the data; zero silent hallucinations |
| 6 | **Adversarial / injection** | An injected instruction cannot move the composite, inflate a bounded field, or slip a fabricated number past the verifier |

Track 6 is the differentiator: it turns the project's core claim — *the
recommendation is computed, not generated* — from a design assertion into a
passing test. Tracks 1, 4, 5 and 6 run fully offline; 2 and 3 provide their
scoring logic and run against live/dated inputs.

## Next step

The seven PRD phases are complete. Remaining is polish rather than new scope: a
**real-company golden file** (hand-entered from an annual report, to close the
Track 1 reality gap), optional **UI views** (score explorer, multi-company
comparison), and running the harness across the full **15-company evaluation
set** with dated analyst consensus.
