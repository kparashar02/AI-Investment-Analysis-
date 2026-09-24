"""Manual CSV import — the reliable, offline data path (PRD 9.1 fallback 2).

Free live providers can rate-limit, break, or simply not cover a given Indian
ticker. For the evaluation set and for a demo that must not fail, a company's
five years of statements are hand-entered once from its annual report into a
small CSV; this module compiles that CSV into the project's existing golden-file
schema, so it flows through the *same* loader, engine, decision and report as
everything else — and it is exactly how a **real** golden file (the currently
skipped Phase 0 validation) is created.

The CSV is deliberately in the same units and conventions the models use — rupees
**crore**, natural (positive) signs for costs and capex — because that is how an
Indian annual report already presents the numbers. Nothing is unit-converted
here; what you type is what the engine sees, which is what makes a golden file a
trustworthy reference.

CSV format (wide; a template lives at ``data/golden/TEMPLATE.csv``)::

    section,field,FY2024,FY2025,FY2026
    company,ticker,ACME.NS,,
    company,company_name,Acme Industries,,
    company,sector,Capital Goods,,
    income,revenue,7000,8000,10000
    income,pat,800,975,1425
    balance,total_assets,7070,7600,9000
    cashflow,operating_cash_flow,1100,1300,1900
    market,cmp,,,150

``company``/``market`` rows carry a single value (first non-empty cell);
statement rows carry one value per fiscal-year column.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.data.fixtures import GoldenFile

_STATEMENT_SECTIONS = {"income", "balance", "cashflow"}
_COMPANY_FIELDS = {"ticker", "company_name", "sector", "industry", "fiscal_year_end", "basis"}
_MARKET_FIELDS = {"cmp", "shares_outstanding", "beta", "market_cap", "week_52_high", "week_52_low"}
# Required on every period, because a statement with no top line or bottom line
# is not a statement (the models enforce this too, but we want a clear message).
_REQUIRED_PER_PERIOD = ("revenue", "pat")


class CsvImportError(ValueError):
    """A manual CSV that cannot be compiled into statements."""


def _num(cell: str) -> float | None:
    cell = (cell or "").strip().replace(",", "")
    if cell == "":
        return None
    try:
        return float(cell)
    except ValueError as exc:
        raise CsvImportError(f"'{cell}' is not a number") from exc


def _first_nonempty(cells: list[str]) -> str | None:
    for c in cells:
        if (c or "").strip():
            return c.strip()
    return None


def _period_end(label: str, fiscal_year_end: str) -> str:
    digits = "".join(ch for ch in label if ch.isdigit())[:4]
    if len(digits) != 4:
        raise CsvImportError(f"period label {label!r} has no 4-digit year")
    return f"{digits}-{fiscal_year_end}"


def parse_manual_csv(path: str | Path) -> dict[str, Any]:
    """Compile a manual CSV into a golden-file payload dict (canonical, crore)."""
    rows = list(csv.reader(Path(path).open("r", encoding="utf-8-sig", newline="")))
    rows = [r for r in rows if any((c or "").strip() for c in r)]   # drop blank lines
    if len(rows) < 2:
        raise CsvImportError("CSV has no header and/or no data rows")

    header = rows[0]
    if len(header) < 3 or header[0].strip().lower() != "section" or header[1].strip().lower() != "field":
        raise CsvImportError("first row must be 'section,field,<FY labels...>'")
    period_labels = [c.strip() for c in header[2:] if c.strip()]
    if not period_labels:
        raise CsvImportError("no fiscal-year columns in the header")

    company: dict[str, Any] = {}
    market: dict[str, Any] = {}
    per_period: dict[str, dict[str, dict[str, float]]] = {
        label: {"income": {}, "balance": {}, "cashflow": {}} for label in period_labels
    }

    for row in rows[1:]:
        section = (row[0] if len(row) > 0 else "").strip().lower()
        field = (row[1] if len(row) > 1 else "").strip()
        values = list(row[2:]) + [""] * (len(period_labels) - len(row[2:]))
        if not section or section.startswith("#"):
            continue    # blank or comment row
        if not field:
            continue
        if section == "company":
            if field in _COMPANY_FIELDS:
                company[field] = _first_nonempty(values)
        elif section == "market":
            if field in _MARKET_FIELDS:
                cell = _first_nonempty(values)
                market[field] = _num(cell) if cell is not None else None
        elif section in _STATEMENT_SECTIONS:
            for i, label in enumerate(period_labels):
                num = _num(values[i])
                if num is not None:
                    per_period[label][section][field] = num
        else:
            raise CsvImportError(f"unknown section {section!r} (row: {row})")

    fiscal_year_end = company.get("fiscal_year_end") or "03-31"
    basis = (company.get("basis") or "CONSOLIDATED").upper()

    annual: list[dict[str, Any]] = []
    for label in period_labels:
        statements = per_period[label]
        for req in _REQUIRED_PER_PERIOD:
            if req not in statements["income"]:
                raise CsvImportError(f"period {label} is missing required income.{req}")
        annual.append({
            "label": label, "period_end": _period_end(label, fiscal_year_end),
            "basis": basis, "source": "manual_csv",
            "income": statements["income"], "balance": statements["balance"],
            "cashflow": statements["cashflow"],
        })

    payload: dict[str, Any] = {
        "_meta": {"kind": "REAL", "source": "manual_csv",
                  "warning": "Hand-entered from an annual report; verify against the filing."},
        "company": {k: company.get(k) for k in ("ticker", "company_name", "sector", "industry")
                    if company.get(k)} | {"fiscal_year_end": fiscal_year_end},
        "annual": annual,
    }
    if market.get("cmp") is not None:
        market_block = {"ticker": company.get("ticker", ""), "source": "manual_csv",
                        **{k: v for k, v in market.items() if v is not None}}
        payload["market"] = market_block
    return payload


def load_manual_csv(path: str | Path) -> GoldenFile:
    """Parse a manual CSV and return it as a :class:`GoldenFile` — so
    ``.statements`` and ``.market`` work exactly as for a golden fixture."""
    return GoldenFile(Path(path), parse_manual_csv(path))


def write_golden_json(payload: dict[str, Any], out_path: str | Path) -> Path:
    """Persist a compiled payload as a golden JSON file (the canonical schema)."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path
