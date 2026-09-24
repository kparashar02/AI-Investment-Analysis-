"""Validation of valuation.yaml (app/config/settings.load_valuation)."""

from __future__ import annotations

import textwrap

import pytest

from app.config.settings import ConfigError, load_valuation


def test_real_valuation_config_loads_and_stamps():
    data = load_valuation()
    assert data["version"]
    assert data["forecast"]["horizon_years"] in (5, 10)


def _write(tmp_path, body: str):
    path = tmp_path / "valuation.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def test_rejects_missing_version(tmp_path):
    path = _write(tmp_path, """
        wacc: {equity_risk_premium_pct: 7.0, beta_floor: 0.5, beta_cap: 2.0}
        terminal_growth: {min_pct: 3.0, max_pct: 5.5}
        forecast: {horizon_years: 5}
    """)
    with pytest.raises(ConfigError, match="version"):
        load_valuation(path)


def test_rejects_erp_outside_india_band(tmp_path):
    path = _write(tmp_path, """
        version: "9.9"
        wacc: {equity_risk_premium_pct: 12.0, beta_floor: 0.5, beta_cap: 2.0}
        terminal_growth: {min_pct: 3.0, max_pct: 5.5}
        forecast: {horizon_years: 5}
    """)
    with pytest.raises(ConfigError, match="equity_risk_premium"):
        load_valuation(path)


def test_rejects_terminal_growth_above_ceiling(tmp_path):
    path = _write(tmp_path, """
        version: "9.9"
        wacc: {equity_risk_premium_pct: 7.0, beta_floor: 0.5, beta_cap: 2.0}
        terminal_growth: {min_pct: 3.0, max_pct: 7.0}
        forecast: {horizon_years: 5}
    """)
    with pytest.raises(ConfigError, match="terminal_growth"):
        load_valuation(path)


def test_rejects_bad_horizon(tmp_path):
    path = _write(tmp_path, """
        version: "9.9"
        wacc: {equity_risk_premium_pct: 7.0, beta_floor: 0.5, beta_cap: 2.0}
        terminal_growth: {min_pct: 3.0, max_pct: 5.5}
        forecast: {horizon_years: 7}
    """)
    with pytest.raises(ConfigError, match="horizon"):
        load_valuation(path)


def test_rejects_inverted_beta_bounds(tmp_path):
    path = _write(tmp_path, """
        version: "9.9"
        wacc: {equity_risk_premium_pct: 7.0, beta_floor: 2.0, beta_cap: 0.5}
        terminal_growth: {min_pct: 3.0, max_pct: 5.5}
        forecast: {horizon_years: 5}
    """)
    with pytest.raises(ConfigError, match="beta"):
        load_valuation(path)
