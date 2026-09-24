"""Configuration loading and validation.

The scoring model lives in YAML rather than in code so that an evaluator can
read and argue with it directly (PRD NF18). That only works if the YAML is
actually validated on load — a weights file whose components silently sum to
0.95 would quietly deflate a pillar and nobody would notice. Every invariant
the scoring engine relies on is therefore asserted here, at import time,
loudly.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: str | Path | None = None) -> None:
    """Load ``.env`` into ``os.environ`` (stdlib; no python-dotenv dependency).

    Existing environment variables win (``setdefault``), so a real shell export
    always overrides the file. Called once at CLI startup so ``analyze``/``serve``
    pick up keys from ``.env`` without the user exporting anything."""
    env_path = Path(path) if path is not None else (REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
CONFIG_DIR = REPO_ROOT / "app" / "config"

DEFAULT_THRESHOLDS = CONFIG_DIR / "thresholds.yaml"
DEFAULT_WEIGHTS = CONFIG_DIR / "weights.yaml"
DEFAULT_SOURCES = CONFIG_DIR / "sources.yaml"
DEFAULT_VALUATION = CONFIG_DIR / "valuation.yaml"

WEIGHT_TOLERANCE = 1e-6


class ConfigError(ValueError):
    """Raised when a configuration file violates an invariant the engine needs."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} did not parse to a mapping")
    return data


def _validate_curve(name: str, curve: Any) -> list[tuple[float, float]]:
    if not isinstance(curve, list) or len(curve) < 2:
        raise ConfigError(f"curve for '{name}' needs at least two points")
    points: list[tuple[float, float]] = []
    for point in curve:
        if not isinstance(point, list | tuple) or len(point) != 2:
            raise ConfigError(f"curve for '{name}' has a malformed point: {point!r}")
        x, y = float(point[0]), float(point[1])
        if not 0.0 <= y <= 100.0:
            raise ConfigError(f"curve for '{name}' has score {y} outside 0-100")
        points.append((x, y))
    for (x0, _), (x1, _) in zip(points, points[1:], strict=False):
        if x1 <= x0:
            raise ConfigError(
                f"curve for '{name}' must have strictly increasing x values "
                f"(saw {x0} then {x1})"
            )
    return points


@functools.lru_cache(maxsize=8)
def load_thresholds(path: str | None = None) -> dict[str, Any]:
    """Load and validate thresholds.yaml. Cached — the file is read once."""
    data = _read_yaml(Path(path) if path else DEFAULT_THRESHOLDS)

    if "version" not in data:
        raise ConfigError("thresholds.yaml must declare a version (it is stamped on reports)")

    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ConfigError("thresholds.yaml has no metrics section")

    for name, spec in metrics.items():
        if "curve" not in spec:
            raise ConfigError(f"metric '{name}' has no curve")
        spec["curve"] = _validate_curve(name, spec["curve"])

    for sector, overrides in (data.get("sector_overrides") or {}).items():
        for name, spec in overrides.items():
            if name not in metrics:
                raise ConfigError(
                    f"sector override '{sector}.{name}' targets a metric that does "
                    f"not exist in the base thresholds"
                )
            if "curve" in spec:
                spec["curve"] = _validate_curve(f"{sector}.{name}", spec["curve"])

    blend = data.get("blend") or {}
    total = float(blend.get("absolute_band", 0)) + float(blend.get("peer_percentile", 0))
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ConfigError(f"thresholds.yaml blend weights sum to {total}, not 1.0")

    return data


@functools.lru_cache(maxsize=8)
def load_weights(path: str | None = None) -> dict[str, Any]:
    """Load and validate weights.yaml. Cached."""
    data = _read_yaml(Path(path) if path else DEFAULT_WEIGHTS)

    if "version" not in data:
        raise ConfigError("weights.yaml must declare a version (it is stamped on reports)")

    pillars = data.get("pillars") or {}
    if not pillars:
        raise ConfigError("weights.yaml has no pillars section")
    total = sum(float(v) for v in pillars.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ConfigError(f"pillar weights sum to {total}, not 1.0")

    for pillar, components in (data.get("components") or {}).items():
        if not components:
            raise ConfigError(f"pillar '{pillar}' has an empty components map")
        subtotal = sum(float(v) for v in components.values())
        if abs(subtotal - 1.0) > WEIGHT_TOLERANCE:
            raise ConfigError(
                f"component weights for pillar '{pillar}' sum to {subtotal}, not 1.0"
            )
        if pillar not in pillars:
            raise ConfigError(f"components declared for unknown pillar '{pillar}'")

    methods = data.get("valuation_methods") or {}
    if methods:
        subtotal = sum(float(v) for v in methods.values())
        if abs(subtotal - 1.0) > WEIGHT_TOLERANCE:
            raise ConfigError(f"valuation_methods weights sum to {subtotal}, not 1.0")

    _validate_rating_bands(data.get("rating_bands") or {})
    return data


def _validate_rating_bands(bands: dict[str, Any]) -> None:
    """Rating bands must tile [0, 101) with no gap and no overlap.

    A gap would leave a composite score with no rating; an overlap would make
    the rating depend on dict iteration order, which is exactly the kind of
    non-determinism the whole design exists to prevent.
    """
    if not bands:
        raise ConfigError("weights.yaml has no rating_bands section")

    intervals = sorted(
        ((float(low), float(high), name) for name, (low, high) in bands.items()),
        key=lambda t: t[0],
    )
    if intervals[0][0] != 0:
        raise ConfigError(f"lowest rating band starts at {intervals[0][0]}, not 0")
    for (_, high, name), (low, _, next_name) in zip(intervals, intervals[1:], strict=False):
        if high != low:
            raise ConfigError(
                f"rating bands '{name}' and '{next_name}' are not contiguous "
                f"({high} vs {low}) — a composite score could fall in a gap or "
                f"match two bands"
            )


@functools.lru_cache(maxsize=8)
def load_sources(path: str | None = None) -> dict[str, Any]:
    """Load sources.yaml (Phase 3 allowlist). Validated lightly."""
    data = _read_yaml(Path(path) if path else DEFAULT_SOURCES)
    if "tiers" not in data:
        raise ConfigError("sources.yaml has no tiers section")
    return data


@functools.lru_cache(maxsize=8)
def load_valuation(path: str | None = None) -> dict[str, Any]:
    """Load and validate valuation.yaml (Phase 2 DCF parameters). Cached.

    The DCF is only as trustworthy as its bounds, so the invariants that keep a
    fair value finite and honest are asserted here at load time, loudly — the
    same discipline weights.yaml and thresholds.yaml get.
    """
    data = _read_yaml(Path(path) if path else DEFAULT_VALUATION)

    if "version" not in data:
        raise ConfigError("valuation.yaml must declare a version (it is stamped on reports)")

    wacc = data.get("wacc") or {}
    erp = float(wacc.get("equity_risk_premium_pct", 0))
    if not 6.0 <= erp <= 8.5:
        raise ConfigError(
            f"equity_risk_premium_pct {erp} is outside the India band 6.0-8.5 "
            f"(PRD 8.9); a value outside it needs a cited justification"
        )
    floor = float(wacc.get("beta_floor", 0))
    cap = float(wacc.get("beta_cap", 0))
    if not 0 < floor < cap:
        raise ConfigError(f"beta bounds must satisfy 0 < floor < cap (got {floor}, {cap})")

    tg = data.get("terminal_growth") or {}
    g_min, g_max = float(tg.get("min_pct", 0)), float(tg.get("max_pct", 0))
    if not 0 < g_min <= g_max:
        raise ConfigError(f"terminal_growth min/max must satisfy 0 < min <= max (got {g_min}, {g_max})")
    if g_max > 6.0:
        raise ConfigError(
            f"terminal_growth max_pct {g_max} exceeds India's nominal long-run "
            f"ceiling; the model must not assume a company outgrows the economy forever"
        )

    horizon = int((data.get("forecast") or {}).get("horizon_years", 0))
    if horizon not in (5, 10):
        raise ConfigError(f"forecast horizon_years must be 5 or 10 (got {horizon})")

    return data


def config_stamp() -> dict[str, str]:
    """Version stamp placed on every report so a score can always be
    reproduced against the configuration that produced it."""
    return {
        "weights_version": str(load_weights().get("version")),
        "thresholds_version": str(load_thresholds().get("version")),
        "valuation_version": str(load_valuation().get("version")),
    }
