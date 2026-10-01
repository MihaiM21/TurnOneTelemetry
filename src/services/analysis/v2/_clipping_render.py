"""
Energy clipping rendering -- presentation only.

Numbers and labels, no sentences. Three panels, recomposed per format:

* leaderboard -- time lost to clipping per driver (bar in team colour, seconds
  at the bar end, metres clipped as a muted column)
* track map -- the pole lap's racing line in grey with the highlighted driver's
  clipping zones overlaid in their colour
* speed trace -- the highlighted driver's lap, zones shaded and each labelled
  with the speed it cost

landscape: leaderboard left, map over trace right. square: the same, narrower.
portrait / story: map, trace and a shortened leaderboard stacked top to bottom.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from matplotlib.collections import LineCollection

from src.services.analysis.v2._lap_duel_render import _session_label
from src.services.plotting import canvas as cv
from src.services.plotting import output as dirOrg

MUTED = cv.MUTED
TEXT = cv.TEXT

# Leaderboard rows per format (None = the whole field).
_MAX_ROWS = {"landscape": None, "square": None, "portrait": 12, "story": 10}


def output_path(payload: Dict[str, Any], fmt_name: Optional[str], driver: str) -> str:
    info = payload["session_info"]
    y, event, session = info["year"], info["event_name"], info["session_name"]
    folder = f"{y}/{event.replace(' ', '')}/{session}"
    dirOrg.checkForFolder(folder)
    name = f"Energy clipping {driver}{cv.format_suffix(fmt_name or 'landscape')}.png"
    return f"outputs/plots/{folder}/{name}"


def resolve_driver(payload: Dict[str, Any], driver: Optional[str]) -> Dict[str, Any]:
    """The payload's entry for ``driver`` (default: the pole lap); ValueError if unknown."""
    codes = [d["driver"] for d in payload["drivers"]]
    code = (driver or payload["reference_driver"]).strip().upper()
    for d in payload["drivers"]:
        if d["driver"] == code:
            return d
    raise ValueError(f"driver must be one of {', '.join(sorted(codes))} (got {driver!r})")


def _rows(payload: Dict[str, Any], code: str, limit: Optional[int]) -> List[Dict[str, Any]]:
    drivers = payload["drivers"]
    if limit is None or len(drivers) <= limit:
        return list(drivers)
    top = drivers[:limit]
    if any(d["driver"] == code for d in top):
        return top
    # Keep the highlighted driver visible even when they are outside the cut.
    return top[:limit - 1] + [d for d in drivers if d["driver"] == code]


# ----------------------------------------------------------------------
# Panels
# ----------------------------------------------------------------------
def _leaderboard(ax, payload, fmt: cv.CanvasFormat, code: str) -> None:
    rows = _rows(payload, code, _MAX_ROWS[fmt.name])
    n = len(rows)
    vmax = max(d["time_lost_s"] for d in payload["drivers"]) or 1.0
    row_px = ax.get_position().height * fmt.height_px / (n + 1.4)
    size = float(np.clip(row_px * 0.5 * 72.0 / cv.DESIGN_DPI, fmt.base_fontsize * 0.75, fmt.base_fontsize * 0.95))
    small = max(size * 0.88, fmt.base_fontsize * 0.7)

    # Bars use whatever the value label and the clipped-metres column leave over.
    font_px = cv.DESIGN_DPI / 72.0
    axes_px = ax.get_position().width * fmt.width_px
    text_px = 6.6 * 0.66 * size * font_px + 6.6 * 0.6 * small * font_px + 0.9 * size * font_px
    ax.set_xlim(0, vmax / float(np.clip(1.0 - text_px / axes_px, 0.3, 0.8)))
    ax.set_ylim(n - 0.4, -1.5)
    for i, d in enumerate(rows):
        hi = d["driver"] == code
        if hi:
            ax.axhspan(i - 0.5, i + 0.5, color="#1d1d1d", lw=0, zorder=0)
        ax.barh(i, d["time_lost_s"], height=0.66, color=d["color"], alpha=1.0 if hi else 0.72,
                edgecolor=TEXT if hi else "none", linewidth=1.3, zorder=2)
        ax.text(d["time_lost_s"], i, f" {d['time_lost_s']:.3f}s", ha="left", va="center", fontsize=size,
                color=TEXT, fontweight="bold" if hi else "normal", zorder=3)
        ax.text(1.0, i, f"{d['clip_m']:.0f} m", transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontsize=small, color=TEXT if hi else MUTED, fontweight="bold" if hi else "normal")
    ax.text(0.0, -1.0, "TIME LOST", ha="left", va="center", fontsize=small, color=MUTED, fontweight="semibold")
    ax.text(1.0, -1.0, "CLIPPED", transform=ax.get_yaxis_transform(), ha="right", va="center",
            fontsize=small, color=MUTED, fontweight="semibold")

    ax.set_yticks(range(n))
    ax.set_yticklabels([d["driver"] for d in rows])
    ax.tick_params(axis="y", length=0, labelsize=size, pad=6)
    ax.tick_params(axis="x", length=0, labelbottom=False)
    for label, d in zip(ax.get_yticklabels(), rows):
        hi = d["driver"] == code
        label.set_fontweight("bold" if hi else "normal")
        label.set_color(d["color"] if hi else TEXT)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(False)


def _rotated(track: Dict[str, Any], x, y):
    theta = np.deg2rad(track.get("rotation") or 0)
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    return x * np.cos(theta) - y * np.sin(theta), x * np.sin(theta) + y * np.cos(theta)


def _zone_points(track: Dict[str, Any], zone: Dict[str, Any]):
    """Outline points covering a zone, interpolated by lap fraction."""
    frac = np.asarray(track["fraction"], dtype=float)
    span = np.linspace(zone["start_fraction"], zone["end_fraction"], 40)
    return np.interp(span, frac, track["x"]), np.interp(span, frac, track["y"])


def _track_map(ax, payload, fmt: cv.CanvasFormat, entry: Dict[str, Any]) -> None:
    track = payload.get("track")
    ax.set_axis_off()
    if not track:
        return
    xr, yr = _rotated(track, track["x"], track["y"])
    pts = np.column_stack([xr, yr]).reshape(-1, 1, 2)
    base = np.concatenate([pts[:-1], pts[1:]], axis=1)
    ax.add_collection(LineCollection(base, colors="#4a4a4a", linewidths=5.0, capstyle="round", zorder=1))
    for zone in entry["zones"]:
        zx, zy = _rotated(track, *_zone_points(track, zone))
        seg = np.column_stack([zx, zy]).reshape(-1, 1, 2)
        ax.add_collection(LineCollection(np.concatenate([seg[:-1], seg[1:]], axis=1), colors=entry["color"],
                                         linewidths=8.5, capstyle="round", zorder=2))
    ax.plot(xr[0], yr[0], marker="s", color=TEXT, ms=6.5, zorder=3)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.margins(0.06)


def _speed_trace(ax, fmt: cv.CanvasFormat, entry: Dict[str, Any]) -> None:
    dist = np.asarray(entry["trace"]["distance"], dtype=float)
    speed = np.asarray(entry["trace"]["speed"], dtype=float)
    color = entry["color"]
    ax.plot(dist, speed, color=TEXT, lw=1.8, zorder=3, solid_capstyle="round")
    vmax, vmin = float(speed.max()), float(speed.min())
    rng = max(vmax - vmin, 1.0)
    ax.set_xlim(dist[0], dist[-1])

    for zone in entry["zones"]:
        ax.axvspan(zone["start_m"], zone["end_m"], color=color, alpha=0.25, lw=0, zorder=1)
        m = (dist >= zone["start_m"]) & (dist <= zone["end_m"])
        if m.sum() > 1:
            ax.plot(dist[m], speed[m], color=color, lw=3.6, zorder=4, solid_capstyle="round")

    # Biggest zones first. Each label sits just above its zone; one that would
    # land on an already placed label steps up a line, and is dropped if it
    # still cannot fit.
    size = fmt.base_fontsize * 0.82
    px_per_pt = cv.DESIGN_DPI / 72.0
    span = dist[-1] - dist[0]
    axes_w = ax.get_position().width * fmt.width_px
    axes_h = ax.get_position().height * fmt.height_px
    half = span * (size * px_per_pt * 0.66 * 8) / max(axes_w, 1.0) / 2.0
    line_pt = size * 1.35
    label_px = size * px_per_pt * 1.25
    dpp = 1.3 * rng / max(axes_h, 1.0)  # provisional data units per pixel
    boxes: List[tuple] = []
    labels = []
    for zone in sorted(entry["zones"], key=lambda z: -z["kmh_lost"]):
        x, ha = (zone["start_m"] + zone["end_m"]) / 2.0, "center"
        x0, x1 = x - half, x + half
        if x1 > dist[-1]:
            x, ha, x0, x1 = float(dist[-1]), "right", dist[-1] - 2 * half, dist[-1]
        elif x0 < dist[0]:
            x, ha, x0, x1 = float(dist[0]), "left", dist[0], dist[0] + 2 * half
        m = (dist >= zone["start_m"]) & (dist <= zone["end_m"])
        peak = float(speed[m].max()) if m.any() else vmax
        base_px = (peak - (vmin - 0.05 * rng)) / dpp
        for k in range(3):
            off = 9 + k * line_pt
            y0 = base_px + off * px_per_pt
            if not any(x0 < bx1 and x1 > bx0 and y0 < by1 and y0 + label_px > by0 for bx0, bx1, by0, by1 in boxes):
                boxes.append((x0, x1, y0, y0 + label_px))
                labels.append((x, peak, off, ha, zone["kmh_lost"]))
                break

    # Headroom so the highest label stays inside the axes.
    need_px = 8.0 + max((off * px_per_pt + label_px + (peak - vmax) / dpp for _, peak, off, _, _ in labels),
                        default=0.0)
    if need_px > 0:
        ax.set_ylim(vmin - 0.05 * rng, vmax + need_px * 1.05 * rng / max(axes_h - need_px, 1.0))
    else:
        ax.set_ylim(vmin - 0.05 * rng, vmax + 0.05 * rng)
    ax.set_yticks(list(range(100, int(vmax) + 1, 100)))
    for x, peak, off, ha, kmh in labels:
        ax.annotate(f"−{kmh:.0f} km/h", (x, peak), xytext=(0, off), textcoords="offset points",
                    ha=ha, va="bottom", fontsize=size, color=color, fontweight="bold", zorder=6)
    ax.set_ylabel("Speed (km/h)")
    ax.set_xlabel("Lap distance (m)")


# ----------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------
# Leaderboard row pitch (px) for the stacked formats.
_PITCH_PX = {"portrait": 31.0, "story": 38.0}
_MAP_SHARE = {"portrait": 0.58, "story": 0.62}


def _layout(fig, fmt: cv.CanvasFormat, n_rows: int):
    """``(leaderboard_rect | spec, map_spec, trace_spec)`` for the format."""
    if fmt.name in ("landscape", "square"):
        ratios = [1.0, 1.75] if fmt.name == "landscape" else [1.2, 1.0]
        gs = cv.safe_gridspec(fig, fmt, 1, 2, width_ratios=ratios, wspace=0.16 if fmt.name == "landscape" else 0.3)
        right = gs[0, 1].subgridspec(2, 1, height_ratios=[1.6, 1.0], hspace=0.16)
        return gs[0, 0], right[0], right[1]

    # Stacked: sizes in pixels. The leaderboard has no x axis, so it may use the
    # band safe_gridspec keeps for tick labels; the trace needs a gap for its own.
    font_px = fmt.base_fontsize * cv.DESIGN_DPI / 72.0
    probe = cv.safe_gridspec(fig, fmt, 1)[0].get_position(fig)
    total_px = probe.height * fmt.height_px
    pad_px = 2.8 * font_px
    gap_px = 3.1 * font_px
    lb_px = (n_rows + 1.4) * _PITCH_PX[fmt.name]
    free = total_px - gap_px - (lb_px - pad_px)
    map_px = free * _MAP_SHARE[fmt.name]
    gs = cv.safe_gridspec(fig, fmt, 5, 1, hspace=0.0, height_ratios=[
        map_px - 0.5 * font_px, 0.5 * font_px, free - map_px, gap_px, lb_px - pad_px])
    return gs[4], gs[0], gs[2]


def render(payload: Dict[str, Any], fmt_name: Optional[str] = None, driver: Optional[str] = None) -> str:
    fmt = cv.get_format(fmt_name or "landscape")
    entry = resolve_driver(payload, driver)
    code = entry["driver"]
    info = payload["session_info"]

    fig = cv.new_canvas(fmt)
    cv.add_header(fig, fmt, f"Energy clipping · {code}",
                  f"{info['year']} {info['event_name']}  ·  {_session_label(info['session_name'])}")
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)
    _, _, right, top = fmt.safe
    mark_px = 0.042 * min(fmt.width_px, fmt.height_px)
    cv.pin(fig.text(right, top - (mark_px + 10) / fmt.height_px, "ESTIMATED", ha="right", va="top",
                    fontsize=fmt.base_fontsize * 0.7, color=MUTED, fontweight="semibold"))

    rows = _rows(payload, code, _MAX_ROWS[fmt.name])
    lb_spec, map_spec, trace_spec = _layout(fig, fmt, len(rows))

    if fmt.is_vertical:
        pos = lb_spec.get_position(fig)
        pad = 2.8 * fmt.base_fontsize * cv.DESIGN_DPI / 72.0 / fmt.height_px
        lb_ax = fig.add_axes((pos.x0, pos.y0 - pad, pos.width, pos.height + pad))
    else:
        lb_ax = fig.add_subplot(lb_spec)
    _leaderboard(lb_ax, payload, fmt, code)

    if fmt.is_vertical:
        # The map is axis-less: let it use the width the y-label pad reserves.
        pos = map_spec.get_position(fig)
        map_ax = fig.add_axes((fmt.safe[0], pos.y0, fmt.safe[2] - fmt.safe[0], pos.height))
    else:
        map_ax = fig.add_subplot(map_spec)
    _track_map(map_ax, payload, fmt, entry)

    trace_ax = fig.add_subplot(trace_spec)
    _speed_trace(trace_ax, fmt, entry)
    cv.style_axis(trace_ax, fmt)

    return cv.save_png(fig, output_path(payload, fmt_name, code), fmt)
