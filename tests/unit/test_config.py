"""Configuration validation.

The scoring model lives in YAML so that a reader can argue with it without
reading Python. That only works if the YAML is validated on load: a weights
file whose components quietly sum to 0.95 would deflate a pillar by 5% and
nobody would ever notice. Each test below corresponds to a specific way a
hand-edited config file can be wrong.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.config.settings import (
    ConfigError,
    config_stamp,
    load_sources,
    load_thresholds,
    load_weights,
)


# ------------------------------------------------------- the shipped config --
def test_shipped_config_loads():
    thresholds = load_thresholds()
    weights = load_weights()
    assert thresholds["version"]
    assert weights["version"]
    assert weights["pillars"]["fundamentals"] == 0.30


def test_pillar_weights_sum_to_one():
    assert sum(load_weights()["pillars"].values()) == pytest.approx(1.0)


def test_every_pillar_component_map_sums_to_one():
    for pillar, components in load_weights()["components"].items():
        assert sum(components.values()) == pytest.approx(1.0), pillar


def test_rating_bands_tile_the_range_without_gaps():
    bands = load_weights()["rating_bands"]
    intervals = sorted((low, high) for low, high in bands.values())
    assert intervals[0][0] == 0
    for (_, high), (low, _) in zip(intervals, intervals[1:], strict=False):
        assert high == low


def test_every_scored_component_has_a_threshold_curve():
    """A component weight pointing at a metric with no curve would silently
    contribute nothing, permanently reweighting the pillar.

    Only the Phase 1 pillars are checked; valuation, industry, news and risk
    components are produced by later phases and are scored by their own
    engines rather than by a curve in thresholds.yaml.
    """
    thresholds = load_thresholds()["metrics"]
    components = load_weights()["components"]
    for pillar in ("fundamentals", "growth", "cashflow"):
        for metric in components[pillar]:
            assert metric in thresholds, (
                f"weights.yaml scores '{metric}' in pillar '{pillar}' but "
                f"thresholds.yaml has no curve for it"
            )


def test_sources_allowlist_loads():
    sources = load_sources()
    assert set(sources["tiers"]) == {1, 2, 3}
    assert sources["tiers"][1]["credibility_weight"] == 1.0
    assert sources["content_handling"]["treat_as_untrusted_data"] is True


def test_config_stamp_reports_both_versions():
    stamp = config_stamp()
    assert stamp["weights_version"]
    assert stamp["thresholds_version"]


# ------------------------------------------------------- rejection of bad ----
def _write(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def test_rejects_pillar_weights_that_do_not_sum_to_one(tmp_path):
    path = _write(tmp_path, "w.yaml", """
        version: "test"
        pillars:
          fundamentals: 0.30
          valuation: 0.20
        rating_bands:
          BUY: [0, 101]
    """)
    with pytest.raises(ConfigError, match="pillar weights sum to"):
        load_weights(path)


def test_rejects_component_weights_that_do_not_sum_to_one(tmp_path):
    path = _write(tmp_path, "w2.yaml", """
        version: "test"
        pillars:
          fundamentals: 1.0
        components:
          fundamentals:
            roe: 0.5
            roce: 0.4
        rating_bands:
          BUY: [0, 101]
    """)
    with pytest.raises(ConfigError, match="component weights for pillar"):
        load_weights(path)


def test_rejects_a_gap_between_rating_bands(tmp_path):
    """A composite score landing in a gap would have no rating at all."""
    path = _write(tmp_path, "w3.yaml", """
        version: "test"
        pillars:
          fundamentals: 1.0
        rating_bands:
          SELL: [0, 40]
          BUY:  [50, 101]
    """)
    with pytest.raises(ConfigError, match="not contiguous"):
        load_weights(path)


def test_rejects_a_missing_version(tmp_path):
    """The version is stamped on every report; without it a score cannot be
    reproduced against the configuration that produced it."""
    path = _write(tmp_path, "w4.yaml", """
        pillars:
          fundamentals: 1.0
        rating_bands:
          BUY: [0, 101]
    """)
    with pytest.raises(ConfigError, match="must declare a version"):
        load_weights(path)


def test_rejects_a_non_monotonic_curve(tmp_path):
    """Interpolation requires strictly increasing x values."""
    path = _write(tmp_path, "t.yaml", """
        version: "test"
        blend:
          absolute_band: 0.6
          peer_percentile: 0.4
        metrics:
          roe:
            curve: [[0, 0], [20, 50], [10, 90]]
    """)
    with pytest.raises(ConfigError, match="strictly increasing"):
        load_thresholds(path)


def test_rejects_a_score_outside_zero_to_hundred(tmp_path):
    path = _write(tmp_path, "t2.yaml", """
        version: "test"
        blend:
          absolute_band: 0.6
          peer_percentile: 0.4
        metrics:
          roe:
            curve: [[0, 0], [20, 150]]
    """)
    with pytest.raises(ConfigError, match="outside 0-100"):
        load_thresholds(path)


def test_rejects_a_sector_override_for_an_unknown_metric(tmp_path):
    """A typo in an override key would otherwise be silently ignored."""
    path = _write(tmp_path, "t3.yaml", """
        version: "test"
        blend:
          absolute_band: 0.6
          peer_percentile: 0.4
        metrics:
          roe:
            curve: [[0, 0], [20, 100]]
        sector_overrides:
          it_services:
            roe_typo:
              curve: [[0, 0], [30, 100]]
    """)
    with pytest.raises(ConfigError, match="does not exist in the base thresholds"):
        load_thresholds(path)


def test_rejects_blend_weights_that_do_not_sum_to_one(tmp_path):
    path = _write(tmp_path, "t4.yaml", """
        version: "test"
        blend:
          absolute_band: 0.7
          peer_percentile: 0.4
        metrics:
          roe:
            curve: [[0, 0], [20, 100]]
    """)
    with pytest.raises(ConfigError, match="blend weights sum to"):
        load_thresholds(path)


def test_missing_file_is_an_error_not_a_default(tmp_path):
    """Falling back to built-in defaults would mean a report stamped with a
    config version that was never actually applied."""
    with pytest.raises(ConfigError, match="not found"):
        load_weights(str(tmp_path / "does_not_exist.yaml"))
