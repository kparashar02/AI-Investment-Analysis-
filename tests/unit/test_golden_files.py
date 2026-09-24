"""Generic validator for golden files carrying manual expectations.

This is the harness for Track 1 of the evaluation framework (PRD 19.1). Any
golden file that declares an ``expected_metrics`` block is checked against
it, with the tolerance the file itself specifies.

**The synthetic fixtures test the arithmetic. Only a REAL golden file tests
the system against reality**, and it cannot be generated — the figures have
to be entered by hand from a published annual report and the expected ratios
computed independently in Excel. See ``data/golden/TEMPLATE.json``. Until at
least one real file exists, the parametrised test below reports as skipped,
which is the honest signal: the arithmetic is verified, the agreement with a
human analyst reading primary sources is not yet.
"""

from __future__ import annotations

import pytest

from app.data.fixtures import list_golden, load_golden
from app.engine.registry import compute_metric_set

pytestmark = pytest.mark.golden


def _files_with_expectations():
    out = []
    for path in list_golden():
        golden = load_golden(path)
        if golden.expected_metrics:
            out.append(path)
    return out


FILES_WITH_EXPECTATIONS = _files_with_expectations()


def test_every_golden_file_parses_and_is_computable():
    """A golden file that does not load is a broken golden file."""
    paths = list_golden()
    assert paths, "no golden files found in data/golden/"
    for path in paths:
        golden = load_golden(path)
        metric_set = compute_metric_set(golden.statements, golden.market)
        available, total = metric_set.available_count()
        assert available > 0, f"{path.name}: no metrics could be computed"
        assert total > 30, (
            f"{path.name}: only {total} metrics in the library — the PRD "
            f"specifies at least 35 (objective O2)"
        )


def test_every_golden_file_declares_whether_it_is_synthetic():
    """A fabricated company must never be mistaken for market data."""
    for path in list_golden():
        golden = load_golden(path)
        assert golden.kind in {"SYNTHETIC", "REAL"}, (
            f"{path.name}: _meta.kind must be SYNTHETIC or REAL, got {golden.kind!r}"
        )
        if golden.is_synthetic:
            assert "NOT" in golden.meta.get("warning", "").upper(), (
                f"{path.name}: synthetic fixtures must carry an explicit warning"
            )
            assert "SYNTHETIC" in golden.statements.company_name.upper(), (
                f"{path.name}: put SYNTHETIC in the company name so it cannot be "
                f"quoted out of context"
            )


@pytest.mark.skipif(
    not FILES_WITH_EXPECTATIONS,
    reason=(
        "No golden file carries an expected_metrics block yet. Real-company "
        "golden values must be hand-entered from an annual report — see "
        "data/golden/TEMPLATE.json. This is the PRD Phase 1 exit gate."
    ),
)
@pytest.mark.parametrize(
    "path", FILES_WITH_EXPECTATIONS, ids=lambda p: p.stem
)
def test_computed_values_match_manual_expectations(path):
    """Engine output versus a human's independent Excel calculation.

    A deviation is informative either way. The usual causes, in order of
    frequency: consolidated vs standalone mismatch, a fiscal-year labelling
    difference, an average-vs-closing balance-sheet convention, or a genuine
    engine bug. Investigate before assuming the engine is wrong, and record
    the finding in docs/EVALUATION.md.
    """
    golden = load_golden(path)
    metric_set = compute_metric_set(golden.statements, golden.market)

    failures: list[str] = []
    for name, expectation in golden.expected_metrics.items():
        expected = expectation.get("value")
        tolerance = float(expectation.get("tolerance_pct", 0.5))

        metric = metric_set.get(name)
        if metric is None:
            failures.append(f"{name}: not computed by the engine at all")
            continue
        if metric.value is None:
            failures.append(
                f"{name}: engine reports unavailable ({metric.unavailable_reason})"
            )
            continue
        if expected is None:
            failures.append(f"{name}: expectation has no value; fill in the template")
            continue

        if expected == 0:
            ok = abs(metric.value) <= 1e-9
            deviation = abs(metric.value)
        else:
            deviation = abs(metric.value - expected) / abs(expected) * 100.0
            ok = deviation <= tolerance

        if not ok:
            failures.append(
                f"{name}: engine {metric.value:,.4f} vs manual {expected:,.4f} "
                f"({deviation:.3f}% deviation, tolerance {tolerance}%). "
                f"Manual basis: {expectation.get('computed_from', 'not stated')}. "
                f"Source: {expectation.get('source', 'not stated')}"
            )

    assert not failures, (
        f"{path.name}: {len(failures)} metric(s) disagree with the manual "
        f"calculation:\n  - " + "\n  - ".join(failures)
    )


def test_template_is_not_treated_as_a_fixture():
    """TEMPLATE.json holds placeholder zeros; loading it as data would poison
    the suite with a company whose revenue is zero."""
    stems = {p.stem.upper() for p in list_golden()}
    assert "TEMPLATE" not in stems
    assert any(p.stem.upper() == "TEMPLATE" for p in list_golden(include_template=True))
