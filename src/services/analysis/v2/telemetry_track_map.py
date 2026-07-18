"""
Telemetry Track Map (V2, livetiming-backed).

A single driver's fastest lap rendered as a colored racing line over the
circuit outline: speed (continuous, ``plasma``) or gear (discrete 8-color
palette). Braking zones are drawn as a wider red under-line so hard-braking
areas jump out even at a glance. Callout arrows mark the top-speed point and
the slowest corner; a start/finish dot anchors the map.

Data flow: ``get_fastest_lap_telemetry`` (``_helpers``) resolves the driver's
personal-best lap window and returns Time/Distance/X/Y + requested channels
in one merged frame. ``get_circuit_info_for_session`` optionally supplies a
rotation angle (applied to X/Y before plotting) — its absence never blocks
rendering, it just means an unrotated map.

Any session (practice/qualifying/race all have CarData + Position streams).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.colors import BoundaryNorm, ListedColormap

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import (
    format_lap_time,
    get_all_driver_codes,
    get_circuit_info_for_session,
    get_fastest_lap_telemetry,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "track_map"

_VALID_COLOR_BY = {"speed", "gear"}
_CHANNELS = {"Speed": "2", "Brake": "5", "Gear": "45"}
_BRAKE_THRESHOLD = 0.0  # Brake channel varies 0-100 or boolean by year -> ">0".
_MAX_MAP_POINTS = 800

# 8-color discrete gear palette (index 0 unused; gears run 1-8).
_GEAR_COLORS = [
    "#4B0082", "#0057E7", "#00A3E0", "#2CA02C",
    "#FFD700", "#FF8C00", "#D62728", "#8B0000",
]


def _init(y: int, event_name: str, session_name: str, tla: str, color_by: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Track map {color_by} {tla} {y} {event_name} {session_name}.png"
    return location, name


def _data_type(tla: str, color_by: str) -> str:
    return f"track_map_{color_by}_{tla.upper()}"


def _assert_valid_color_by(color_by: str, year: int, identifier: Any, session: str) -> None:
    if color_by not in _VALID_COLOR_BY:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session, source="livetiming",
            reason=f"color_by must be one of {sorted(_VALID_COLOR_BY)} (got {color_by!r})",
        )


def _resolve_driver_num(
    driver_codes: Dict[str, str], driver_tla: str,
    year: int, identifier: Any, session: str,
) -> str:
    wanted = driver_tla.strip().upper()
    for num, tla in driver_codes.items():
        if str(tla).upper() == wanted:
            return num
    valid = sorted({str(tla) for tla in driver_codes.values() if tla})
    raise DataNotAvailableError(
        year=year, gp=identifier, session=session, source="livetiming",
        reason=f"Unknown driver {driver_tla!r}. Valid drivers: {', '.join(valid)}",
    )


# ----------------------------------------------------------------------
# Pure builders (unit-testable without a live store)
# ----------------------------------------------------------------------
def _rotate_xy(x: np.ndarray, y: np.ndarray, rotation_deg: float) -> tuple:
    """Rotate X/Y arrays about the origin by ``rotation_deg`` degrees."""
    if not rotation_deg:
        return x, y
    theta = np.deg2rad(rotation_deg)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    x_rot = x * cos_t - y * sin_t
    y_rot = x * sin_t + y * cos_t
    return x_rot, y_rot


def _downsample_map_points(df, max_points: int = _MAX_MAP_POINTS):
    """Evenly downsample a distance-ordered frame to at most ``max_points`` rows.

    Always keeps the first and last row so the loop closes cleanly. A no-op
    when the frame already has <= ``max_points`` rows.
    """
    n = len(df)
    if n <= max_points:
        return df
    idx = np.linspace(0, n - 1, max_points).round().astype(int)
    idx = sorted(set(idx.tolist()))
    if idx[0] != 0:
        idx.insert(0, 0)
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return df.iloc[idx]


def _braking_segments(df, threshold: float = _BRAKE_THRESHOLD) -> List[Dict[str, Any]]:
    """Contiguous braking-zone segments (square-wave style) from a Brake column.

    Returns ``[{start_idx, end_idx, start_distance, end_distance}, ...]`` for
    every run of consecutive rows where ``Brake > threshold``. Indices are
    positional (0-based) into the (already distance-sorted) frame.
    """
    if df is None or len(df) == 0 or 'Brake' not in df.columns:
        return []
    brake = (df['Brake'].to_numpy(dtype=float) > threshold)
    distances = df['Distance'].to_numpy() if 'Distance' in df.columns else np.arange(len(df))

    segments: List[Dict[str, Any]] = []
    start = None
    for i, on in enumerate(brake):
        if on and start is None:
            start = i
        elif not on and start is not None:
            segments.append({
                "start_idx": start, "end_idx": i - 1,
                "start_distance": float(distances[start]),
                "end_distance": float(distances[i - 1]),
            })
            start = None
    if start is not None:
        segments.append({
            "start_idx": start, "end_idx": len(brake) - 1,
            "start_distance": float(distances[start]),
            "end_distance": float(distances[len(brake) - 1]),
        })
    return segments


def _find_callouts(df) -> Dict[str, Optional[Dict[str, Any]]]:
    """Top-speed point and slowest-corner point from a Speed/Distance/X/Y frame.

    Returns ``{"top_speed": {...} | None, "slowest_corner": {...} | None}``,
    each a dict with ``distance``, ``speed``, ``x``, ``y`` (when the frame has
    those columns), or ``None`` if the frame is empty / lacks ``Speed``.
    """
    if df is None or len(df) == 0 or 'Speed' not in df.columns:
        return {"top_speed": None, "slowest_corner": None}

    def _row_to_callout(row) -> Dict[str, Any]:
        return {
            "distance": float(row.get('Distance', 0.0)),
            "speed": float(row.get('Speed', 0.0)),
            "x": float(row.get('X', 0.0)) if 'X' in df.columns else None,
            "y": float(row.get('Y', 0.0)) if 'Y' in df.columns else None,
        }

    top_row = df.loc[df['Speed'].idxmax()]
    slow_row = df.loc[df['Speed'].idxmin()]
    return {
        "top_speed": _row_to_callout(top_row),
        "slowest_corner": _row_to_callout(slow_row),
    }


def build_payload_from_parts(
    df, tla: str, color_by: str, circuit_info: Optional[Dict[str, Any]],
    lap_time_s: float, y: int, event_name: str, session: str,
) -> Dict[str, Any]:
    """Assemble the JSON payload from an already-fetched telemetry frame (test seam)."""
    frame = df.sort_values('Distance').reset_index(drop=True) if not df.empty else df
    rotation = circuit_info.get('rotation', 0) if circuit_info else 0

    points: List[Dict[str, Any]] = []
    if not frame.empty:
        ds = _downsample_map_points(frame, _MAX_MAP_POINTS)
        xs = ds['X'].to_numpy(dtype=float) if 'X' in ds.columns else np.zeros(len(ds))
        ys = ds['Y'].to_numpy(dtype=float) if 'Y' in ds.columns else np.zeros(len(ds))
        xs, ys = _rotate_xy(xs, ys, rotation)
        for i, (_, row) in enumerate(ds.iterrows()):
            points.append({
                "distance": float(row.get('Distance', 0.0)),
                "x": float(xs[i]),
                "y": float(ys[i]),
                "speed": float(row.get('Speed', 0.0)) if 'Speed' in ds.columns else None,
                "gear": int(row.get('Gear', 0)) if 'Gear' in ds.columns else None,
                "brake": float(row.get('Brake', 0.0)) if 'Brake' in ds.columns else None,
            })

    braking = _braking_segments(frame) if not frame.empty else []
    callouts = _find_callouts(frame) if not frame.empty else {"top_speed": None, "slowest_corner": None}

    return {
        "driver": tla,
        "color_by": color_by,
        "lap_time_s": lap_time_s,
        "points": points,
        "braking_segments": braking,
        "callouts": callouts,
        "circuit": circuit_info,
        "session_info": {"year": y, "event_name": event_name, "session_name": session},
    }


def _build_payload(
    store: SessionDataStore, tla: str, color_by: str,
    year: int, identifier: Any, session: str,
) -> Dict[str, Any]:
    client = store.client
    base_url = store.base_url
    driver_codes = get_all_driver_codes(base_url, client)
    driver_num = _resolve_driver_num(driver_codes, tla, year, identifier, session)

    channels = list(_CHANNELS.values())
    df = get_fastest_lap_telemetry(base_url, client, driver_num, channels=channels)
    if df.empty:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session, source="livetiming",
            reason=f"No fastest-lap telemetry available for {tla.upper()}",
        )

    lap_time_s = float(df['LapTime'].iloc[0]) if 'LapTime' in df.columns else 0.0
    circuit_info = get_circuit_info_for_session(store)

    return build_payload_from_parts(
        df, tla.upper(), color_by, circuit_info, lap_time_s,
        year, store.event_name, session,
    )


class TrackMapData:
    """Callable: ``TrackMapData()(year, identifier, session, driver, color_by) -> dict``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        driver: str, color_by: str = "speed",
    ) -> Dict[str, Any]:
        color_by = (color_by or "speed").strip().lower()
        _assert_valid_color_by(color_by, y, identifier, e)
        tla = driver.strip().upper()

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, tla, color_by, y, identifier, e)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=_data_type(tla, color_by), generator=_generate, version="v2",
        )


class TrackMapPlot:
    """Callable: ``TrackMapPlot()(year, identifier, session, driver, color_by) -> png path``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        driver: str, color_by: str = "speed",
    ) -> str:
        color_by = (color_by or "speed").strip().lower()
        _assert_valid_color_by(color_by, y, identifier, e)
        tla = driver.strip().upper()

        payload = TrackMapData()(y, identifier, e, tla, color_by)
        if not payload.get("points"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason=f"No telemetry points available to plot for {tla}",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        return self._render(payload, y, event_name, e)

    @staticmethod
    def _render(payload: Dict[str, Any], y: int, event_name: str, e: str) -> str:
        setup_theme.setup_turnone_theme()
        tla = payload["driver"]
        color_by = payload["color_by"]
        location, name = _init(y, event_name, e, tla, color_by)

        points = payload["points"]
        xs = np.array([p["x"] for p in points])
        ys = np.array([p["y"] for p in points])

        fig, ax = plt.subplots(figsize=(13, 13))

        segs = np.array([xs, ys]).T.reshape(-1, 1, 2)
        segments = np.concatenate([segs[:-1], segs[1:]], axis=1)

        # Braking zones: wider red under-line so hard-braking sections stand
        # out beneath the color-coded racing line.
        braking = payload.get("braking_segments") or []
        for seg in braking:
            s_idx = max(0, min(seg["start_idx"], len(points) - 1))
            e_idx = max(0, min(seg["end_idx"], len(points) - 1))
            if e_idx <= s_idx:
                continue
            bx = xs[s_idx:e_idx + 1]
            by = ys[s_idx:e_idx + 1]
            if len(bx) < 2:
                continue
            ax.plot(bx, by, color="#FF3B3B", linewidth=12, alpha=0.9,
                    solid_capstyle="round", zorder=2)

        if color_by == "gear":
            gears = np.array([p.get("gear") or 0 for p in points[:-1]], dtype=float)
            cmap = ListedColormap(_GEAR_COLORS)
            bounds = np.arange(0.5, 9.5, 1.0)
            norm = BoundaryNorm(bounds, cmap.N)
            lc = LineCollection(segments, cmap=cmap, norm=norm, zorder=3)
            lc.set_array(gears)
            colorbar_label = "Gear"
        else:
            speeds = np.array([p.get("speed") or 0.0 for p in points[:-1]], dtype=float)
            cmap = plt.get_cmap("plasma")
            lc = LineCollection(segments, cmap=cmap, zorder=3)
            lc.set_array(speeds)
            colorbar_label = "Speed (km/h)"

        lc.set_linewidth(7)
        ax.add_collection(lc)

        # Start/Finish marker.
        ax.scatter([xs[0]], [ys[0]], s=140, color="white", edgecolors="#0d0d0d",
                   linewidths=1.5, zorder=5)
        ax.annotate("S/F", (xs[0], ys[0]), textcoords="offset points", xytext=(10, 10),
                    fontsize=11, fontweight="bold", color="white")

        TrackMapPlot._add_callouts(ax, payload.get("callouts") or {})

        ax.set_aspect('equal')
        ax.axis('off')
        ax.autoscale_view()

        cbar = fig.colorbar(lc, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02)
        cbar.set_label(colorbar_label)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=4, alpha=.5)
        except Exception:
            pass

        lap_str = format_lap_time(payload.get("lap_time_s", 0.0))
        plt.suptitle(f"{tla} fastest lap — {lap_str}\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _add_callouts(ax, callouts: Dict[str, Optional[Dict[str, Any]]]) -> None:
        top_speed = callouts.get("top_speed")
        if top_speed and top_speed.get("x") is not None:
            ax.annotate(
                f"Top speed\n{top_speed['speed']:.0f} km/h",
                xy=(top_speed["x"], top_speed["y"]),
                xytext=(25, 25), textcoords="offset points",
                fontsize=10, color="white",
                arrowprops=dict(arrowstyle="->", color="white", lw=1.2),
                bbox=dict(boxstyle="round,pad=0.3", fc="#141414", ec="#2a2a2a", alpha=0.85),
            )

        slowest = callouts.get("slowest_corner")
        if slowest and slowest.get("x") is not None:
            ax.annotate(
                f"Slowest corner\n{slowest['speed']:.0f} km/h",
                xy=(slowest["x"], slowest["y"]),
                xytext=(-60, -25), textcoords="offset points",
                fontsize=10, color="white",
                arrowprops=dict(arrowstyle="->", color="white", lw=1.2),
                bbox=dict(boxstyle="round,pad=0.3", fc="#141414", ec="#2a2a2a", alpha=0.85),
            )


if __name__ == "__main__":
    print("Testing V2 Telemetry Track Map...")
    try:
        client = F1StaticClient()
        data = TrackMapData()(2025, 1, "Q", "VER", "speed")
        print(f"Points: {len(data.get('points', []))}")
        plot_path = TrackMapPlot()(2025, 1, "Q", "VER", "speed")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
