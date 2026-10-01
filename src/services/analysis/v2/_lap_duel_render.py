"""
Lap Duel rendering -- presentation only.

Numbers and labels, no sentences: corner numbers, apex minimum speeds, top
speeds, the delta, and each section of the lap tinted in the colour of the
driver who gained time there (stronger tint = bigger gain). The same payload is
recomposed per format rather than cropped:

* landscape / square: telemetry panels on the left, identity + track + the
  three biggest swings in a side column (landscape) or a top band (square)
* portrait / story: plates, track map and a reduced panel stack, top to bottom
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from matplotlib.collections import LineCollection

from src.services.plotting import canvas as cv
from src.services.plotting import output as dirOrg

MUTED = "#8A8A8A"
GRID = "#2a2a2a"

# Panel -> relative height. The speed trace is the hero.
_PANEL_HEIGHTS = {"speed": 3.4, "delta": 1.6, "long_g": 0.9, "lat_g": 0.9,
                  "throttle": 1.0, "brake": 0.6, "gear": 0.8}


def _panels(fmt: cv.CanvasFormat, detail: str) -> List[str]:
    if fmt.name == "story":
        return ["speed", "delta", "throttle", "brake"]
    base = ["speed", "delta"]
    if detail == "full":
        base += ["long_g", "lat_g"]
    base += ["throttle", "brake", "gear"]
    if fmt.name == "square":
        # Too short for a readable gear strip next to the plates.
        base.remove("gear")
    return base


def output_path(payload: Dict[str, Any], fmt_name: Optional[str], variant: str) -> str:
    info = payload["session_info"]
    y, event, session = info["year"], info["event_name"], info["session_name"]
    folder = f"{y}/{event.replace(' ', '')}/{session}"
    dirOrg.checkForFolder(folder)
    a, b = payload["a"]["driverCode"], payload["b"]["driverCode"]
    name = f"Lap duel {a} vs {b} {variant}{cv.format_suffix(fmt_name or 'landscape')}.png"
    return f"outputs/plots/{folder}/{name}"


def _title(payload: Dict[str, Any]) -> Tuple[str, str]:
    a, b = payload["a"], payload["b"]
    title = f"{a['driverCode']} vs {b['driverCode']}"
    if payload.get("same_session", True):
        info = payload["session_info"]
        parts = [f"{info['year']} {info['event_name']}", _session_label(info["session_name"])]
        if a["selection"] == b["selection"] == "fastest":
            parts.append("Fastest laps")
        elif a["segment"] and a["segment"] == b["segment"]:
            parts.append(f"{a['segment']} laps")
        return title, "  ·  ".join(parts)
    sub = (f"{a['year']} {_session_label(a['session'])}  vs  {b['year']} {_session_label(b['session'])}"
           f"  ·  {a['event_name']}")
    return title, sub


def _session_label(code: str) -> str:
    return {"Q": "Qualifying", "R": "Race", "S": "Sprint", "SQ": "Sprint Qualifying",
            "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3"}.get(code, code)


def _plate_detail(side: Dict[str, Any], same_session: bool) -> Optional[str]:
    bits = []
    if side.get("segment"):
        bits.append(side["segment"])
    elif side.get("lap_number"):
        bits.append(f"Lap {side['lap_number']}")
    if not same_session:
        bits.insert(0, str(side["year"]))
    return " ".join(bits) or None


# ----------------------------------------------------------------------
# Panels
# ----------------------------------------------------------------------
def _shade_sections(ax, payload, color_a, color_b):
    sections = [s for s in payload.get("sections", []) if abs(s["delta_change_s"]) >= 0.004]
    if not sections:
        return
    biggest = max(abs(s["delta_change_s"]) for s in sections)
    for s in sections:
        strength = abs(s["delta_change_s"]) / biggest
        color = color_b if s["gainer"] == "b" else color_a
        ax.axvspan(s["start_m"], s["end_m"], color=color, alpha=0.04 + 0.16 * strength, lw=0, zorder=0)


def _tag(ax, fmt: cv.CanvasFormat, text: str, color: str = MUTED, y: float = 0.96, va: str = "top") -> None:
    """Small uppercase panel tag inside the top-left corner, on a dark plate.

    Short panels cannot fit a rotated y label without it overflowing into the
    neighbouring panel, so they carry their name inside instead.
    """
    ax.text(0.006, y, text.upper(), transform=ax.transAxes, ha="left", va=va,
            fontsize=fmt.base_fontsize * 0.7, color=color, fontweight="semibold", zorder=8,
            bbox={"boxstyle": "round,pad=0.25", "fc": "#141414", "ec": "none", "alpha": 0.85})


def _pt_to_px(pt: float) -> float:
    return pt * cv.DESIGN_DPI / 72.0


def _tag_px(fmt: cv.CanvasFormat) -> float:
    """Height of a :func:`_tag` plate in pixels (text + box padding + a little air)."""
    return _pt_to_px(fmt.base_fontsize * 0.7) * 1.55 + 3


def _fit_ylim(ax, fmt: cv.CanvasFormat, lo: float, hi: float, top_px: float = 0.0, bottom_px: float = 0.0):
    """Set y limits so data ``lo..hi`` leaves ``top_px`` / ``bottom_px`` pixels free above / below.

    Labels drawn at a pixel offset from the data (apex speeds under the slowest
    corner, the final gap above the delta line) need room measured in pixels,
    not as a fraction of the data range, or they cross into the next panel.
    """
    height_px = max(ax.get_position().height * fmt.height_px, 1.0)
    a, c = top_px / height_px, bottom_px / height_px
    if a + c > 0.8:  # a panel too short for its labels: keep some of it for the data
        a, c = 0.8 * a / (a + c), 0.8 * c / (a + c)
    span = max(hi - lo, 1e-9) / (1.0 - a - c)
    ax.set_ylim(lo - c * span, hi + a * span)


def _ticks_within(ax, lo: float, hi: float) -> None:
    """Keep only the y ticks inside the data range (the label headroom is not data)."""
    ticks = [t for t in ax.get_yticks() if lo - 1e-9 <= t <= hi + 1e-9]
    if len(ticks) >= 2:
        ax.set_yticks(ticks)


def _gap_ticks(ax, fmt: cv.CanvasFormat, lo: float, hi: float) -> List[float]:
    """Zero plus one round step on each side the gap reaches, if the labels have room."""
    reach = max(abs(lo), abs(hi))
    step = next((s for s in (10, 5, 2.5, 2, 1, 0.5, 0.25, 0.2, 0.1, 0.05) if s <= reach), 0.05)
    y0, y1 = ax.get_ylim()
    px_per_unit = ax.get_position().height * fmt.height_px / max(y1 - y0, 1e-9)
    if step * px_per_unit < 1.3 * _pt_to_px(fmt.base_fontsize * 0.85):
        return [0.0]
    ticks = [0.0]
    if hi >= step:
        ticks.append(step)
    if -lo >= step:
        ticks.insert(0, -step)
    return ticks


def _label_spacing(ax, fmt: cv.CanvasFormat, span: float, chars: int = 3) -> float:
    """Minimum data-units gap between two labels of ``chars`` characters on this axis."""
    width_px = ax.get_position().width * fmt.width_px
    label_px = fmt.base_fontsize * 0.8 * cv.DESIGN_DPI / 72.0 * 0.62 * chars + 6
    return span * label_px / max(width_px, 1.0)


# Apex labels: first line this many points under the slowest trace, then one line per driver.
_APEX_GAP_PT = 5.0
_APEX_LINE = 1.15


def _speed_panel(ax, payload, fmt, color_a, color_b, max_apex_labels: int):
    dist = np.asarray(payload["distance"])
    a, b = payload["a"], payload["b"]
    _shade_sections(ax, payload, color_a, color_b)
    ax.plot(dist, b["speed"], color=color_b, lw=2.0, zorder=3, solid_capstyle="round")
    ax.plot(dist, a["speed"], color=color_a, lw=2.0, zorder=4, solid_capstyle="round")
    ax.set_ylabel("Speed (km/h)")
    vmax = max(max(a["speed"]), max(b["speed"]))
    vmin = min(min(a["speed"]), min(b["speed"]))
    size = fmt.base_fontsize * 0.82
    top_size = fmt.base_fontsize * 0.9
    # Two stacked apex labels under the slowest corner; one top-speed label over the peak.
    _fit_ylim(ax, fmt, vmin, vmax,
              top_px=_pt_to_px(7 + 1.25 * top_size) + 6,
              bottom_px=_pt_to_px(_APEX_GAP_PT + 2 * _APEX_LINE * size) + 6)
    _ticks_within(ax, vmin, vmax)
    ax.set_xlim(dist[0], dist[-1])

    # Corner numbers along the top edge; drop any that would collide.
    min_gap = _label_spacing(ax, fmt, dist[-1] - dist[0], chars=2)
    last = -np.inf
    for c in payload.get("corners", []):
        ax.axvline(c["distance_m"], color="#3a3a3a", lw=0.8, ls=":", zorder=1)
        if c["distance_m"] - last >= min_gap:
            ax.text(c["distance_m"], 1.005, str(c["number"]), transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=fmt.base_fontsize * 0.78, color=MUTED)
            last = c["distance_m"]

    # Apex minimum speeds (slowest first when space is short), stacked A over B.
    apex_gap = _label_spacing(ax, fmt, dist[-1] - dist[0], chars=3)
    placed: List[float] = []
    apexes = []
    for apex in sorted(payload.get("apexes", []), key=lambda x: x["min_speed_a"]):
        if len(apexes) >= max_apex_labels:
            break
        if all(abs(apex["distance_m"] - d) >= apex_gap for d in placed):
            apexes.append(apex)
            placed.append(apex["distance_m"])
    for apex in apexes:
        xy = (apex["distance_m"], min(apex["min_speed_a"], apex["min_speed_b"]))
        for i, (value, color) in enumerate(((apex["min_speed_a"], color_a), (apex["min_speed_b"], color_b))):
            ax.annotate(f"{value:.0f}", xy, xytext=(0, -(_APEX_GAP_PT + i * _APEX_LINE * size)),
                        textcoords="offset points", ha="center", va="top",
                        fontsize=size, color=color, fontweight="bold", zorder=6)

    # Top speeds at the right edge of the frame, where they happened.
    hi = payload["highlights"]["top_speed_kmh"]
    at = payload["highlights"]["top_speed_at_m"]
    peak = max(hi.values())
    span = dist[-1] - dist[0]
    ca, cb = a["driverCode"], b["driverCode"]
    together = abs(at[ca] - at[cb]) < 0.08 * span
    edge = 0.04 * span
    for side, color, dx, ha in ((a, color_a, -4, "right"), (b, color_b, 4, "left")):
        code = side["driverCode"]
        if together:
            # Side by side around the shared spot; keep the pair in frame when the
            # peak is at the line (both labels are about one label-width wide).
            room = _label_spacing(ax, fmt, span, chars=4)
            x = float(np.clip((at[ca] + at[cb]) / 2.0, dist[0] + room, dist[-1] - room))
        else:
            # Each at its own spot; nudge inward so a top speed at the line stays in frame.
            x, dx, ha = float(np.clip(at[code], dist[0] + edge, dist[-1] - edge)), 0, "center"
        ax.annotate(f"{hi[code]:.0f}", (x, peak), xytext=(dx, 7), textcoords="offset points",
                    ha=ha, va="bottom", fontsize=top_size, color=color,
                    fontweight="bold", zorder=6)


def _delta_panel(ax, payload, fmt, color_a, color_b):
    dist = np.asarray(payload["distance"])
    delta = np.asarray(payload["delta"])
    a, b = payload["a"]["driverCode"], payload["b"]["driverCode"]
    ax.axhline(0, color="#5a5a5a", lw=1.0)
    ax.fill_between(dist, delta, 0, where=delta >= 0, color=color_a, alpha=0.28, lw=0, interpolate=True)
    ax.fill_between(dist, delta, 0, where=delta < 0, color=color_b, alpha=0.28, lw=0, interpolate=True)
    ax.plot(dist, delta, color="#E9E9E9", lw=1.6, zorder=3)
    ax.set_ylabel("Gap (s)")
    _tag(ax, fmt, f"{a} ahead", color=color_a)
    _tag(ax, fmt, f"{b} ahead", color=color_b, y=0.04, va="bottom")
    final = delta[-1]
    size = fmt.base_fontsize * 0.9
    # Final gap in the band reserved on its side (top-right when A is ahead), never
    # next to the end of the line: in a race the gap wanders there and ran through it.
    ax.text(0.995, 0.97 if final >= 0 else 0.03, f"{final:+.3f}", transform=ax.transAxes,
            ha="right", va="top" if final >= 0 else "bottom",
            fontsize=size, color="#E9E9E9", fontweight="bold", zorder=6)
    # Room for the final-gap label on its side and for the "ahead" tags in both corners.
    label_px = _pt_to_px(1.25 * size) + 8
    tag_px = _tag_px(fmt)
    lo, hi = min(float(delta.min()), 0.0), max(float(delta.max()), 0.0)
    pad = 0.04 * max(hi - lo, 0.1)
    _fit_ylim(ax, fmt, lo - pad, hi + pad,
              top_px=max(tag_px, label_px if final >= 0 else 0.0),
              bottom_px=max(tag_px, label_px if final < 0 else 0.0))
    ax.set_yticks(_gap_ticks(ax, fmt, lo, hi))


def _series_panel(ax, payload, key, label, color_a, color_b, scale=1.0, step=False, ylim=None, series=None,
                  fmt=None):
    dist = np.asarray(payload["distance"])
    src = series or {"a": payload["a"][key], "b": payload["b"][key]}
    for side, color, z in (("b", color_b, 3), ("a", color_a, 4)):
        values = np.asarray(src[side], dtype=float) * scale
        if step:
            ax.step(dist, values, where="post", color=color, lw=1.6, zorder=z)
        else:
            ax.plot(dist, values, color=color, lw=1.6, zorder=z)
    if fmt is not None:
        _tag(ax, fmt, label, y=0.97)
    lo, hi = ylim if ylim else ax.get_ylim()
    # The tag gets its own band above the data, so no trace ever runs under it.
    if fmt is not None:
        _fit_ylim(ax, fmt, lo, hi, top_px=_tag_px(fmt))
    else:
        ax.set_ylim(lo, hi)


def _brake_panel(ax, payload, color_a, color_b):
    dist = np.asarray(payload["distance"])
    for side, color, offset in (("a", color_a, 0.0), ("b", color_b, -1.15)):
        on = np.asarray(payload[side]["brake"], dtype=float)
        ax.fill_between(dist, offset, offset + on * 0.9, step="post", color=color, alpha=0.85, lw=0)
    ax.set_ylim(-1.3, 1.05)
    ax.set_yticks([0.45, -0.7])
    ax.set_yticklabels([payload["a"]["driverCode"], payload["b"]["driverCode"]])
    ax.grid(False)


def _track_map(ax, payload, color_a, color_b):
    track = payload.get("track")
    ax.set_axis_off()
    if not track:
        return
    x = np.asarray(track["x"], dtype=float)
    y = np.asarray(track["y"], dtype=float)
    theta = np.deg2rad(track.get("rotation") or 0)
    xr = x * np.cos(theta) - y * np.sin(theta)
    yr = x * np.sin(theta) + y * np.cos(theta)
    pts = np.column_stack([xr, yr]).reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    colors = [color_a if f == "a" else color_b for f in track["faster"][:-1]]
    ax.add_collection(LineCollection(segs, colors=colors, linewidths=5.5, capstyle="round", zorder=2))
    ax.plot(xr[0], yr[0], marker="s", color="#E9E9E9", ms=6, zorder=3)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.margins(0.06)


def _swings(ax, payload, fmt, color_a, color_b):
    ax.set_axis_off()
    swings = payload["highlights"]["biggest_swings"]
    a = payload["a"]["driverCode"]
    size = fmt.base_fontsize * 0.95
    for i, s in enumerate(swings):
        y = 0.82 - i * 0.3
        color = color_a if s["gainer"] == a else color_b
        ax.text(0.02, y, s["where"], transform=ax.transAxes, ha="left", va="center",
                fontsize=size, color="#E9E9E9", fontweight="bold")
        ax.text(0.98, y, f"{s['gainer']} −{s['seconds']:.3f}s", transform=ax.transAxes, ha="right",
                va="center", fontsize=size, color=color, fontweight="bold")


# ----------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------
def render(payload: Dict[str, Any], fmt_name: Optional[str] = None, hero: bool = False,
           variant: str = "best") -> str:
    fmt = cv.get_format(fmt_name or "landscape")
    fig = cv.new_canvas(fmt)
    color_a = payload["a"]["color"]
    color_b = payload["b"]["color"]
    detail = payload.get("detail", "standard")
    panels = _panels(fmt, detail)
    same_session = payload.get("same_session", True)

    title, subtitle = _title(payload)
    cv.add_header(fig, fmt, title, subtitle)
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    heights = [_PANEL_HEIGHTS[p] for p in panels]
    side_rows: List[str] = []
    if fmt.name == "landscape":
        gs = cv.safe_gridspec(fig, fmt, len(panels), 2, top_labels=True, width_ratios=[3.7, 1.0],
                              height_ratios=heights, hspace=0.14, wspace=0.07)
        axes_specs = [gs[i, 0] for i in range(len(panels))]
        side = gs[:, 1].subgridspec(3, 1, height_ratios=[1.05, 2.0, 1.1], hspace=0.12)
        plate_spec, map_spec, swing_spec = side[0], side[1], side[2]
        side_rows = ["plates", "map", "swings"]
    else:
        top_rows = {"square": [("plates", 1.35)],
                    "portrait": [("plates", 0.95), ("map", 1.3)],
                    "story": [("plates", 0.95), ("map", 2.1)]}[fmt.name]
        heights = [h for _, h in top_rows] + heights
        gs = cv.safe_gridspec(fig, fmt, len(heights), 1, height_ratios=heights, hspace=0.18)
        n_top = len(top_rows)
        axes_specs = [gs[n_top + i] for i in range(len(panels))]
        plate_spec = gs[0]
        map_spec = gs[1] if n_top > 1 else None
        swing_spec = None
        side_rows = [name for name, _ in top_rows]

    # Driver plates.
    box = plate_spec.get_position(fig)
    if fmt.name == "landscape":
        h = box.height * 0.46
        rects = [(box.x0, box.y0 + box.height * 0.52, box.width, h), (box.x0, box.y0, box.width, h)]
    else:
        # Corner numbers are drawn just above the first panel: keep the plates clear of them.
        clear = (_pt_to_px(fmt.base_fontsize * 0.78) * 1.4 / fmt.height_px) if map_spec is None else 0.0
        w = box.width * 0.49
        h = box.height - clear
        rects = [(box.x0, box.y0 + clear, w, h), (box.x0 + box.width * 0.51, box.y0 + clear, w, h)]
    for rect, side_key in zip(rects, ("a", "b")):
        side = payload[side_key]
        cv.add_driver_plate(fig, fmt, rect, side["driverCode"], side["color"], lap_time=side["lapTime"],
                            detail=_plate_detail(side, same_session), hero=hero)

    if map_spec is not None and "map" in side_rows:
        _track_map(fig.add_subplot(map_spec), payload, color_a, color_b)
    if swing_spec is not None:
        _swings(fig.add_subplot(swing_spec), payload, fmt, color_a, color_b)

    axes = []
    for i, (name, spec) in enumerate(zip(panels, axes_specs)):
        ax = fig.add_subplot(spec, sharex=axes[0] if axes else None)
        axes.append(ax)
        if name == "speed":
            _speed_panel(ax, payload, fmt, color_a, color_b, max_apex_labels=5 if fmt.is_vertical else 12)
        elif name == "delta":
            _delta_panel(ax, payload, fmt, color_a, color_b)
        elif name == "throttle":
            _series_panel(ax, payload, "throttle", "Throttle %", color_a, color_b, scale=100, ylim=(-5, 104),
                          fmt=fmt)
            ax.set_yticks([0, 100])
        elif name == "brake":
            _brake_panel(ax, payload, color_a, color_b)
        elif name == "gear":
            _series_panel(ax, payload, "gear", "Gear", color_a, color_b, step=True, ylim=(0.5, 8.5), fmt=fmt)
            ax.set_yticks([2, 8])
        elif name in ("long_g", "lat_g"):
            acc = payload["accelerations"]
            label = "Long g" if name == "long_g" else "Lat g"
            _series_panel(ax, payload, name, label, color_a, color_b,
                          series={"a": acc["a"][name], "b": acc["b"][name]}, fmt=fmt)
            ax.axhline(0, color="#5a5a5a", lw=0.8)
        cv.style_axis(ax, fmt)
        if name == "brake":
            ax.grid(False)
            ax.tick_params(axis="y", labelsize=fmt.base_fontsize * 0.72, length=0)
        if i < len(panels) - 1:
            ax.tick_params(labelbottom=False)
    axes[-1].set_xlabel("Lap distance (m)")
    fig.align_ylabels([ax for ax, name in zip(axes, panels) if name in ("speed", "delta")])

    if any(p in panels for p in ("long_g", "lat_g")):
        left, bottom, right, _ = fmt.safe
        cv.pin(fig.text(right, bottom + 0.022 * (1 if fmt.is_vertical else 1.4), "g derived from ~4 Hz data",
                        ha="right", va="bottom", fontsize=fmt.base_fontsize * 0.7, color=MUTED))

    return cv.save_png(fig, output_path(payload, fmt_name, variant), fmt)
