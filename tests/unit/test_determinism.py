"""Determinism of the computation layer (PRD NF4, evaluation Track 4).

The system's central defensibility claim is that identical inputs produce an
identical score. For the deterministic layer that claim is absolute: not
"close", but bit-identical. If these tests ever fail, something
non-deterministic — a set iteration, a dict ordering, a floating-point
reduction whose order varies, or worst of all a language model output — has
leaked into the arithmetic.
"""

from __future__ import annotations

import pytest

from app.engine.registry import compute_metric_set, pillar_preview

pytestmark = pytest.mark.determinism

RUNS = 5


def test_fingerprint_is_stable_across_runs(acme):
    fingerprints = {
        compute_metric_set(acme.statements, acme.market).fingerprint()
        for _ in range(RUNS)
    }
    assert len(fingerprints) == 1, (
        f"{len(fingerprints)} distinct fingerprints across {RUNS} runs: {fingerprints}"
    )


def test_every_value_is_bit_identical(acme):
    """Equality, not approximate equality. ``==`` is the assertion on purpose."""
    first = compute_metric_set(acme.statements, acme.market)
    for _ in range(RUNS - 1):
        again = compute_metric_set(acme.statements, acme.market)
        for name, metric in first.metrics.items():
            other = again.metrics[name]
            assert metric.value == other.value, f"{name} value moved"
            assert metric.final_score == other.final_score, f"{name} score moved"
        for name, screen in first.screens.items():
            assert screen.score == again.screens[name].score, f"screen {name} moved"


def test_pillar_scores_are_bit_identical(acme):
    first = pillar_preview(compute_metric_set(acme.statements, acme.market))
    second = pillar_preview(compute_metric_set(acme.statements, acme.market))
    for pillar, detail in first.items():
        assert detail["score"] == second[pillar]["score"]
        assert detail["contributions"] == second[pillar]["contributions"]


def test_full_serialisation_is_identical(acme):
    """Round-tripping through JSON must not change anything either.

    The report and the API both serialise this object, so a difference here
    would mean the number a reader sees is not the number that was scored.
    """
    first = compute_metric_set(acme.statements, acme.market).model_dump_json()
    second = compute_metric_set(acme.statements, acme.market).model_dump_json()
    assert first == second


def test_fingerprint_distinguishes_different_companies(acme, levcyc):
    """A fingerprint that never changes would pass every test above vacuously."""
    a = compute_metric_set(acme.statements, acme.market).fingerprint()
    b = compute_metric_set(levcyc.statements, levcyc.market).fingerprint()
    assert a != b


def test_fingerprint_changes_when_an_input_changes(acme):
    """Sensitivity check: alter one figure and the fingerprint must move."""
    baseline = compute_metric_set(acme.statements, acme.market).fingerprint()

    mutated = acme.statements.model_copy(deep=True)
    mutated.annual[0].income.pat += 1.0
    changed = compute_metric_set(mutated, acme.market).fingerprint()

    assert baseline != changed


def test_metric_ordering_does_not_affect_the_fingerprint(acme):
    """The fingerprint sorts its keys, so dict insertion order is irrelevant.

    Without the sort, an unrelated refactor that reordered metric
    construction would appear to change every company's score.
    """
    metric_set = compute_metric_set(acme.statements, acme.market)
    shuffled = metric_set.model_copy(deep=True)
    shuffled.metrics = dict(reversed(list(metric_set.metrics.items())))
    assert shuffled.fingerprint() == metric_set.fingerprint()
