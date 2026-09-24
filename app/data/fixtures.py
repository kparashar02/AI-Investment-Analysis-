"""Loader for golden-value fixture files.

Golden files are the ground truth of Track 1 of the evaluation harness
(PRD 19.1). Two kinds live side by side in ``data/golden/``:

* ``kind: SYNTHETIC`` — fabricated companies whose figures were chosen so
  that every ratio has an exactly computable value. They test the arithmetic.
* ``kind: REAL`` — a real listed company's published statements, hand-entered
  from its annual report, with the expected ratio values computed manually in
  Excel and recorded in ``expected_metrics`` alongside a page reference.
  These test whether the engine agrees with a human analyst.

Only the second kind validates the system against reality, and it cannot be
generated — it has to be typed in from the filings. See ``TEMPLATE.json``.

The loader deliberately performs no cleaning or guessing. A golden file that
does not parse is a broken golden file, and it should fail loudly rather than
be silently repaired.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config.settings import REPO_ROOT
from app.models.statements import (
    AnnualPeriod,
    BalanceSheet,
    CashFlowStatement,
    FinancialStatements,
    IncomeStatement,
    MarketData,
    ReportingBasis,
)

GOLDEN_DIR = REPO_ROOT / "data" / "golden"


class GoldenFile:
    """A parsed golden fixture: statements, market data and expectations."""

    def __init__(self, path: Path, payload: dict[str, Any]):
        self.path = path
        self.payload = payload

    @property
    def meta(self) -> dict[str, Any]:
        return self.payload.get("_meta") or {}

    @property
    def kind(self) -> str:
        return str(self.meta.get("kind", "UNKNOWN"))

    @property
    def is_synthetic(self) -> bool:
        return self.kind.upper() == "SYNTHETIC"

    @property
    def expected_metrics(self) -> dict[str, dict[str, Any]]:
        """Manually verified expectations, if this file carries any.

        Keys beginning with ``_`` are notes/metadata, not metrics, and are
        skipped so they are never mistaken for a metric to validate."""
        block = self.payload.get("expected_metrics") or {}
        return {k: v for k, v in block.items() if not k.startswith("_")}

    @property
    def statements(self) -> FinancialStatements:
        company = self.payload.get("company") or {}
        periods = [_period(raw) for raw in self.payload.get("annual") or []]
        if not periods:
            raise ValueError(f"{self.path.name}: no annual periods")
        return FinancialStatements(
            ticker=company.get("ticker", self.path.stem.upper()),
            company_name=company.get("company_name", self.path.stem),
            sector=company.get("sector"),
            industry=company.get("industry"),
            fiscal_year_end=company.get("fiscal_year_end", "03-31"),
            annual=periods,
        )

    @property
    def market(self) -> MarketData | None:
        raw = self.payload.get("market")
        if not raw:
            return None
        return MarketData(**raw)


def _period(raw: dict[str, Any]) -> AnnualPeriod:
    return AnnualPeriod(
        label=raw["label"],
        period_end=raw["period_end"],
        basis=ReportingBasis(raw.get("basis", "UNKNOWN")),
        source=raw.get("source", "golden_file"),
        income=IncomeStatement(**raw["income"]),
        balance=BalanceSheet(**raw["balance"]),
        cashflow=CashFlowStatement(**raw["cashflow"]),
    )


def load_golden(name_or_path: str | Path) -> GoldenFile:
    """Load one golden file by stem name (``"acme_industries"``) or by path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = GOLDEN_DIR / f"{path.name}.json"
    if not path.is_absolute():
        candidate = GOLDEN_DIR / path.name
        path = candidate if candidate.exists() else path
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in GOLDEN_DIR.glob("*.json")))
        raise FileNotFoundError(f"golden file not found: {path}. Available: {available}")
    with path.open("r", encoding="utf-8") as handle:
        return GoldenFile(path, json.load(handle))


def list_golden(include_template: bool = False) -> list[Path]:
    """Every golden file on disk, sorted."""
    paths = sorted(GOLDEN_DIR.glob("*.json"))
    if not include_template:
        paths = [p for p in paths if p.stem.upper() != "TEMPLATE"]
    return paths
