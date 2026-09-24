"""Command-line interface to the deterministic computation layer.

Runs with no API key, no network and no language model — which is the point.
The Phase 1 exit gate (PRD 22, Phase 1) is that a complete and accurate ratio
sheet can be produced for a company from normalised statements alone, and
this is how that is demonstrated.

    python -m app.cli list
    python -m app.cli sheet acme_industries
    python -m app.cli sheet leveraged_cyclicals --pillars
    python -m app.cli json acme_industries
    python -m app.cli fingerprint acme_industries --runs 5

The ``fetch`` command is the exception: it reaches a live data provider
(yfinance) and therefore needs the network and the yfinance package. It exists
to demonstrate the Phase 1b data layer end to end — retrieve, normalise, score
a real ticker — and prints a data-quality summary before the sheet.

    python -m app.cli fetch TCS.NS --sheet
"""

from __future__ import annotations

import argparse
import sys

from app.data.fixtures import list_golden, load_golden
from app.engine.registry import compute_metric_set, pillar_preview
from app.models.metrics import MetricSet, Pillar

WIDTH = 92

PILLAR_ORDER = [
    (Pillar.FUNDAMENTALS, "FUNDAMENTALS"),
    (Pillar.GROWTH, "GROWTH"),
    (Pillar.CASHFLOW, "CASH FLOW & EARNINGS QUALITY"),
    (Pillar.VALUATION, "VALUATION"),
    (Pillar.RISK, "RISK"),
    (Pillar.CONTEXT, "CONTEXT (computed, not scored)"),
]


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _header(metric_set: MetricSet) -> str:
    lines = [
        _rule("="),
        f"{metric_set.company_name}  [{metric_set.ticker}]",
        f"{metric_set.sector or 'sector unknown'}"
        f"  ->  threshold curve: '{metric_set.sector_key}'",
        f"Period: {metric_set.period}"
        f"    thresholds.yaml v{metric_set.thresholds_version}",
        _rule("="),
    ]
    return "\n".join(lines)


def _metric_row(name: str, metric) -> str:
    value = metric.display()
    score = "  -  " if metric.final_score is None else f"{metric.final_score:5.1f}"
    flag = "" if metric.available else "  (n/a)"
    return f"  {metric.label[:44]:<44} {value:>16}   score {score}{flag}"


def print_sheet(metric_set: MetricSet, show_pillars: bool, show_working: bool) -> None:
    print(_header(metric_set))

    for pillar, title in PILLAR_ORDER:
        group = metric_set.by_pillar(pillar)
        if not group:
            continue
        print(f"\n{title}")
        print(_rule())
        for name in sorted(group):
            metric = group[name]
            print(_metric_row(name, metric))
            if show_working and metric.available:
                print(f"        {metric.working()}")
            for note in metric.notes:
                print(f"        note: {note}")
            if not metric.available and metric.unavailable_reason:
                print(f"        reason: {metric.unavailable_reason}")

    print("\nCOMPOSITE SCREENS")
    print(_rule())
    for name in sorted(metric_set.screens):
        screen = metric_set.screens[name]
        value = "n/a" if screen.score is None else f"{screen.score:.2f}"
        if screen.max_score:
            value += f" / {screen.max_score:.0f}"
        print(f"  {screen.label[:44]:<44} {value:>16}")
        print(f"        {screen.interpretation}")
        for caveat in screen.caveats:
            print(f"        caveat: {caveat}")

    if show_pillars:
        print("\nPILLAR PREVIEW  (Phase 1 pillars only — NOT a composite score)")
        print(_rule())
        for pillar, detail in pillar_preview(metric_set).items():
            score = detail["score"]
            shown = "n/a" if score is None else f"{score:5.1f} / 100"
            print(f"  {pillar:<20} {shown:>14}   pillar weight {detail['weight']:.0%}")
            for component, contribution in sorted(
                detail["contributions"].items(), key=lambda kv: -kv[1]
            ):
                print(f"        {component:<38} contributes {contribution:6.2f}")
            for note in detail["notes"]:
                print(f"        note: {note}")
        print(
            "\n  The composite score and the BUY/HOLD/SELL rating are NOT produced\n"
            "  here. Valuation needs the Phase 2 DCF; industry, news and risk need\n"
            "  the Phase 3 agents. A composite built on three of seven pillars would\n"
            "  be a different model wearing the same name."
        )

    available, total = metric_set.available_count()
    print(f"\nDATA: {available}/{total} metrics computed "
          f"({available / total * 100:.0f}% of the metric library)")
    print(f"FINGERPRINT: {metric_set.fingerprint()}  (see PRD 19.4, determinism)")

    if metric_set.warnings:
        print("\nWARNINGS")
        print(_rule())
        for warning in metric_set.warnings:
            print(f"  ! {warning}")
    print()


def print_decision(decision, show_valuation: bool = True) -> None:
    """Full score decomposition and rating (PRD 12.8 demonstrable artefact)."""
    from app.models.decision import Rating  # noqa: PLC0415

    print(_rule("="))
    print(f"{decision.company_name}  [{decision.ticker}]")
    print(f"Sector curve: '{decision.sector_key}'    Period: {decision.period}")
    print(_rule("="))

    print("\nPILLAR SCORES")
    print(_rule())
    for pillar in decision.pillar_scores:
        if pillar.score is None:
            shown = "   n/a"
            contrib = ""
        else:
            label = "(inverted) " if pillar.name == "risk" else ""
            shown = f"{pillar.score:6.1f}"
            contrib = (f"  x {pillar.effective_weight:.3f} = {pillar.contribution:6.2f} {label}"
                       if pillar.contribution is not None else "")
        print(f"  {pillar.name:<14} {shown} / 100   (weight {pillar.configured_weight:.0%}){contrib}")
        for note in pillar.notes:
            print(f"        note: {note}")

    print(_rule())
    if decision.composite_score is not None:
        print(f"  COMPOSITE (renormalised over scored pillars)          {decision.composite_score:6.2f} / 100")
        print(f"  Weight covered: {decision.weight_covered:.0%}   Basis: {decision.basis.value}")
    else:
        print("  COMPOSITE: not computed")

    if show_valuation and decision.valuation is not None:
        v = decision.valuation
        print("\nVALUATION")
        print(_rule())
        d = v.dcf
        if d.available:
            print(f"  DCF fair value    : ₹{d.fair_value_low:.1f} – ₹{d.fair_value_high:.1f} "
                  f"(base ₹{d.fair_value_base:.1f})   [{d.confidence}]")
            print(f"  WACC {d.wacc_pct:.2f}%  (Re {d.cost_of_equity_pct:.2f}%, Rd {d.cost_of_debt_pct:.2f}%, "
                  f"β {d.beta_used:.2f}, tax {d.tax_rate*100:.1f}%)   terminal g {d.terminal_growth_pct:.2f}%")
            for flag in d.flags:
                print(f"        flag: {flag}")
        else:
            print(f"  DCF unavailable: {d.unavailable_reason}")
        if v.reconciled_low is not None:
            print(f"  Reconciled FV     : ₹{v.reconciled_low:.1f} – ₹{v.reconciled_high:.1f}   "
                  f"(midpoint ₹{v.fair_value_midpoint:.1f})")
        if v.current_price is not None and v.upside_pct is not None:
            print(f"  CMP ₹{v.current_price:.1f}   Upside/(Downside): {v.upside_pct:+.1f}%")
        for note in v.notes:
            print(f"        note: {note}")

    fired = [x for x in decision.vetoes if x.triggered]
    print("\nVETOES")
    print(_rule())
    if fired:
        for veto in fired:
            vals = ", ".join(f"{k}={val}" for k, val in veto.values.items())
            print(f"  {veto.code} TRIGGERED [{veto.action}]: {veto.description}")
            if vals:
                print(f"        {vals}")
    else:
        print("  none triggered")
    not_eval = [x.code for x in decision.vetoes if not x.evaluable]
    if not_eval:
        print(f"  (not evaluable without later phases: {', '.join(not_eval)})")

    print("\n" + _rule("="))
    verdict = decision.rating.value
    if decision.rating is Rating.NO_RATING:
        verdict = "NO RATING"
    elif decision.rating is Rating.NOT_SUPPORTED:
        verdict = "NOT SUPPORTED IN V1"
    print(f"  RATING: {verdict}    Confidence: {decision.confidence.value}")
    if decision.applied_vetoes:
        print(f"  (rating shaped by veto(s): {', '.join(decision.applied_vetoes)})")
    print(_rule("="))
    print(f"  config: weights v{decision.weights_version}, thresholds v{decision.thresholds_version}, "
          f"valuation v{decision.valuation_version}")
    print(f"  FINGERPRINT: {decision.fingerprint()}")
    if decision.warnings:
        print("\n  WARNINGS")
        for warning in decision.warnings:
            print(f"    ! {warning}")
    print()


def cmd_score(args: argparse.Namespace) -> int:
    """Full deterministic decision for a golden fixture (PRD Phase 2 artefact)."""
    from app.engine.scoring import build_decision  # noqa: PLC0415

    golden = load_golden(args.company)
    metric_set = compute_metric_set(golden.statements, golden.market)
    decision = build_decision(metric_set, golden.statements, golden.market)
    if golden.is_synthetic:
        print("\n*** SYNTHETIC TEST COMPANY — these are not real market figures. ***")
    print_decision(decision)
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    paths = list_golden()
    if not paths:
        print("No golden files found in data/golden/.")
        return 1
    print("Golden fixtures:\n")
    for path in paths:
        golden = load_golden(path)
        statements = golden.statements
        print(f"  {path.stem:<24} {statements.company_name}")
        print(f"  {'':<24} {golden.kind}, {len(statements.annual)} annual periods, "
              f"sector: {statements.sector}")
    print(
        "\nSYNTHETIC fixtures test the arithmetic. Validating the engine against\n"
        "reality needs a REAL fixture hand-entered from an annual report:\n"
        "see data/golden/TEMPLATE.json."
    )
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    golden = load_golden(args.company)
    metric_set = compute_metric_set(golden.statements, golden.market)
    if golden.is_synthetic:
        print("\n*** SYNTHETIC TEST COMPANY — these are not real market figures. ***")
    print_sheet(metric_set, show_pillars=args.pillars, show_working=args.working)
    return 0


def cmd_json(args: argparse.Namespace) -> int:
    golden = load_golden(args.company)
    metric_set = compute_metric_set(golden.statements, golden.market)
    print(metric_set.model_dump_json(indent=2))
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """Recompute N times and confirm the fingerprint never moves.

    This is the determinism check of PRD NF4 in its simplest form. If two
    runs over identical inputs ever disagree, something non-deterministic has
    entered the arithmetic layer.
    """
    golden = load_golden(args.company)
    fingerprints = []
    for _ in range(args.runs):
        metric_set = compute_metric_set(golden.statements, golden.market)
        fingerprints.append(metric_set.fingerprint())

    unique = set(fingerprints)
    print(f"{args.runs} runs of {golden.statements.company_name}")
    for i, fingerprint in enumerate(fingerprints, 1):
        print(f"  run {i}: {fingerprint}")
    if len(unique) == 1:
        print("\nPASS — identical across all runs. The computation layer is deterministic.")
        return 0
    print(f"\nFAIL — {len(unique)} distinct fingerprints. Non-determinism has entered "
          f"the arithmetic layer.")
    return 1


def cmd_fetch(args: argparse.Namespace) -> int:
    """Retrieve a live ticker through the data layer, then optionally score it.

    This is the only command that touches the network. It imports the data
    service lazily so that every other command — and the whole test suite —
    stays free of the provider dependencies.
    """
    from app.data.service import DataService, DataUnavailable  # noqa: PLC0415

    service = DataService(use_cache=not args.no_cache)
    try:
        statements = service.get_statements(args.ticker)
    except DataUnavailable as exc:
        print(f"FETCH FAILED: {exc}", file=sys.stderr)
        return 1

    dq = statements.data_quality
    print(_rule("="))
    print(f"{statements.company_name}  [{statements.ticker}]")
    print(f"{statements.sector or 'sector unknown'} / {statements.industry or 'industry unknown'}")
    print(_rule("="))
    if dq is not None:
        print(f"Completeness       : {dq.completeness_pct:.0f}%")
        print(f"Annual periods     : {dq.annual_periods_available}")
        print(f"Quarterly periods  : {dq.quarterly_periods_available}")
        print(f"Sources            : {', '.join(dq.sources_used) or 'none'}")
        print(f"Sufficient to rate : {'yes' if dq.sufficient_to_rate else 'NO (veto V1)'}")
        if dq.missing_fields:
            print(f"Missing core fields: {', '.join(dq.missing_fields)}")
        if dq.balance_check_failures:
            print(f"Balance failures   : {', '.join(dq.balance_check_failures)}")
        for warning in dq.warnings:
            print(f"  ! {warning}")

    if args.sheet or args.score:
        market = service.get_market_or_none(args.ticker)
        metric_set = compute_metric_set(statements, market)
        if args.sheet:
            print()
            print_sheet(metric_set, show_pillars=args.pillars, show_working=False)
        if args.score:
            from app.engine.scoring import build_decision  # noqa: PLC0415

            print()
            print_decision(build_decision(metric_set, statements, market))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Run the full Phase 3 agent pipeline on a live company.

    Needs the network, yfinance, and an OPENROUTER_API_KEY (a free model works
    for development). Imports the agent layer lazily so the rest of the CLI and
    the test suite stay independent of it.
    """
    from app.agents.llm import OpenRouterLLM  # noqa: PLC0415
    from app.agents.orchestrator import AnalysisPipeline  # noqa: PLC0415
    from app.data.news_service import NewsService  # noqa: PLC0415
    from app.data.service import DataService  # noqa: PLC0415
    from app.data.web_research import WebResearchService  # noqa: PLC0415

    llm = OpenRouterLLM()
    if not llm.available():
        print("ANALYZE FAILED: OPENROUTER_API_KEY is not set. Add it to .env "
              "(see .env.example) and pick a free OPENROUTER_MODEL.", file=sys.stderr)
        return 1

    news_service = NewsService(use_cache=not args.no_cache)
    if not any(p.available() for p in news_service.providers):
        print("  (no ALPHA_VANTAGE_API_KEY or NEWSAPI_KEY set — the news pillar "
              "will score neutral)", file=sys.stderr)
    web_research = WebResearchService(use_cache=not args.no_cache)
    if not web_research.backend.available():
        print("  (no BRAVE_API_KEY set — the industry agent runs without live "
              "web research/citations)", file=sys.stderr)
    print("", file=sys.stderr)
    pipeline = AnalysisPipeline(
        llm, DataService(use_cache=not args.no_cache),
        news_source=news_service.news_source, web_research=web_research)
    print(f"Running the analysis pipeline for {args.query!r} "
          f"(model: {llm.model})...\n")
    state = pipeline.run(args.query)

    trace = state.get("trace") or []
    print("PIPELINE: " + " -> ".join(trace))
    for err in state.get("errors") or []:
        print(f"  ! {err}")
    print()

    decision = state.get("decision")
    if decision is None:
        print("ANALYZE FAILED: pipeline produced no decision.", file=sys.stderr)
        return 1
    print_decision(decision)

    report = state.get("report")
    if report is not None:
        from pathlib import Path  # noqa: PLC0415
        from app.report import PdfUnavailable, html_to_pdf, render_html, render_markdown  # noqa: PLC0415

        stem = decision.ticker.replace("/", "_")
        md_path = Path.cwd() / f"{stem}_report.md"
        html_path = Path.cwd() / f"{stem}_report.html"
        md_path.write_text(render_markdown(report), encoding="utf-8")
        html = render_html(report, metric_set=state.get("metric_set"),
                           market=state.get("market"), news=state.get("news"))
        html_path.write_text(html, encoding="utf-8")

        v = report.verification
        if v is not None:
            print(f"\nVERIFICATION: {'PASSED' if v.passed else 'FAILED'} — "
                  f"{v.numeric_claims_checked} numeric claims checked, "
                  f"{len(v.unsupported_numbers)} ungrounded"
                  + (" (unverified-claims banner set)" if report.unverified_banner else ""))
        print(f"REPORT: {md_path}\n        {html_path}")

        if args.pdf:
            pdf_path = Path.cwd() / f"{stem}_report.pdf"
            try:
                html_to_pdf(html, pdf_path)
                print(f"        {pdf_path}")
            except PdfUnavailable as exc:
                print(f"        PDF not generated — {exc}", file=sys.stderr)
    return 0


def cmd_import_csv(args: argparse.Namespace) -> int:
    """Compile a hand-entered CSV into statements; optionally save + score it."""
    from pathlib import Path  # noqa: PLC0415

    from app.config.settings import REPO_ROOT  # noqa: PLC0415
    from app.data.csv_import import CsvImportError, parse_manual_csv, write_golden_json  # noqa: PLC0415
    from app.data.fixtures import GoldenFile  # noqa: PLC0415
    from app.data.normalise import build_data_quality  # noqa: PLC0415

    try:
        payload = parse_manual_csv(args.csv)
        golden = GoldenFile(Path(args.csv), payload)
        statements = golden.statements
    except (CsvImportError, ValueError) as exc:
        print(f"IMPORT FAILED: {exc}", file=sys.stderr)
        return 1

    market = golden.market
    dq = build_data_quality(statements, sources_used=["manual_csv"])
    print(_rule("="))
    print(f"{statements.company_name}  [{statements.ticker}]")
    print(f"{statements.sector or 'sector unknown'} / {statements.industry or 'industry unknown'}")
    print(_rule("="))
    print(f"Annual periods    : {len(statements.annual)} ({', '.join(p.label for p in statements.annual)})")
    print(f"Completeness      : {dq.completeness_pct:.0f}%")
    print(f"Market data       : {'yes (CMP ' + format(market.cmp, '.1f') + ')' if market else 'none'}")
    print(f"Sufficient to rate: {'yes' if dq.sufficient_to_rate else 'NO (veto V1)'}")
    for period in statements.annual:
        passed, dev = period.balance.balance_check()
        if not passed and dev is not None:
            print(f"  ! {period.label} balance sheet off by {dev:.2f}% of total assets")

    if args.save:
        out = REPO_ROOT / "data" / "golden" / f"{args.save}.json"
        write_golden_json(payload, out)
        print(f"\nSaved golden file: {out}")

    if args.sheet or args.score:
        metric_set = compute_metric_set(statements, market)
        if args.sheet:
            print()
            print_sheet(metric_set, show_pillars=args.pillars, show_working=False)
        if args.score:
            from app.engine.scoring import build_decision  # noqa: PLC0415
            print()
            print_decision(build_decision(metric_set, statements, market))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the FastAPI web app (browser UI + API). Needs the analysis stack."""
    import uvicorn  # noqa: PLC0415

    from app.api.app import create_app  # noqa: PLC0415
    from app.api.service import build_production_service  # noqa: PLC0415

    service = build_production_service(use_cache=not args.no_cache)
    if not service.pipeline.llm.available():
        print("  (OPENROUTER_API_KEY not set — analyses will fail until it is)", file=sys.stderr)
    print(f"Serving on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Run the six-track evaluation harness and write docs/EVALUATION.md."""
    from app.eval.harness import run_evaluation, write_evaluation_md  # noqa: PLC0415

    judge = None
    if args.judge:
        from app.agents.llm import OpenRouterLLM  # noqa: PLC0415
        from app.eval.rubric import ReportJudgeAgent  # noqa: PLC0415
        llm = OpenRouterLLM()
        if llm.available():
            judge = ReportJudgeAgent(llm)
        else:
            print("  (--judge ignored: OPENROUTER_API_KEY not set)", file=sys.stderr)

    report = run_evaluation(judge=judge)
    print(_rule("="))
    print("EVALUATION HARNESS (PRD §19)")
    print(_rule("="))
    for t in report.tracks:
        print(f"  Track {t.track}  [{t.status:^7}]  {t.name}")
        print(f"            {t.summary}")
        for detail in t.details:
            print(f"              - {detail}")
    print(_rule("="))
    print(f"  OVERALL: {'PASS' if report.passed else 'FAIL'} "
          "(PARTIAL/SKIPPED are informational, not failures)")
    out = write_evaluation_md(report)
    print(f"  Written: {out}")
    return 0 if report.passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Deterministic equity computation layer. No API key, no network, "
            "no language model."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="list available golden fixtures")
    p_list.set_defaults(func=cmd_list)

    p_sheet = subparsers.add_parser("sheet", help="print the full ratio sheet")
    p_sheet.add_argument("company", help="golden fixture stem, e.g. acme_industries")
    p_sheet.add_argument("--pillars", action="store_true",
                         help="show the Phase 1 pillar score preview")
    p_sheet.add_argument("--working", action="store_true",
                         help="show each metric's formula and inputs")
    p_sheet.set_defaults(func=cmd_sheet)

    p_json = subparsers.add_parser("json", help="dump the MetricSet as JSON")
    p_json.add_argument("company")
    p_json.set_defaults(func=cmd_json)

    p_fp = subparsers.add_parser("fingerprint", help="determinism check")
    p_fp.add_argument("company")
    p_fp.add_argument("--runs", type=int, default=5)
    p_fp.set_defaults(func=cmd_fingerprint)

    p_score = subparsers.add_parser(
        "score", help="full deterministic decision — composite, valuation, vetoes, rating"
    )
    p_score.add_argument("company", help="golden fixture stem, e.g. acme_industries")
    p_score.set_defaults(func=cmd_score)

    p_fetch = subparsers.add_parser(
        "fetch", help="retrieve a live ticker via the data layer (needs network + yfinance)"
    )
    p_fetch.add_argument("ticker", help="listing symbol, e.g. TCS.NS or INFY.NS")
    p_fetch.add_argument("--sheet", action="store_true",
                         help="also compute and print the ratio sheet")
    p_fetch.add_argument("--score", action="store_true",
                         help="also compute and print the full decision (composite, valuation, rating)")
    p_fetch.add_argument("--pillars", action="store_true",
                         help="with --sheet, show the pillar preview")
    p_fetch.add_argument("--no-cache", action="store_true",
                         help="bypass the disk cache and force a fresh fetch")
    p_fetch.set_defaults(func=cmd_fetch)

    p_analyze = subparsers.add_parser(
        "analyze", help="full agent pipeline on a live company (needs network + OPENROUTER_API_KEY)"
    )
    p_analyze.add_argument("query", help="company name or ticker, e.g. 'TCS' or 'INFY.NS'")
    p_analyze.add_argument("--no-cache", action="store_true",
                           help="bypass the disk cache and force a fresh fetch")
    p_analyze.add_argument("--pdf", action="store_true",
                           help="also render a PDF (needs WeasyPrint + native libs; falls back to HTML)")
    p_analyze.set_defaults(func=cmd_analyze)

    p_import = subparsers.add_parser(
        "import-csv", help="compile a hand-entered CSV into statements (see data/golden/TEMPLATE.csv)"
    )
    p_import.add_argument("csv", help="path to the manual CSV")
    p_import.add_argument("--save", metavar="NAME",
                          help="also save as data/golden/NAME.json (a reusable golden file)")
    p_import.add_argument("--sheet", action="store_true", help="print the ratio sheet")
    p_import.add_argument("--score", action="store_true", help="print the full decision")
    p_import.add_argument("--pillars", action="store_true", help="with --sheet, show the pillar preview")
    p_import.set_defaults(func=cmd_import_csv)

    p_serve = subparsers.add_parser(
        "serve", help="launch the web app (browser UI + API); needs the analysis stack"
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--no-cache", action="store_true", help="bypass the disk cache")
    p_serve.set_defaults(func=cmd_serve)

    p_eval = subparsers.add_parser(
        "eval", help="run the six-track evaluation harness and write docs/EVALUATION.md"
    )
    p_eval.add_argument("--judge", action="store_true",
                        help="also run Track 3 (report quality) with a live LLM judge")
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    from app.config.settings import load_env  # noqa: PLC0415

    load_env()   # pick up .env (OpenRouter key, model, etc.) before anything runs
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
