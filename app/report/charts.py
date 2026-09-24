"""Inline-SVG charts for the report (PRD 13.4).

Eight charts, each a pure function from data to a self-contained ``<svg>``
string. Inline SVG is deliberate: it renders identically in a browser and in a
PDF engine, needs no JavaScript and no charting dependency, and is fully
deterministic — the same inputs produce byte-identical markup, which keeps the
report reproducible and unit-testable. Missing data yields a labelled
placeholder rather than a crash.
"""

from __future__ import annotations

import html
import math
from typing import Any

# A small, print-safe palette (readable on white; distinct in greyscale order).
_SERIES = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0891b2"]
_AXIS = "#94a3b8"
_INK = "#334155"
_GRID = "#e2e8f0"


def _esc(text: Any) -> str:
    return html.escape(str(text))


def _svg(body: str, *, width: int = 640, height: int = 360, title: str = "") -> str:
    label = f'<title>{_esc(title)}</title>' if title else ""
    return (f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{_esc(title)}" class="chart">{label}{body}</svg>')


def _placeholder(title: str, width: int = 640, height: int = 360) -> str:
    body = (f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc"/>'
            f'<text x="{width/2}" y="{height/2}" text-anchor="middle" fill="{_AXIS}" '
            f'font-size="14">{_esc(title)}: no data</text>')
    return _svg(body, width=width, height=height, title=title)


def _text(x: float, y: float, s: str, *, anchor: str = "middle", size: int = 11,
          fill: str = _INK, weight: str = "normal") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}">{_esc(s)}</text>')


def _line(x1, y1, x2, y2, *, stroke=_AXIS, width=1.0, dash: str = "") -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{width}"{d}/>'


def _clean(values: list[float | None]) -> list[float]:
    return [v for v in values if isinstance(v, (int, float))]


# 1 -----------------------------------------------------------------------
def radar_chart(pillars: list[tuple[str, float | None]]) -> str:
    if not pillars:
        return _placeholder("Pillar scores")
    cx, cy, r = 320, 190, 130
    n = len(pillars)
    rings = "".join(
        f'<circle cx="{cx}" cy="{cy}" r="{r*f:.1f}" fill="none" stroke="{_GRID}"/>'
        for f in (0.25, 0.5, 0.75, 1.0))
    axes, labels, pts = [], [], []
    for i, (name, score) in enumerate(pillars):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        ax, ay = cx + r * math.cos(ang), cy + r * math.sin(ang)
        axes.append(_line(cx, cy, ax, ay, stroke=_GRID))
        lx, ly = cx + (r + 18) * math.cos(ang), cy + (r + 18) * math.sin(ang)
        labels.append(_text(lx, ly + 3, name, size=10, fill=_INK))
        val = (score or 0) / 100.0
        pts.append(f"{cx + r*val*math.cos(ang):.1f},{cy + r*val*math.sin(ang):.1f}")
    poly = (f'<polygon points="{" ".join(pts)}" fill="{_SERIES[0]}33" '
            f'stroke="{_SERIES[0]}" stroke-width="2"/>')
    body = rings + "".join(axes) + poly + "".join(labels) + _text(cx, 24, "Pillar Scores", size=13, weight="bold")
    return _svg(body, title="Pillar scores")


# 2 -----------------------------------------------------------------------
def _bar_axes(periods: list[str], vmax: float, *, x0=60, x1=610, y0=40, y1=300) -> tuple[str, Any]:
    grid = _line(x0, y1, x1, y1, stroke=_AXIS) + _line(x0, y0, x0, y1, stroke=_AXIS)
    for f in (0.25, 0.5, 0.75, 1.0):
        y = y1 - (y1 - y0) * f
        grid += _line(x0, y, x1, y, stroke=_GRID) + _text(x0 - 6, y + 3, f"{vmax*f:,.0f}", anchor="end", size=9)
    def x_of(i: int) -> float:
        step = (x1 - x0) / max(1, len(periods))
        return x0 + step * (i + 0.5)
    def y_of(v: float) -> float:
        return y1 - (y1 - y0) * (v / vmax if vmax else 0)
    for i, p in enumerate(periods):
        grid += _text(x_of(i), y1 + 16, p, size=9)
    return grid, (x_of, y_of, x0, x1, y0, y1)


def grouped_bar_revenue_pat(periods, revenue, pat) -> str:
    vals = _clean(revenue) + _clean(pat)
    if not periods or not vals:
        return _placeholder("Revenue & PAT")
    vmax = max(vals) * 1.1
    axes, (x_of, y_of, *_ ) = _bar_axes(periods, vmax)
    step = (610 - 60) / max(1, len(periods))
    bw = step * 0.32
    bars = ""
    for i in range(len(periods)):
        cx = x_of(i)
        if i < len(revenue) and isinstance(revenue[i], (int, float)):
            bars += f'<rect x="{cx-bw-1:.1f}" y="{y_of(revenue[i]):.1f}" width="{bw:.1f}" height="{300-y_of(revenue[i]):.1f}" fill="{_SERIES[0]}"/>'
        if i < len(pat) and isinstance(pat[i], (int, float)):
            bars += f'<rect x="{cx+1:.1f}" y="{y_of(pat[i]):.1f}" width="{bw:.1f}" height="{300-y_of(pat[i]):.1f}" fill="{_SERIES[1]}"/>'
    legend = (f'<rect x="440" y="20" width="10" height="10" fill="{_SERIES[0]}"/>' + _text(500, 29, "Revenue", size=10) +
              f'<rect x="440" y="34" width="10" height="10" fill="{_SERIES[1]}"/>' + _text(487, 43, "PAT", size=10))
    return _svg(_text(320, 24, "Revenue & PAT (₹ cr)", size=13, weight="bold") + axes + bars + legend,
                title="Revenue and PAT")


# 3 -----------------------------------------------------------------------
def margin_lines(periods, series: dict[str, list[float | None]]) -> str:
    present = {k: v for k, v in series.items() if _clean(v)}
    if not periods or not present:
        return _placeholder("Margin trend")
    allvals = [x for v in present.values() for x in _clean(v)]
    vmax = max(allvals) * 1.15 if allvals else 1
    axes, (x_of, y_of, *_ ) = _bar_axes(periods, vmax)
    paths, legend = "", ""
    for idx, (name, vals) in enumerate(present.items()):
        color = _SERIES[idx % len(_SERIES)]
        pts = [f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in enumerate(vals) if isinstance(v, (int, float))]
        if len(pts) >= 2:
            paths += f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2"/>'
        legend += f'<rect x="440" y="{18+idx*14}" width="10" height="10" fill="{color}"/>' + _text(500, 27+idx*14, name, size=9)
    return _svg(_text(320, 24, "Margin Trend (%)", size=13, weight="bold") + axes + paths + legend,
                title="Margin trend")


# 4 -----------------------------------------------------------------------
def ocf_vs_pat(periods, ocf, pat) -> str:
    vals = _clean(ocf) + _clean(pat)
    if not periods or not vals:
        return _placeholder("OCF vs PAT")
    vmax = max(vals) * 1.1
    axes, (x_of, y_of, *_ ) = _bar_axes(periods, vmax)
    step = (610 - 60) / max(1, len(periods))
    bw = step * 0.4
    bars = ""
    for i in range(len(periods)):
        if i < len(ocf) and isinstance(ocf[i], (int, float)):
            bars += f'<rect x="{x_of(i)-bw/2:.1f}" y="{y_of(ocf[i]):.1f}" width="{bw:.1f}" height="{300-y_of(ocf[i]):.1f}" fill="{_SERIES[4]}"/>'
    line_pts = [f"{x_of(i):.1f},{y_of(pat[i]):.1f}" for i in range(len(periods))
                if i < len(pat) and isinstance(pat[i], (int, float))]
    line = f'<polyline points="{" ".join(line_pts)}" fill="none" stroke="{_SERIES[3]}" stroke-width="2.5"/>' if len(line_pts) >= 2 else ""
    dots = "".join(f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" fill="{_SERIES[3]}"/>' for p in line_pts)
    legend = (f'<rect x="430" y="20" width="10" height="10" fill="{_SERIES[4]}"/>' + _text(470, 29, "OCF", size=10) +
              f'<line x1="430" y1="40" x2="446" y2="40" stroke="{_SERIES[3]}" stroke-width="2.5"/>' + _text(470, 43, "PAT", size=10))
    return _svg(_text(320, 24, "OCF vs PAT (₹ cr)", size=13, weight="bold") + axes + bars + line + dots + legend,
                title="OCF vs PAT")


# 5 -----------------------------------------------------------------------
def peer_multiples_bar(comparisons: list[Any]) -> str:
    rows = [(c.multiple, c.company_value, c.benchmark_value) for c in comparisons
            if getattr(c, "company_value", None) is not None]
    if not rows:
        return _placeholder("Peer multiples")
    vmax = max(max(cv, bv or 0) for _, cv, bv in rows) * 1.2
    x0, x1, y0 = 90, 600, 50
    rh = min(40, (300 - y0) / len(rows))
    body = _text(320, 24, "Valuation vs Peer Median", size=13, weight="bold")
    for i, (name, cv, bv) in enumerate(rows):
        y = y0 + i * rh
        w = (x1 - x0) * (cv / vmax)
        body += _text(x0 - 6, y + rh/2, name, anchor="end", size=10)
        body += f'<rect x="{x0}" y="{y+4:.1f}" width="{w:.1f}" height="{rh-10:.1f}" fill="{_SERIES[0]}"/>'
        body += _text(x0 + w + 6, y + rh/2, f"{cv:.1f}x", anchor="start", size=9)
        if bv:
            mx = x0 + (x1 - x0) * (bv / vmax)
            body += _line(mx, y + 2, mx, y + rh - 4, stroke=_SERIES[3], width=2)
    body += (f'<line x1="440" y1="316" x2="456" y2="316" stroke="{_SERIES[3]}" stroke-width="2"/>' +
             _text(505, 319, "peer median", size=9))
    return _svg(body, title="Peer multiples")


# 6 -----------------------------------------------------------------------
def price_band(price_history: list[Any], fair_low, fair_high, cmp_) -> str:
    pts = [(str(d), float(p)) for d, p in (price_history or []) if p is not None]
    if len(pts) < 2:
        return _placeholder("Price history & fair value")
    prices = [p for _, p in pts]
    lo = min([*prices, fair_low or min(prices)])
    hi = max([*prices, fair_high or max(prices)])
    span = (hi - lo) or 1
    x0, x1, y0, y1 = 55, 610, 45, 300
    def X(i): return x0 + (x1 - x0) * i / (len(pts) - 1)
    def Y(v): return y1 - (y1 - y0) * (v - lo) / span
    band = ""
    if fair_low is not None and fair_high is not None:
        band = f'<rect x="{x0}" y="{Y(fair_high):.1f}" width="{x1-x0}" height="{Y(fair_low)-Y(fair_high):.1f}" fill="{_SERIES[1]}22"/>'
        band += _text(x1 - 4, Y(fair_high) - 3, "fair value", anchor="end", size=9, fill=_SERIES[1])
    poly = " ".join(f"{X(i):.1f},{Y(p):.1f}" for i, (_, p) in enumerate(pts))
    line = f'<polyline points="{poly}" fill="none" stroke="{_SERIES[0]}" stroke-width="1.8"/>'
    axes = _line(x0, y1, x1, y1, stroke=_AXIS) + _line(x0, y0, x0, y1, stroke=_AXIS)
    axes += _text(x0 - 6, Y(hi) + 3, f"{hi:,.0f}", anchor="end", size=9) + _text(x0 - 6, Y(lo) + 3, f"{lo:,.0f}", anchor="end", size=9)
    return _svg(_text(320, 24, "Price History & Fair-Value Band", size=13, weight="bold") + band + axes + line,
                title="Price history and fair value")


# 7 -----------------------------------------------------------------------
def sensitivity_heatmap(sensitivity: Any) -> str:
    if sensitivity is None or not sensitivity.grid:
        return _placeholder("DCF sensitivity")
    grid = sensitivity.grid
    flat = [c for row in grid for c in row if isinstance(c, (int, float))]
    if not flat:
        return _placeholder("DCF sensitivity")
    lo, hi = min(flat), max(flat)
    span = (hi - lo) or 1
    rows, cols = len(grid), len(grid[0])
    x0, y0, cw, ch = 110, 60, min(90, 480 / cols), 34
    body = _text(320, 24, "DCF Sensitivity — fair value / share", size=13, weight="bold")
    for j, g in enumerate(sensitivity.growth_values_pct):
        body += _text(x0 + cw * (j + 0.5), y0 - 8, f"g {g:.1f}%", size=9)
    for i, wv in enumerate(sensitivity.wacc_values_pct):
        body += _text(x0 - 8, y0 + ch * (i + 0.6), f"WACC {wv:.1f}%", anchor="end", size=9)
        for j in range(cols):
            cell = grid[i][j]
            x, y = x0 + cw * j, y0 + ch * i
            if isinstance(cell, (int, float)):
                t = (cell - lo) / span
                r_, g_, b_ = int(220 - 140 * t), int(235 - 60 * t), int(255 - 200 * t)
                body += f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw-2:.1f}" height="{ch-2:.1f}" fill="rgb({r_},{g_},{b_})"/>'
                body += _text(x + cw/2, y + ch/2 + 3, f"{cell:,.0f}", size=9, fill=_INK)
            else:
                body += f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw-2:.1f}" height="{ch-2:.1f}" fill="#f1f5f9"/>'
    return _svg(body, title="DCF sensitivity")


# 8 -----------------------------------------------------------------------
def news_scatter(points: list[tuple[str, float, int]]) -> str:
    if not points:
        return _placeholder("News over time")
    x0, x1, y0, y1 = 55, 610, 50, 290
    mid = (y0 + y1) / 2
    body = _text(320, 24, "News: direction over time (size = materiality)", size=12, weight="bold")
    body += _line(x0, mid, x1, mid, stroke=_GRID) + _line(x0, y0, x0, y1, stroke=_AXIS)
    body += _text(x0 - 6, y0 + 6, "+", anchor="end", size=12, fill=_SERIES[1]) + _text(x0 - 6, y1, "–", anchor="end", size=12, fill=_SERIES[3])
    n = len(points)
    for i, (_date, direction, materiality) in enumerate(points):
        x = x0 + (x1 - x0) * (i + 0.5) / n
        y = mid - (mid - y0) * max(-1.0, min(1.0, direction))
        color = _SERIES[1] if direction > 0 else (_SERIES[3] if direction < 0 else _AXIS)
        body += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3 + 2*materiality:.1f}" fill="{color}66" stroke="{color}"/>'
    return _svg(body, title="News over time")
