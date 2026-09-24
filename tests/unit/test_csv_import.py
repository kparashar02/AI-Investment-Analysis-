"""Manual CSV import (app/data/csv_import.py)."""

from __future__ import annotations

import json

import pytest

from app.config.settings import REPO_ROOT
from app.data.csv_import import (
    CsvImportError,
    load_manual_csv,
    parse_manual_csv,
    write_golden_json,
)
from app.data.fixtures import GoldenFile
from app.engine.registry import compute_metric_set

TEMPLATE = REPO_ROOT / "data" / "golden" / "TEMPLATE.csv"


def _write(tmp_path, text: str):
    path = tmp_path / "c.csv"
    path.write_text(text, encoding="utf-8")
    return path


# --- the shipped template compiles end to end ------------------------------

def test_template_compiles_to_statements():
    golden = load_manual_csv(TEMPLATE)
    stmts = golden.statements
    assert stmts.ticker == "ACME.NS"
    assert [p.label for p in stmts.annual] == ["FY2026", "FY2025", "FY2024", "FY2023", "FY2022"]
    assert stmts.latest.period_end == "2026-03-31"


def test_values_are_not_unit_converted():
    # Manual data is already in crore — it must pass through unchanged (unlike
    # the yfinance path, which divides absolute rupees by 1e7).
    latest = load_manual_csv(TEMPLATE).statements.latest
    assert latest.income.revenue == pytest.approx(10000.0)
    assert latest.income.pat == pytest.approx(1425.0)
    assert latest.income.shares_diluted == pytest.approx(100.0)


def test_natural_signs_preserved():
    latest = load_manual_csv(TEMPLATE).statements.latest
    assert latest.cashflow.capex == pytest.approx(700.0)      # positive magnitude as entered
    assert latest.cashflow.capex > 0


def test_market_block_parsed():
    market = load_manual_csv(TEMPLATE).market
    assert market is not None
    assert market.cmp == pytest.approx(150.0)
    assert market.shares_outstanding == pytest.approx(100.0)


def test_template_feeds_the_engine():
    golden = load_manual_csv(TEMPLATE)
    metric_set = compute_metric_set(golden.statements, golden.market)
    assert metric_set.value("roe") is not None       # ROE computes from imported data
    assert metric_set.value("pe_ratio") is not None   # needs the market block


# --- golden round trip -----------------------------------------------------

def test_round_trip_through_golden_json(tmp_path):
    payload = parse_manual_csv(TEMPLATE)
    out = write_golden_json(payload, tmp_path / "acme_manual.json")
    reloaded = GoldenFile(out, json.loads(out.read_text(encoding="utf-8")))
    assert reloaded.kind == "REAL"
    assert reloaded.statements.latest.income.revenue == pytest.approx(10000.0)


# --- validation ------------------------------------------------------------

def test_missing_revenue_is_rejected(tmp_path):
    csv = "section,field,FY2026\nincome,pat,100\n"
    with pytest.raises(CsvImportError, match="revenue"):
        parse_manual_csv(_write(tmp_path, csv))


def test_bad_header_is_rejected(tmp_path):
    with pytest.raises(CsvImportError, match="section"):
        parse_manual_csv(_write(tmp_path, "foo,bar,FY2026\nincome,revenue,100\n"))


def test_non_numeric_value_is_rejected(tmp_path):
    csv = "section,field,FY2026\nincome,revenue,not_a_number\nincome,pat,100\n"
    with pytest.raises(CsvImportError):
        parse_manual_csv(_write(tmp_path, csv))


def test_unknown_section_is_rejected(tmp_path):
    csv = "section,field,FY2026\nweird,revenue,100\n"
    with pytest.raises(CsvImportError, match="unknown section"):
        parse_manual_csv(_write(tmp_path, csv))


def test_comment_rows_are_ignored(tmp_path):
    csv = ("section,field,FY2026\n"
           "# this is a comment,,\n"
           "income,revenue,10000\nincome,pat,1425\n")
    payload = parse_manual_csv(_write(tmp_path, csv))
    assert payload["annual"][0]["income"]["revenue"] == 10000.0
