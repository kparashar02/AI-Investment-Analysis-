"""Guardrail & veto rules (app/engine/guardrails.py)."""

from __future__ import annotations

from app.engine.guardrails import apply_vetoes, evaluate_vetoes
from app.models.decision import Rating, VetoTrigger


def _by_code(vetoes):
    return {v.code: v for v in vetoes}


# --- evaluate_vetoes on the fixtures ---------------------------------------

def test_healthy_company_trips_no_hard_vetoes(acme, acme_metrics):
    vetoes = _by_code(evaluate_vetoes(
        acme_metrics, acme.statements, data_completeness_pct=100.0, upside_pct=25.0))
    for code in ("V1", "V2", "V3", "V4", "V5"):
        assert vetoes[code].triggered is False


def test_distressed_company_trips_leverage_and_distress(levcyc, levcyc_metrics):
    vetoes = _by_code(evaluate_vetoes(
        levcyc_metrics, levcyc.statements, data_completeness_pct=100.0, upside_pct=None))
    assert vetoes["V2"].triggered is True    # net debt/EBITDA > 5 and cover < 1.5
    assert vetoes["V3"].triggered is True    # 3-year poor cash conversion
    assert vetoes["V4"].triggered is True    # Altman Z in distress


def test_v1_triggers_on_thin_data(acme, acme_metrics):
    vetoes = _by_code(evaluate_vetoes(
        acme_metrics, acme.statements, data_completeness_pct=55.0, upside_pct=10.0))
    assert vetoes["V1"].triggered is True
    assert vetoes["V1"].values["data_completeness_pct"] == 55.0


def test_v7_triggers_on_large_downside(acme, acme_metrics):
    vetoes = _by_code(evaluate_vetoes(
        acme_metrics, acme.statements, data_completeness_pct=100.0, upside_pct=-55.0))
    assert vetoes["V7"].triggered is True


def test_v6_and_v9_are_not_evaluable_in_phase_2(acme, acme_metrics):
    vetoes = _by_code(evaluate_vetoes(
        acme_metrics, acme.statements, data_completeness_pct=100.0, upside_pct=10.0))
    assert vetoes["V6"].evaluable is False
    assert vetoes["V9"].evaluable is False


# --- apply_vetoes semantics -------------------------------------------------

def _veto(code, action, triggered=True):
    return VetoTrigger(code=code, description=code, action=action, evaluable=True,
                       triggered=triggered)


def test_no_rating_overrides_everything():
    rating, applied, _ = apply_vetoes(Rating.BUY, [_veto("V1", "NO_RATING")])
    assert rating is Rating.NO_RATING
    assert "V1" in applied


def test_not_supported_overrides():
    rating, applied, _ = apply_vetoes(Rating.BUY, [_veto("V8", "NOT_SUPPORTED")])
    assert rating is Rating.NOT_SUPPORTED


def test_cap_at_reduce_lowers_a_buy():
    rating, applied, _ = apply_vetoes(Rating.BUY, [_veto("V2", "CAP_AT_REDUCE")])
    assert rating is Rating.REDUCE
    assert "V2" in applied


def test_cap_does_not_raise_a_worse_rating():
    # A SELL capped at HOLD stays SELL — a cap is a ceiling, never a floor.
    rating, _, _ = apply_vetoes(Rating.SELL, [_veto("V3", "CAP_AT_HOLD")])
    assert rating is Rating.SELL


def test_worst_cap_governs_when_several_fire():
    rating, applied, _ = apply_vetoes(
        Rating.BUY, [_veto("V3", "CAP_AT_HOLD"), _veto("V2", "CAP_AT_REDUCE")])
    assert rating is Rating.REDUCE
    assert set(applied) == {"V2", "V3"}


def test_untriggered_vetoes_leave_rating_untouched():
    rating, applied, _ = apply_vetoes(
        Rating.ACCUMULATE, [_veto("V2", "CAP_AT_REDUCE", triggered=False)])
    assert rating is Rating.ACCUMULATE
    assert applied == []


def test_downgrade_confidence_flag():
    rating, applied, downgraded = apply_vetoes(
        Rating.BUY, [_veto("V9", "DOWNGRADE_CONFIDENCE")])
    assert rating is Rating.BUY       # rating retained
    assert downgraded is True
