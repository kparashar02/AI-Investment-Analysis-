"""Evaluation result models (PRD 19)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TrackResult(BaseModel):
    """The outcome of one evaluation track."""

    track: int
    name: str
    status: str                       # PASS | FAIL | PARTIAL | SKIPPED
    summary: str
    metrics: dict[str, float] = Field(default_factory=dict)
    details: list[str] = Field(default_factory=list)


class EvaluationReport(BaseModel):
    generated_at: str
    config_versions: dict[str, str] = Field(default_factory=dict)
    tracks: list[TrackResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when no track FAILED (PARTIAL/SKIPPED are not failures)."""
        return all(t.status != "FAIL" for t in self.tracks)


class RubricCriterion(BaseModel):
    key: str
    label: str
    weight: float


class RubricScore(BaseModel):
    key: str
    score: int = Field(ge=0, le=100)
    comment: str = ""
