"""
Corner-by-Corner Duel (V2, livetiming-backed).

Compares two drivers' fastest laps corner-by-corner: where each one is
gaining or losing time, who brakes later, who carries more apex speed.

Methodology:
  * Both drivers' ``Time`` is interpolated (``np.interp``) onto a common
    distance grid built from the union span of their fastest-lap distances.
  * ``delta(d) = t2(d) - t1(d)``, so **positive delta means driver2 is
    BEHIND driver1** at that point on track (driver2's elapsed time is
    larger) and **negative delta means driver2 is AHEAD**. This mirrors the
    usual "delta to reference" convention where driver1 is the reference lap.
  * Corners are detected on driver1's telemetry via ``detect_corners``
    (``_helpers``). Corner *numbers* come from the circuit JSON's
    ``race_data.corners`` (nearest ``trackPosition`` within ~150 units of the
    apex's rotated X/Y); if no circuit info is available, corners are
    numbered sequentially in track order.
  * Per corner: ``min_speed`` for each driver is the minimum speed within
    +-40 m of the apex distance; ``braking_point`` is found by scanning
    backward from the apex for the first sample with ``Brake > 0``; the
    ``delta_gain`` is ``delta(apex + 60m) - delta(apex - 120m)`` — positive
    means driver2 gained time through the corner (relative to driver1),
    negative means driver1 gained.

Any session (practice/qualifying/race all have CarData + Position streams).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import (
    detect_corners,
    get_all_driver_codes,
    get_circuit_info_for_session,
    get_fastest_lap_telemetry,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import get_driver_color

logger = get_logger(__name__)

DATA_TYPE = "corner_duel"

_CHANNELS = ["2", "5"]  # Speed, Brake
_CORNER_NUMBER_MATCH_RADIUS = 150.0
_APEX_WINDOW_M = 40.0
_BRAKE_SCAN_BACK_M = 300.0
_GAIN_AHEAD_M = 60.0
_GAIN_BEHIND_M = 120.0
_MAX_DELTA_SERIES_POINTS = 400


def _init(y: int, event_name: str, session_name: str, tla1: str, tla2: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Corner duel {tla1} vs {tla2} {y} {event_name} {session_name}.png"
    return location, name


def _data_type(tla1: str, tla2: str) -> str:
    a, b = sorted([tla1.upper(), tla2.upper()])
    return f"corner_duel_{a}_{b}"


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
def build_common_distance_grid(dist1: np.ndarray, dist2: np.ndarray, n: int = 500) -> np.ndarray:
    """Common distance grid spanning the overlap of both drivers' laps."""
    lo = max(float(np.min(dist1)), float(np.min(dist2)))
    hi = min(float(np.max(dist1)), float(np.max(dist2)))
    if hi <= lo:
        hi = max(float(np.max(dist1)), float(np.max(dist2)))
        lo = min(float(np.min(dist1)), float(np.min(dist2)))
    return np.linspace(lo, hi, n)


def interp_time_onto_grid(distance: np.ndarray, time: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """``np.interp`` a driver's Time series onto a common distance grid."""
    order = np.argsort(distance)
    return np.interp(grid, distance[order], time[order])


def compute_delta_series(t1_grid: np.ndarray, t2_grid: np.ndarray) -> np.ndarray:
    """delta(d) = t2(d) - t1(d). Positive => driver2 behind driver1 at d."""
    return t2_grid - t1_grid


def _downsample_indices(n: int, max_points: int = _MAX_DELTA_SERIES_POINTS) -> List[int]:
    """Evenly-spaced positional indices covering ``range(n)`` (<= max_points)."""
    if n <= max_points:
        return list(range(n))
    idx = np.linspace(0, n - 1, max_points).round().astype(int)
    return sorted(set(idx.tolist()))


def _nearest_value(x: np.ndarray, y: np.ndarray, target_x: float) -> Optional[float]:
    if len(x) == 0:
        return None
    idx = int(np.argmin(np.abs(x - target_x)))
    return float(y[idx])


def _min_speed_near(
    distance: np.ndarray, speed: np.ndarray, apex: float, window: float = _APEX_WINDOW_M
) -> Optional[float]:
    mask = (distance >= apex - window) & (distance <= apex + window)
    if not np.any(mask):
        return None
    return float(np.min(speed[mask]))


def _braking_point_backward_scan(
    distance: np.ndarray, brake: np.ndarray, apex: float, scan_back: float = _BRAKE_SCAN_BACK_M
) -> Optional[float]:
    """First ``Brake > 0`` sample found scanning backward from the apex.

    Walks samples in descending-distance order starting at/just before the
    apex, looking for the first braking sample. Returns that sample's
    distance (the "braking point"), or ``None`` if no braking is found within
    ``scan_back`` metres of the apex.
    """
    mask = (distance <= apex) & (distance >= apex - scan_back)
    if not np.any(mask):
        return None
    idxs = np.where(mask)[0]
    # Sort by descending distance (closest to apex first) for a true backward scan.
    idxs = idxs[np.argsort(-distance[idxs])]
    for i in idxs:
        if brake[i] > 0:
            return float(distance[i])
    return None


def _match_corner_number(
    apex_x: Optional[float], apex_y: Optional[float],
    circuit_corners: List[Dict[str, Any]], radius: float = _CORNER_NUMBER_MATCH_RADIUS,
) -> Optional[int]:
    if apex_x is None or apex_y is None or not circuit_corners:
        return None
    best_num = None
    best_dist = radius
    for c in circuit_corners:
        cx, cy, num = c.get("x"), c.get("y"), c.get("number")
        if cx is None or cy is None or num is None:
            continue
        d = ((apex_x - cx) ** 2 + (apex_y - cy) ** 2) ** 0.5
        if d < best_dist:
            best_dist = d
            best_num = int(num)
    return best_num


def build_payload_from_parts(
    df1, df2, tla1: str, tla2: str, circuit_info: Optional[Dict[str, Any]],
    team1: Optional[str], team2: Optional[str],
    y: int, event_name: str, session: str,
) -> Dict[str, Any]:
    """Assemble the corner-duel payload from two already-fetched telemetry frames."""
    f1 = df1.sort_values('Distance').reset_index(drop=True)
    f2 = df2.sort_values('Distance').reset_index(drop=True)

    dist1 = f1['Distance'].to_numpy(dtype=float)
    dist2 = f2['Distance'].to_numpy(dtype=float)
    time1 = f1['Time'].to_numpy(dtype=float)
    time2 = f2['Time'].to_numpy(dtype=float)
    speed1 = f1['Speed'].to_numpy(dtype=float) if 'Speed' in f1.columns else np.zeros_like(dist1)
    speed2 = f2['Speed'].to_numpy(dtype=float) if 'Speed' in f2.columns else np.zeros_like(dist2)
    brake1 = f1['Brake'].to_numpy(dtype=float) if 'Brake' in f1.columns else np.zeros_like(dist1)

    grid = build_common_distance_grid(dist1, dist2, n=500)
    t1_grid = interp_time_onto_grid(dist1, time1, grid)
    t2_grid = interp_time_onto_grid(dist2, time2, grid)
    delta = compute_delta_series(t1_grid, t2_grid)

    corners = detect_corners(f1[['Distance', 'Speed']])

    circuit_corners = circuit_info.get('corners') if circuit_info else None
    rotation = circuit_info.get('rotation', 0) if circuit_info else 0

    corner_records: List[Dict[str, Any]] = []
    for i, corner in enumerate(corners, start=1):
        apex = corner["distance_m"]

        apex_x = _nearest_value(dist1, f1['X'].to_numpy(dtype=float), apex) if 'X' in f1.columns else None
        apex_y = _nearest_value(dist1, f1['Y'].to_numpy(dtype=float), apex) if 'Y' in f1.columns else None

        number = None
        if circuit_corners and apex_x is not None and apex_y is not None:
            rx, ry = apex_x, apex_y
            if rotation:
                theta = np.deg2rad(rotation)
                rx = apex_x * np.cos(theta) - apex_y * np.sin(theta)
                ry = apex_x * np.sin(theta) + apex_y * np.cos(theta)
            number = _match_corner_number(rx, ry, circuit_corners)
        if number is None:
            number = i

        min_speed_1 = _min_speed_near(dist1, speed1, apex)
        min_speed_2 = _min_speed_near(dist2, speed2, apex)
        braking_point_1 = _braking_point_backward_scan(dist1, brake1, apex)

        delta_ahead = _nearest_value(grid, delta, apex + _GAIN_AHEAD_M)
        delta_behind = _nearest_value(grid, delta, apex - _GAIN_BEHIND_M)
        delta_gain = None
        if delta_ahead is not None and delta_behind is not None:
            delta_gain = round(delta_ahead - delta_behind, 4)

        corner_records.append({
            "number": number,
            "apex_distance_m": round(apex, 1),
            "min_speed_kmh": {tla1: min_speed_1, tla2: min_speed_2},
            "braking_point_m": {tla1: braking_point_1},
            "delta_gain_s": delta_gain,
            "beneficiary": (tla2 if (delta_gain or 0) > 0 else tla1) if delta_gain is not None else None,
        })

    corner_records.sort(key=lambda c: c["apex_distance_m"])

    # Downsample the delta series for the JSON payload.
    idx = _downsample_indices(len(grid))
    delta_series = [{"distance": float(grid[i]), "delta_s": float(delta[i])} for i in idx]

    # Per-driver full speed-vs-distance traces (same downsampling approach).
    idx1 = _downsample_indices(len(dist1))
    idx2 = _downsample_indices(len(dist2))
    speed_series = {
        tla1: [{"dist_m": float(dist1[i]), "speed_kmh": float(speed1[i])} for i in idx1],
        tla2: [{"dist_m": float(dist2[i]), "speed_kmh": float(speed2[i])} for i in idx2],
    }

    return {
        "driver1": tla1,
        "driver2": tla2,
        "driver1_color": get_driver_color(tla1),
        "driver2_color": get_driver_color(tla2),
        "same_team": bool(team1 and team2 and team1 == team2),
        "delta_series": delta_series,
        "speed_series": speed_series,
        "corners": corner_records,
        "session_info": {"year": y, "event_name": event_name, "session_name": session},
    }


def _build_payload(
    store: SessionDataStore, tla1: str, tla2: str,
    year: int, identifier: Any, session: str,
) -> Dict[str, Any]:
    client = store.client
    base_url = store.base_url
    driver_codes = get_all_driver_codes(base_url, client)
    num1 = _resolve_driver_num(driver_codes, tla1, year, identifier, session)
    num2 = _resolve_driver_num(driver_codes, tla2, year, identifier, session)

    df1 = get_fastest_lap_telemetry(base_url, client, num1, channels=_CHANNELS)
    df2 = get_fastest_lap_telemetry(base_url, client, num2, channels=_CHANNELS)
    if df1.empty or df2.empty:
        missing = tla1 if df1.empty else tla2
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session, source="livetiming",
            reason=f"No fastest-lap telemetry available for {missing.upper()}",
        )

    drivers = store.driver_list()
    team1 = drivers.get(num1, {}).get("team")
    team2 = drivers.get(num2, {}).get("team")

    circuit_info = get_circuit_info_for_session(store)

    return build_payload_from_parts(
        df1, df2, tla1.upper(), tla2.upper(), circuit_info, team1, team2,
        year, store.event_name, session,
    )


class CornerDuelData:
    """Callable: ``CornerDuelData()(year, identifier, session, driver1, driver2) -> dict``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        driver1: str, driver2: str,
    ) -> Dict[str, Any]:
        tla1, tla2 = driver1.strip().upper(), driver2.strip().upper()

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, tla1, tla2, y, identifier, e)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=_data_type(tla1, tla2), generator=_generate, version="v2",
        )


class CornerDuelPlot:
    """Callable: ``CornerDuelPlot()(year, identifier, session, driver1, driver2) -> png path``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        driver1: str, driver2: str,
    ) -> str:
        tla1, tla2 = driver1.strip().upper(), driver2.strip().upper()

        payload = CornerDuelData()(y, identifier, e, tla1, tla2)
        if not payload.get("delta_series"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason=f"No comparable telemetry available for {tla1} vs {tla2}",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        return self._render(payload, y, event_name, e)

    @staticmethod
    def _render(payload: Dict[str, Any], y: int, event_name: str, e: str) -> str:
        setup_theme.setup_turnone_theme()
        tla1, tla2 = payload["driver1"], payload["driver2"]
        location, name = _init(y, event_name, e, tla1, tla2)

        color1 = payload["driver1_color"]
        color2 = payload["driver2_color"]
        same_team = payload.get("same_team", False)

        series = payload["delta_series"]
        distances = np.array([p["distance"] for p in series])
        deltas = np.array([p["delta_s"] for p in series])
        corners = payload.get("corners", [])

        fig, (ax_speed, ax_delta, ax_bars) = plt.subplots(
            3, 1, figsize=(14, 12), height_ratios=[2, 1, 1],
            sharex=True, layout='constrained',
        )

        speed_series = payload.get("speed_series") or {}
        CornerDuelPlot._render_speed_panel(
            ax_speed, speed_series, corners, tla1, tla2, color1, color2, same_team
        )
        CornerDuelPlot._render_delta_panel(ax_delta, distances, deltas, tla1, tla2)
        CornerDuelPlot._render_gain_bars(ax_bars, corners, tla1, tla2, color1, color2)

        ax_bars.set_xlabel("Distance (m)")

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"{tla1} vs {tla2} corner duel\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _render_speed_panel(ax, speed_series, corners, tla1, tla2, color1, color2, same_team):
        """Top panel: both drivers' full speed-vs-distance traces + corner markers."""
        style2 = "--" if same_team else "-"
        for tla, color, style in ((tla1, color1, "-"), (tla2, color2, style2)):
            trace = speed_series.get(tla) or []
            if not trace:
                continue
            xs = [p["dist_m"] for p in trace]
            ys = [p["speed_kmh"] for p in trace]
            ax.plot(xs, ys, color=color, linestyle=style, linewidth=2.2, label=tla, zorder=3)

        for corner in corners:
            apex = corner["apex_distance_m"]
            ax.axvline(apex, color="#555555", linestyle=":", linewidth=1.0, zorder=1)
            ax.annotate(str(corner["number"]), (apex, 1.0), xycoords=("data", "axes fraction"),
                        ha="center", va="bottom", fontsize=9, color="#aaaaaa")

        ax.set_ylabel("Speed (km/h)")
        ax.set_title("Speed traces (corner apexes marked)")
        ax.legend(loc="lower right", fontsize=9)

    @staticmethod
    def _render_delta_panel(ax, distances, deltas, tla1, tla2):
        ax.axhline(0, color="#888888", linewidth=1.0)
        ax.plot(distances, deltas, color="white", linewidth=1.6, zorder=3)
        ax.fill_between(
            distances, deltas, 0, where=(deltas < 0), color="#43b02a",
            alpha=0.35, interpolate=True, label=f"{tla2} ahead",
        )
        ax.fill_between(
            distances, deltas, 0, where=(deltas >= 0), color="#da291c",
            alpha=0.35, interpolate=True, label=f"{tla1} ahead",
        )
        ax.set_ylabel("Cumulative delta (s)")
        ax.set_title(f"Gap evolution ({tla2} - {tla1})")
        ax.legend(loc="upper left", fontsize=9)

    @staticmethod
    def _render_gain_bars(ax, corners, tla1, tla2, color1, color2):
        if not corners:
            ax.text(0.5, 0.5, "No corner data", ha="center", va="center",
                    transform=ax.transAxes, color="#888888")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        labels = [str(c["number"]) for c in corners]
        gains = [c["delta_gain_s"] if c["delta_gain_s"] is not None else 0.0 for c in corners]
        colors = [color2 if (c.get("delta_gain_s") or 0) > 0 else color1 for c in corners]

        ax.bar(labels, gains, color=colors, edgecolor="#0d0d0d")
        ax.axhline(0, color="#888888", linewidth=1.0)
        ax.set_xlabel("Corner")
        ax.set_ylabel("Delta gain (s)")
        ax.set_title(f"Per-corner delta gain (colored by beneficiary: {color1}={tla1}, {color2}={tla2})")


if __name__ == "__main__":
    print("Testing V2 Corner Duel...")
    try:
        client = F1StaticClient()
        data = CornerDuelData()(2025, 1, "Q", "VER", "NOR")
        print(f"Corners: {len(data.get('corners', []))}")
        plot_path = CornerDuelPlot()(2025, 1, "Q", "VER", "NOR")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
