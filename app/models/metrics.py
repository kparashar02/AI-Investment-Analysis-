"""Computed-metric models.

Every number the system shows a user is wrapped in a :class:`MetricValue`
carrying its formula and the exact inputs used. That is not decoration — it
is how the report shows its working, and it is what lets a reader disagree
with a specific number instead of with a black box (PRD 8.5, 13.1 s5).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Unit(str, Enum):
    PCT = "PCT"                    # held as a percentage: 46.2 means 46.2%
    RATIO = "RATIO"                # 0.40 means 0.40x
    TIMES = "TIMES"                # 10.0 means 10.0x
    DAYS = "DAYS"
    CRORE = "CRORE"                # rupees crore
    RUPEES = "RUPEES"              # rupees per share
    SCORE = "SCORE"
    PP_PER_YEAR = "PP_PER_YEAR"    # percentage points per year

    @property
    def suffix(self) -> str:
        return {
            Unit.PCT: "%",
            Unit.RATIO: "x",
            Unit.TIMES: "x",
            Unit.DAYS: " days",
            Unit.CRORE: " Cr",
            Unit.RUPEES: "",
            Unit.SCORE: "",
            Unit.PP_PER_YEAR: " pp/yr",
        }[self]


class Pillar(str, Enum):
    FUNDAMENTALS = "FUNDAMENTALS"
    VALUATION = "VALUATION"
    GROWTH = "GROWTH"
    CASHFLOW = "CASHFLOW"
    INDUSTRY = "INDUSTRY"
    NEWS = "NEWS"
    RISK = "RISK"
    CONTEXT = "CONTEXT"            # computed and displayed, but not scored


class MetricValue(BaseModel):
    """One computed metric with its full audit trail."""

    name: str
    label: str
    value: float | None
    unit: Unit
    pillar: Pillar = Pillar.CONTEXT
    period: str | None = None

    formula: str = ""
    inputs_used: dict[str, float] = Field(default_factory=dict)

    band_score: float | None = None
    peer_percentile: float | None = None
    final_score: float | None = None

    unavailable_reason: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.value is not None

    def display(self, dp: int = 2) -> str:
        if self.value is None:
            return "n/a"
        return f"{self.value:,.{dp}f}{self.unit.suffix}"

    def working(self) -> str:
        """Human-readable derivation, e.g.::

            ROE 32.39% = PAT 1,425.0 / avg_equity 4,400.0

        Used by the report's hover tooltips (PRD UI2).
        """
        if self.value is None:
            return f"{self.label}: n/a ({self.unavailable_reason or 'no data'})"
        inputs = ", ".join(f"{k} {v:,.1f}" for k, v in self.inputs_used.items())
        base = f"{self.label} {self.display()}"
        if self.formula:
            base += f" = {self.formula}"
        return f"{base}  [{inputs}]" if inputs else base


class MetricSeries(BaseModel):
    """One metric tracked across periods, oldest-first, for trend charts."""

    name: str
    label: str
    unit: Unit
    periods: list[str]
    values: list[float | None]

    def as_pairs(self) -> list[tuple[str, float | None]]:
        return list(zip(self.periods, self.values, strict=True))


class ScreenResult(BaseModel):
    """Output of a composite screen (Piotroski, Altman, DuPont)."""

    name: str
    label: str
    score: float | None
    max_score: float | None = None
    interpretation: str = ""
    components: dict[str, float | bool | None] = Field(default_factory=dict)
    variant: str | None = None
    caveats: list[str] = Field(default_factory=list)


class MetricSet(BaseModel):
    """Everything the deterministic layer produced for one company.

    This object is the complete output of Phase 1 and the complete input to
    the Phase 2 scoring engine. Note that it contains no field an LLM can
    write — the schema itself enforces the separation of concerns (PRD 6).
    """

    ticker: str
    company_name: str
    sector: str | None = None
    sector_key: str = "general"
    period: str
    thresholds_version: str = ""

    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    series: dict[str, MetricSeries] = Field(default_factory=dict)
    screens: dict[str, ScreenResult] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def get(self, name: str) -> MetricValue | None:
        return self.metrics.get(name)

    def value(self, name: str) -> float | None:
        metric = self.metrics.get(name)
        return metric.value if metric else None

    def score(self, name: str) -> float | None:
        metric = self.metrics.get(name)
        return metric.final_score if metric else None

    def by_pillar(self, pillar: Pillar) -> dict[str, MetricValue]:
        return {k: v for k, v in self.metrics.items() if v.pillar is pillar}

    def available_count(self) -> tuple[int, int]:
        """``(available, total)`` — the headline data-completeness figure."""
        total = len(self.metrics)
        available = sum(1 for m in self.metrics.values() if m.available)
        return (available, total)

    def fingerprint(self) -> str:
        """Stable hash of every computed value.

        The determinism test (PRD 19.4 / NF4) asserts this is identical
        across repeated runs on identical inputs. If it ever differs, an LLM
        output has leaked into the arithmetic layer.
        """
        import hashlib

        parts = []
        for name in sorted(self.metrics):
            metric = self.metrics[name]
            parts.append(f"{name}={metric.value!r}:{metric.final_score!r}")
        for name in sorted(self.screens):
            parts.append(f"{name}={self.screens[name].score!r}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
