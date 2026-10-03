"""
Field-wide charts rendering -- presentation only.

* :func:`render_dominance` -- the field dominance map: the track outline coloured
  minisector by minisector in the colour of whoever was fastest through it, and
  a ranked list of owners with the number of minisectors each one holds.
* :func:`render_sector_gap` -- gap to pole for P2..P10 as a diverging stacked bar
  split into S1 / S2 / S3.

Numbers and labels only. The same payload is recomposed per canvas format
rather than cropped; text never drops below 0.7x the format's base font size.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from matplotlib.collections import LineCollection

from src.services.plotting import canvas as cv
from src.services.plotting import output as dirOrg

MUTED = cv.MUTED
TEXT = cv.TEXT
MIN_FONT = 0.7          # x base_fontsize

_SESSION_LONG = {"Q": "Qualifying", "R": "Race", "S": "Sprint", "SQ": "Sprint Qualifying",
                 "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3"}

# Sector shades: light -> dark blue, each readable on the dark canvas.
SECTOR_COLOURS = ("#8FD3F4", "#4C9FE0", "#2A62B8")
_SECTOR_TEXT = ("#0d0d0d", "#0d0d0d", "#F2F2F2")


def _output_path(info: Dict[str, Any], name: str, fmt_name: Optional[str]) -> str:
    year, event, session = info["year"], info["event_name"], info["session_name"]
    folder = f"{year}/{event.replace(' ', '')}/{session}"
    dirOrg.checkForFolder(folder)
    return f"outputs/plots/{folder}/{name}{cv.format_suffix(fmt_name or 'landscape')}.png"


def _content_box(fmt: cv.CanvasFormat) -> Tuple[float, float, float, float]:
    """``(left, bottom, width, height)`` in figure fractions below the header, above the footer."""
    left, bottom, right, top = fmt.safe
    content_top = top - cv.header_height(fmt)
    content_bottom = bottom + 0.05 * (top - bottom)
    return left, content_bottom, right - left, content_top - content_bottom


def _font_px(fmt: cv.CanvasFormat, scale: float = 1.0) -> float:
    return fmt.base_fontsize * scale * cv.DESIGN_DPI / 72.0


# ----------------------------------------------------------------------
# Field dominance
# ----------------------------------------------------------------------
def _rotated(track: Dict[str, Any], samples: int = 1200) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotated outline resampled evenly in lap fraction -> ``(x, y, fraction)``."""
    frac = np.asarray(track["fraction"], dtype=float)
    x = np.asarray(track["x"], dtype=float)
    y = np.asarray(track["y"], dtype=float)
    f = np.linspace(frac[0], frac[-1], samples)
    x, y = np.interp(f, frac, x), np.interp(f, frac, y)
    theta = np.deg2rad(track.get("rotation") or 0)
    xr = x * np.cos(theta) - y * np.sin(theta)
    yr = x * np.sin(theta) + y * np.cos(theta)
    return xr, yr, f


def _draw_map(ax, payload: Dict[str, Any], lw: float) -> None:
    ax.set_axis_off()
    track = payload.get("track")
    if not track:
        return
    x, y, f = _rotated(track, samples=2400)
    sectors = payload["minisectors"]
    n = len(sectors)
    # Dark under-stroke keeps the line legible where the outline crosses itself.
    ax.plot(x, y, color="#0d0d0d", lw=lw + 5, solid_capstyle="round", solid_joinstyle="round", zorder=1)
    # One polyline per minisector: joins stay clean through corners.
    lines, colours = [], []
    for i, sec in enumerate(sectors):
        lo = sec["start_fraction"] if i else f[0]
        hi = sec["end_fraction"] if i < n - 1 else f[-1]
        pick = (f >= lo) & (f <= hi + (f[1] - f[0]))
        if pick.sum() >= 2:
            lines.append(np.column_stack([x[pick], y[pick]]))
            colours.append(sec["owner_color"])
    ax.add_collection(LineCollection(lines, colors=colours, linewidths=lw, capstyle="butt", joinstyle="round",
                                     zorder=2))
    ax.plot(x[0], y[0], marker="s", color=TEXT, ms=lw * 0.9, markeredgecolor="#0d0d0d", zorder=3)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.margins(0.05)


def _owner_rows(ax, owners: List[Dict[str, Any]], fmt: cv.CanvasFormat, top_count: int, columns: int) -> None:
    """Ranked owners: colour swatch, label, minisector count, proportion bar."""
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    if not owners:
        return
    bbox = ax.get_position()
    w_px, h_px = bbox.width * fmt.width_px, bbox.height * fmt.height_px
    per_col = math.ceil(len(owners) / columns)
    gap_px = 0.07 * w_px if columns > 1 else 0.0
    col_w = (1.0 - (columns - 1) * gap_px / w_px) / columns
    row_px = min(h_px / per_col, _font_px(fmt, 3.9))
    # Text scales down when rows get tight, never below the minimum.
    scale = max(MIN_FONT / 1.2, min(1.0, row_px / _font_px(fmt, 2.7)))
    label_size = fmt.base_fontsize * 1.2 * scale
    count_size = fmt.base_fontsize * 1.75 * scale
    row_h = row_px / h_px

    for i, o in enumerate(owners):
        col, row = divmod(i, per_col)
        x0 = col * (col_w + gap_px / w_px)
        y_top = 1.0 - row * row_h
        yc = y_top - row_h * 0.46
        swatch = 0.42 * row_h * h_px / w_px
        ax.add_patch(_rect(x0, yc - 0.21 * row_h, swatch, 0.42 * row_h, o["color"]))
        ax.text(x0 + swatch * 1.6, yc, o["code"], ha="left", va="center", fontsize=label_size,
                fontweight="bold", color=TEXT)
        ax.text(x0 + col_w, yc, str(o["count"]), ha="right", va="center", fontsize=count_size,
                fontweight="bold", color=TEXT)
        bar_w = col_w * o["count"] / max(top_count, 1)
        ax.add_patch(_rect(x0, y_top - row_h * 0.86, bar_w, row_h * 0.09, o["color"]))


def _rect(x: float, y: float, w: float, h: float, colour: str):
    import matplotlib.patches as mpatches
    return mpatches.Rectangle((x, y), w, h, facecolor=colour, edgecolor="none", zorder=2)


def _dominance_caption(payload: Dict[str, Any]) -> str:
    if payload.get("top_n"):
        what = f"Top {payload['top_n']} " + ("teams" if payload["mode"] == "team" else "drivers")
    else:
        what = "Teams" if payload["mode"] == "team" else "Drivers"
    return f"{what}  ·  {payload['minisector_count']} minisectors"


def render_dominance(payload: Dict[str, Any], fmt_name: Optional[str] = None) -> str:
    """Field dominance map recomposed for ``fmt_name`` (``None`` -> landscape)."""
    fmt = cv.get_format(fmt_name or "landscape")
    info = payload["session_info"]
    session_long = _SESSION_LONG.get(info["session_name"], info["session_name"])
    fig = cv.new_canvas(fmt)
    subtitle = f"{info['year']} {info['event_name']}"
    if fmt.name == "landscape":
        subtitle += f"  ·  {session_long}"
    cv.add_header(fig, fmt, "Field dominance", subtitle)
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    left, bottom, width, height = _content_box(fmt)
    owners = payload["owners"]
    top_count = owners[0]["count"] if owners else 1
    lw = 9.0 if fmt.name == "landscape" else 8.0

    head_h = _font_px(fmt, 1.9) / fmt.height_px      # caption line above the list
    if fmt.name == "landscape":
        map_w = width * 0.62
        col_x = left + map_w + width * 0.04
        col_w = left + width - col_x
        map_rect = (left, bottom, map_w, height)
        list_h = min(height * 0.86 - head_h, len(owners) * _font_px(fmt, 3.9) / fmt.height_px)
        list_top = bottom + height / 2 + (list_h - head_h) / 2      # caption + rows, centred on the map
        list_rect = (col_x, list_top - list_h, col_w, list_h)
        head_xy = (col_x, list_top + 0.008)
        columns = 1
    else:
        many = len(owners) > (4 if fmt.name == "square" else 6)
        columns = 2 if many else 1
        rows = math.ceil(len(owners) / columns)
        row_frac = _font_px(fmt, 3.2) / fmt.height_px
        list_h = min(height * (0.36 if fmt.name == "story" else 0.42), rows * row_frac)
        list_h = max(list_h, rows * _font_px(fmt, 2.2) / fmt.height_px)
        map_rect = (left, bottom + list_h + head_h + 0.03, width, height - list_h - head_h - 0.03)
        list_rect = (left, bottom, width, list_h)
        head_xy = (left, bottom + list_h + 0.012)

    _draw_map(fig.add_axes(map_rect), payload, lw)
    fig.text(head_xy[0], head_xy[1], _dominance_caption(payload).upper() + (
        f"  ·  {session_long.upper()}" if fmt.name != "landscape" else ""),
        ha="left", va="bottom", fontsize=fmt.base_fontsize * 0.8, color=MUTED, fontweight="semibold")
    _owner_rows(fig.add_axes(list_rect), owners, fmt, top_count, columns)

    name = f"Field dominance {payload['mode']}" + (f" top{payload['top_n']}" if payload.get("top_n") else "")
    return cv.save_png(fig, _output_path(info, name, fmt_name), fmt)


# ----------------------------------------------------------------------
# Sector gap to pole
# ----------------------------------------------------------------------
def _fmt_lap(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes}:{seconds - 60 * minutes:06.3f}" if minutes else f"{seconds:.3f}"


def _pole_strip(fig, fmt: cv.CanvasFormat, rect: Tuple[float, float, float, float], pole: Dict[str, Any]) -> None:
    """Pole plate on the left; the three pole sector times (also the colour key) on the right."""
    x0, y0, w, h = rect
    plate_w = w * (0.36 if fmt.name == "landscape" else 0.40)
    cv.add_driver_plate(fig, fmt, (x0, y0, plate_w, h), pole["driver"], pole["color"],
                        lap_time=_fmt_lap(pole["lap_time_s"]), detail="Pole")
    ax = fig.add_axes((x0 + plate_w, y0, w - plate_w, h))
    ax.set_axis_off()
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 1)
    sw = _font_px(fmt, 1.1) / (ax.get_position().width * fmt.width_px) * 3
    for k, (colour, t) in enumerate(zip(SECTOR_COLOURS, pole["sectors"])):
        ax.add_patch(_rect(k + 0.04, 0.5, sw, 0.36, colour))
        ax.text(k + 0.04 + sw * 1.5, 0.68, f"S{k + 1}", ha="left", va="center", color=MUTED,
                fontsize=fmt.base_fontsize * 0.95, fontweight="semibold")
        ax.text(k + 0.04, 0.34, f"{t:.3f}", ha="left", va="center", color=TEXT,
                fontsize=fmt.base_fontsize * 1.15, fontweight="bold")


def render_sector_gap(payload: Dict[str, Any], fmt_name: Optional[str] = None) -> str:
    """Sector-gap-to-pole chart recomposed for ``fmt_name`` (``None`` -> landscape)."""
    fmt = cv.get_format(fmt_name or "landscape")
    info = payload["session_info"]
    session_long = _SESSION_LONG.get(info["session_name"], info["session_name"])
    fig = cv.new_canvas(fmt)
    subtitle = f"{info['year']} {info['event_name']}  ·  {session_long}" if fmt.name != "story" \
        else f"{info['year']} {info['event_name']}"
    cv.add_header(fig, fmt, "Sector gap to pole", subtitle)
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    left, bottom, width, height = _content_box(fmt)
    strip_h = min(height * 0.16, _font_px(fmt, 4.6) / fmt.height_px)
    _pole_strip(fig, fmt, (left, bottom + height - strip_h, width, strip_h), payload["pole"])

    drivers = payload["drivers"]
    n = len(drivers)
    label_px = _font_px(fmt, 5.2)                       # "10 VER": position column + driver code
    x_lab = _font_px(fmt, 3.4) / fmt.height_px          # room for the axis label
    ax_left = left + (label_px + _font_px(fmt, 0.6)) / fmt.width_px
    ax = fig.add_axes((ax_left, bottom + x_lab, left + width - ax_left, height - strip_h - x_lab - 0.02))

    pos_end = np.zeros(n)
    neg_end = np.zeros(n)
    for i, d in enumerate(drivers):
        for k, g in enumerate(d["sector_gaps_s"]):
            if g >= 0:
                ax.barh(i, g, left=pos_end[i], height=0.66, color=SECTOR_COLOURS[k], edgecolor="#0d0d0d",
                        linewidth=1.0, zorder=3)
                pos_end[i] += g
            else:
                ax.barh(i, g, left=neg_end[i], height=0.66, color=SECTOR_COLOURS[k], edgecolor="#0d0d0d",
                        linewidth=1.0, zorder=3)
                neg_end[i] += g

    ax_w_px = ax.get_position().width * fmt.width_px
    total_px = _font_px(fmt, 1.05) * 0.68 * 7
    span = float(max(pos_end.max(), 0.05))
    x_max = span / max(0.35, 1.0 - (total_px + 12) / ax_w_px)
    x_min = float(neg_end.min()) * 1.15 - (0.02 * x_max if neg_end.min() < 0 else 0.0)
    ax.set_xlim(min(x_min, 0.0), x_max)
    ax.set_ylim(n - 0.5, -0.5)
    px_per_s = ax_w_px / (ax.get_xlim()[1] - ax.get_xlim()[0])

    seg_size = fmt.base_fontsize * 0.78
    seg_px = _font_px(fmt, 0.78) * 0.62 * 5 + 10
    for i, d in enumerate(drivers):
        p_cursor = n_cursor = 0.0
        for k, g in enumerate(d["sector_gaps_s"]):
            centre = p_cursor + g / 2 if g >= 0 else n_cursor + g / 2
            if g >= 0:
                p_cursor += g
            else:
                n_cursor += g
            if abs(g) * px_per_s >= seg_px:
                ax.text(centre, i, f"{g:.3f}", ha="center", va="center", zorder=4,
                        fontsize=seg_size, color=_SECTOR_TEXT[k], fontweight="semibold")
        ax.text(pos_end[i] + 6 / px_per_s, i, f"+{d['gap_s']:.3f}", ha="left", va="center", zorder=4,
                fontsize=fmt.base_fontsize * 1.05, color=TEXT, fontweight="bold")
        ax.text(-_font_px(fmt, 0.5) / ax_w_px, i, d["driver"], transform=ax.get_yaxis_transform(),
                ha="right", va="center", fontsize=fmt.base_fontsize * 1.15, color=d["color"], fontweight="bold")
        ax.text(-_font_px(fmt, 4.9) / ax_w_px, i, str(d["position"]), transform=ax.get_yaxis_transform(),
                ha="left", va="center", fontsize=fmt.base_fontsize * 0.85, color=MUTED, fontweight="semibold")

    ax.axvline(0, color="#7a7a7a", lw=1.2, zorder=2)
    cv.style_axis(ax, fmt)
    ax.grid(False, axis="y")
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Gap to pole (s)")

    return cv.save_png(fig, _output_path(info, "Sector gap", fmt_name), fmt)
