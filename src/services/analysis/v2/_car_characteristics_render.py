"""
Car characteristics rendering -- presentation only.

Numbers and labels, no sentences.

* Corner speed profile: one ranked dot chart per corner class (slow / medium /
  fast) with the corner numbers as a muted tag, each team's mean apex speed and
  its gap to the class best. Landscape and square put the classes side by side;
  portrait and story stack them.
* Efficiency scatter: top speed against mean apex speed, team-coloured dots
  labelled with the team, a dashed field-median crosshair and the quadrant
  names as small corner tags.
"""
from __future__ import annotations

import textwrap
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from matplotlib import patheffects

from src.services.analysis.v2._lap_duel_render import _session_label
from src.services.plotting import canvas as cv
from src.services.plotting import output as dirOrg

MUTED = cv.MUTED
TEXT = cv.TEXT
MINUS = "−"

_CLASS_TITLE = {"slow": "SLOW", "medium": "MEDIUM", "fast": "FAST"}


def output_path(payload: Dict[str, Any], name: str, fmt_name: Optional[str]) -> str:
    info = payload["session_info"]
    folder = f"{info['year']}/{str(info['event_name']).replace(' ', '')}/{info['session_name']}"
    dirOrg.checkForFolder(folder)
    return f"outputs/plots/{folder}/{name}{cv.format_suffix(fmt_name or 'landscape')}.png"


def _subtitle(payload: Dict[str, Any]) -> str:
    info = payload["session_info"]
    parts = [f"{info['year']} {info['event_name']}", _session_label(info["session_name"]), "Best lap per team"]
    return "  ·  ".join(parts)


def _pt_to_fig(pt: float, size_px: int) -> float:
    return pt * cv.DESIGN_DPI / 72.0 / size_px


def _fmt_delta(delta: float) -> str:
    if delta > -0.05:
        return "BEST"
    return f"{MINUS}{abs(delta):.1f}"


# ----------------------------------------------------------------------
# Corner speed profile
# ----------------------------------------------------------------------
def _tag_lines(numbers: List[int], width_px: float, font_pt: float) -> List[str]:
    """Corner numbers ("T8 T15 T16") wrapped to the panel width."""
    if not numbers:
        return []
    char_px = 0.66 * font_pt * cv.DESIGN_DPI / 72.0
    chars = max(8, int(width_px / char_px))
    return textwrap.wrap("  ".join(f"T{n}" for n in numbers), width=chars) or []


def _compact(name: str, limit: int = 8) -> str:
    """A name that fits a narrow name column ("Aston Martin" -> "Aston")."""
    return name if len(name) <= limit else name.split()[0]


def _columns(panel_w_px: float, row_pt: float, longest: int) -> Tuple[float, float, float]:
    """Axes-fraction x of ``(track_start, track_end, value_right)`` for one panel.

    The name column, value column and delta column take what their text needs; the dots get the rest.
    """
    em = row_pt * cv.DESIGN_DPI / 72.0
    name_w = longest * 0.6 * em + 10
    num_w = 5.0 * 0.66 * em
    delta_w = 5.0 * 0.62 * em
    value_right = 1.0 - delta_w / panel_w_px - 0.012
    track_start = name_w / panel_w_px + 0.02
    track_end = value_right - num_w / panel_w_px - 0.02
    return track_start, track_end, value_right


def _limit_text(cls: str, rules: Dict[str, Any]) -> str:
    bounds = rules[cls]
    if cls == "fast":
        return f"> {bounds['min_kmh']:.0f} km/h"
    if "min_kmh" in bounds:
        return f"{bounds['min_kmh']:.0f}–{bounds['max_kmh']:.0f} km/h"
    return f"< {bounds['max_kmh']:.0f} km/h"


def _draw_class(ax, cls: str, data: Dict[str, Any], rules: Dict[str, Any], row_pt: float,
                tag_lines: List[str], title_pt: float, tag_pt: float, panel_w_px: float,
                max_tag_lines: int, compact: bool, show_limit: bool,
                tag_x_pt: Optional[float] = None) -> None:
    rows = data["teams"]
    n = len(rows)
    names = [_compact(r["short"]) if compact else r["short"] for r in rows]
    u0, u1, value_right = _columns(panel_w_px, row_pt, max(len(x) for x in names))
    avg = np.array([r["avg_kmh"] for r in rows], dtype=float)
    lo, hi = float(avg.min()), float(avg.max())
    span = max(hi - lo, 4.0)
    inset = 0.05 * (u1 - u0)
    per_u = span / max((u1 - inset) - (u0 + inset), 1e-6)     # km/h per axes-fraction unit

    def x_at(u: float) -> float:
        return lo + (u - (u0 + inset)) * per_u

    ax.set_xlim(x_at(0.0), x_at(1.0))
    ax.set_ylim(n - 0.4, -0.75)
    tr = ax.get_yaxis_transform()
    dot_pt = row_pt * 1.1
    for y, r, name in zip(range(n), rows, names):
        best = r["delta_kmh"] > -0.05
        ax.plot([x_at(u0), r["avg_kmh"]], [y, y], color=r["color"], lw=2.2, alpha=0.32,
                solid_capstyle="round", zorder=2)
        ax.plot(r["avg_kmh"], y, "o", color=r["color"], ms=dot_pt, mec="#0d0d0d", mew=1.2, zorder=4)
        ax.text(0.0, y, name, transform=tr, ha="left", va="center", fontsize=row_pt, color=TEXT)
        ax.text(value_right, y, f"{r['avg_kmh']:.1f}", transform=tr, ha="right", va="center",
                fontsize=row_pt, color=TEXT, fontweight="bold")
        ax.text(1.0, y, _fmt_delta(r["delta_kmh"]), transform=tr, ha="right", va="center",
                fontsize=row_pt * (0.9 if best else 1.0), color=r["color"] if best else MUTED,
                fontweight="bold" if best else "normal")
    ax.set_yticks([])
    ax.set_xticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Title above the tag lines above the panel (same height on every panel), or -- when ``tag_x_pt`` is
    # given -- the tags sit to the right of the title on the same rows (stacked layouts are short of height).
    line_pt = 1.3 * tag_pt
    if tag_x_pt is None:
        tag_h = max_tag_lines * line_pt
        title_y, x_off = 9 + tag_h, 0.0
    else:
        title_y, x_off = 5.0, tag_x_pt
    for i, line in enumerate(reversed(tag_lines)):
        ax.annotate(line, (0, 1), xycoords="axes fraction", xytext=(x_off, 5 + i * line_pt),
                    textcoords="offset points", ha="left", va="bottom", fontsize=tag_pt, color=MUTED,
                    fontweight="semibold", annotation_clip=False)
    ax.annotate(_CLASS_TITLE[cls], (0, 1), xycoords="axes fraction", xytext=(0, title_y),
                textcoords="offset points", ha="left", va="bottom", fontsize=title_pt, color=TEXT,
                fontweight="bold", annotation_clip=False)
    if show_limit:
        top_line = 5 + (max(len(tag_lines) - 1, 0) * line_pt if tag_x_pt is not None else 0)
        ax.annotate(_limit_text(cls, rules), (1, 1), xycoords="axes fraction",
                    xytext=(0, top_line if tag_x_pt is not None else title_y),
                    textcoords="offset points", ha="right", va="bottom", fontsize=tag_pt, color=MUTED,
                    annotation_clip=False)


def render_profile(payload: Dict[str, Any], fmt_name: Optional[str] = None) -> str:
    fmt = cv.get_format(fmt_name or "landscape")
    fig = cv.new_canvas(fmt)
    cv.add_header(fig, fmt, "Corner speed profile", _subtitle(payload))
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    classes = [c for c in ("slow", "medium", "fast") if payload["classes"][c]["teams"]] or ["slow"]
    n_teams = max((len(payload["classes"][c]["teams"]) for c in classes), default=1) or 1
    side_by_side = fmt.name in ("landscape", "square")
    ncols, nrows = (len(classes), 1) if side_by_side else (1, len(classes))

    base = fmt.base_fontsize
    title_pt = base * 1.15
    tag_pt = base * 0.72
    gs = cv.safe_gridspec(fig, fmt, nrows, ncols, hspace=0.0, wspace=0.0)
    # No y-axis labels (names live inside each panel) and no x label: only the footer line needs clearing.
    footer_px = 0.8 * base * cv.DESIGN_DPI / 72.0 * 1.3 + 24.0
    gs.update(left=fmt.safe[0], bottom=fmt.safe[1] + footer_px / fmt.height_px)
    gutter_px = 0.03 * fmt.width_px
    total_w_px = fmt.width_px * (gs.right - gs.left)
    panel_w_px = (total_w_px - gutter_px * (ncols - 1)) / ncols if side_by_side else total_w_px
    if side_by_side:
        gs.update(wspace=gutter_px / panel_w_px)

    px_per_pt = cv.DESIGN_DPI / 72.0
    show_limit = panel_w_px >= 480               # narrower panels carry the limit as their first tag line
    if side_by_side:
        tag_x_pt = None
        tags = {c: _tag_lines(payload["classes"][c]["corners"], panel_w_px, tag_pt) for c in classes}
    else:
        # Tags share the title's rows: wrap them into the width between the title and the limit label.
        title_w_pt = 6 * 0.8 * title_pt
        tag_x_pt = title_w_pt + 14.0
        room = panel_w_px - (tag_x_pt + 12 * 0.55 * tag_pt + 14.0) * px_per_pt
        tags = {c: _tag_lines(payload["classes"][c]["corners"], room, tag_pt) for c in classes}
    if not show_limit:
        tags = {c: [_limit_text(c, payload["rules"])] + t for c, t in tags.items()}
    max_tag_lines = max((len(t) for t in tags.values()), default=0)
    if side_by_side:
        head_pt = 9 + max_tag_lines * 1.3 * tag_pt + title_pt * 1.3 + 4
    else:
        head_pt = 5 + max(title_pt * 1.3, max_tag_lines * 1.3 * tag_pt) + 8
    head = _pt_to_fig(head_pt, fmt.height_px)

    if side_by_side:
        gs.update(top=gs.top - head)
        avail_h_px = fmt.height_px * (gs.top - gs.bottom)
    else:
        # Every stacked panel carries its own headroom, so it comes out of the row height.
        avail_h_px = fmt.height_px * (gs.top - gs.bottom) / nrows - head_pt * cv.DESIGN_DPI / 72.0

    # Row type size: as large as the row pitch allows, never below 0.72x base; shrink or shorten the
    # names until the dots keep at least a fifth of the panel.
    row_px = avail_h_px / (n_teams + 0.15)
    row_pt = min(base * 0.95, max(base * 0.72, row_px * 72.0 / cv.DESIGN_DPI * 0.62))
    longest = max(len(r["short"]) for c in classes for r in payload["classes"][c]["teams"] or [{"short": ""}])
    compact = False
    while True:
        u0, u1, _ = _columns(panel_w_px, row_pt, min(longest, 8) if compact else longest)
        if u1 - u0 >= 0.2:
            break
        if row_pt > base * 0.72:
            row_pt = max(base * 0.72, row_pt - 0.5)
        elif not compact:
            compact = True
        else:
            break

    axes = []
    if side_by_side:
        axes = [fig.add_subplot(gs[0, i]) for i in range(len(classes))]
    else:
        # Stacked: place manually so each panel has its own headroom and equal row pitch.
        block = (gs.top - gs.bottom) / nrows
        for i in range(len(classes)):
            block_top = gs.top - i * block
            ax_top = block_top - head
            ax_bottom = block_top - block + 0.004
            axes.append(fig.add_axes((gs.left, ax_bottom, gs.right - gs.left, ax_top - ax_bottom)))

    for ax, cls in zip(axes, classes):
        ax.set_facecolor("#111111")
        _draw_class(ax, cls, payload["classes"][cls], payload["rules"], row_pt, tags[cls], title_pt, tag_pt,
                    panel_w_px, max_tag_lines, compact, show_limit, tag_x_pt)

    return cv.save_png(fig, output_path(payload, "Corner speed profile", fmt_name), fmt)


# ----------------------------------------------------------------------
# Efficiency scatter
# ----------------------------------------------------------------------
def _overlap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _place_labels(ax, points_px: np.ndarray, sizes: List[Tuple[float, float]], radius_px: float,
                  bounds: Tuple[float, float, float, float]) -> List[Tuple[float, float, str, str]]:
    """Pick a label offset around each dot that avoids other labels, dots and the axes edge.

    Returns ``(dx_px, dy_px, ha, va)`` per point. Greedy, densest points first.
    """
    n = len(points_px)
    gap = 5.0
    dots = [(p[0] - radius_px, p[1] - radius_px, p[0] + radius_px, p[1] + radius_px) for p in points_px]
    density = [sum(1 for j in range(n) if j != i and np.hypot(*(points_px[i] - points_px[j])) < 160)
               for i in range(n)]
    order = sorted(range(n), key=lambda i: -density[i])
    placed: List[Tuple[float, float, float, float]] = []
    out: List[Optional[Tuple[float, float, str, str]]] = [None] * n
    r = radius_px + gap
    for i in order:
        w, h = sizes[i]
        px, py = points_px[i]
        cands = [
            (r, -h / 2, "left", "bottom", 0.0), (-r, -h / 2, "right", "bottom", 0.1),
            (-w / 2, r, "center", "bottom", 0.2), (-w / 2, -r - h, "center", "top", 0.3),
            (r * 0.8, r * 0.8, "left", "bottom", 0.4), (-r * 0.8, r * 0.8, "right", "bottom", 0.5),
            (r * 0.8, -r * 0.8 - h, "left", "bottom", 0.6), (-r * 0.8, -r * 0.8 - h, "right", "bottom", 0.7),
            (r, r + h * 0.4, "left", "bottom", 0.9), (r, -r - h * 1.4, "left", "bottom", 0.9),
            (-r, r + h * 0.4, "right", "bottom", 1.0), (-r, -r - h * 1.4, "right", "bottom", 1.0),
        ]
        best = None
        for dx, dy, ha, va, pref in cands:
            x0 = px + dx - (w if ha == "right" else (w / 2 if ha == "center" else 0.0))
            y0 = py + dy
            box = (x0, y0, x0 + w, y0 + h)
            score = pref
            score += sum(_overlap(box, b) for b in placed) * 4.0
            score += sum(_overlap(box, d) for j, d in enumerate(dots) if j != i) * 6.0
            outside = max(0.0, bounds[0] - box[0]) + max(0.0, box[2] - bounds[2]) \
                + max(0.0, bounds[1] - box[1]) + max(0.0, box[3] - bounds[3])
            score += outside * h * 8.0
            if best is None or score < best[0]:
                best = (score, box, (dx, dy, ha, va))
        placed.append(best[1])
        out[i] = best[2]
    return out  # type: ignore[return-value]


def render_efficiency(payload: Dict[str, Any], fmt_name: Optional[str] = None) -> str:
    fmt = cv.get_format(fmt_name or "landscape")
    fig = cv.new_canvas(fmt)
    cv.add_header(fig, fmt, "Top speed vs apex speed", _subtitle(payload))
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    gs = cv.safe_gridspec(fig, fmt, 1, 1)
    ax = fig.add_subplot(gs[0, 0])
    teams = payload["teams"]
    xs = np.array([t["top_speed_kmh"] for t in teams], dtype=float)
    ys = np.array([t["avg_apex_kmh"] for t in teams], dtype=float)
    med = payload["field_median"]

    def limits(v: np.ndarray, m: float) -> Tuple[float, float]:
        lo, hi = float(min(v.min(), m)), float(max(v.max(), m))
        pad = max(hi - lo, 4.0) * 0.16
        return lo - pad, hi + pad

    ax.set_xlim(*limits(xs, med["x"]))
    ax.set_ylim(*limits(ys, med["y"]))
    cv.style_axis(ax, fmt)
    ax.set_xlabel("Top speed (km/h)")
    ax.set_ylabel("Avg apex speed (km/h)")
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#3a3a3a")

    ax.axvline(med["x"], color=MUTED, lw=1.2, ls=(0, (5, 4)), alpha=0.7, zorder=1)
    ax.axhline(med["y"], color=MUTED, lw=1.2, ls=(0, (5, 4)), alpha=0.7, zorder=1)

    dot_pt = fmt.base_fontsize * (1.35 if not fmt.is_vertical else 1.25)
    ax.scatter(xs, ys, s=dot_pt ** 2, c=[t["color"] for t in teams], edgecolors="#0d0d0d", linewidths=1.4,
               zorder=4)

    # Quadrant names, muted, in three corners.
    tag_pt = fmt.base_fontsize * 0.78
    for label, x, y, ha, va in (("HIGH DOWNFORCE", 0.012, 0.985, "left", "top"),
                                ("EFFICIENT", 0.988, 0.985, "right", "top"),
                                ("LOW DRAG", 0.988, 0.015, "right", "bottom")):
        ax.text(x, y, label, transform=ax.transAxes, ha=ha, va=va, fontsize=tag_pt, color=MUTED,
                fontweight="semibold", zorder=2)

    # Team labels, nudged clear of each other.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    label_pt = fmt.base_fontsize * (0.95 if not fmt.is_vertical else 0.85)
    sizes = []
    for t in teams:
        probe = ax.text(0, 0, t["short"], fontsize=label_pt, fontweight="bold")
        bb = probe.get_window_extent(renderer)
        sizes.append((bb.width, bb.height))
        probe.remove()
    points_px = ax.transData.transform(np.column_stack([xs, ys]))
    axbb = ax.get_window_extent(renderer)
    bounds = (axbb.x0 + 4, axbb.y0 + 4, axbb.x1 - 4, axbb.y1 - 4)
    radius_px = dot_pt * cv.DESIGN_DPI / 72.0 / 2.0
    for t, (dx, dy, ha, va) in zip(teams, _place_labels(ax, points_px, sizes, radius_px, bounds)):
        ax.annotate(t["short"], (t["top_speed_kmh"], t["avg_apex_kmh"]), xytext=(dx, dy),
                    textcoords="offset pixels", ha=ha, va="bottom",
                    fontsize=label_pt, color=t["color"], fontweight="bold", zorder=6,
                    path_effects=[patheffects.withStroke(linewidth=3.5, foreground="#141414")],
                    # Opaque plate so a median line never runs through a name.
                    bbox={"boxstyle": "square,pad=0.1", "fc": ax.get_facecolor(), "ec": "none"})

    return cv.save_png(fig, output_path(payload, "Efficiency scatter", fmt_name), fmt)
