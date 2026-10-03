"""
Playback-ready telemetry frames over a lap range (V2, livetiming-backed).

``lap_all_data`` answers "everything about one lap for one driver" and returns
the raw ~4 Hz samples exactly as the feed published them. That is the right
shape for analysis and the wrong shape for animation: two drivers' samples land
on different instants, the interval between them wobbles, and a client wanting
smooth motion has to interpolate the whole thing itself.

This module produces the animation shape instead: for a *range* of laps and a
*set* of drivers, a single uniform time grid with position, throttle, brake and
the rest resampled onto it, so a client can advance one frame per tick and draw.

Two layers, and the split is what makes it cheap:

* :class:`LapFrames` is the cache unit -- one driver, one lap, the merged raw
  samples for that window. Small (~340 rows), stable, and durably cached, so it
  is also what the admin backfill can pre-warm.
* :class:`LapsData` is the request. It gathers the cached blocks for every
  (driver, lap) asked for, and resamples the *concatenated* range on **one**
  continuous grid. Resampling per lap and concatenating afterwards would put a
  ragged interval at every lap boundary, because lap lengths are not multiples
  of the frame interval -- exactly the stutter this endpoint exists to avoid.

Cache misses for the whole request are collected first and served by a single
pass over each raw stream (``extract_channels_window`` /
``extract_positions_window``), not one pass per driver per lap.

Any session works -- practice, qualifying and race all publish CarData and
Position.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.repositories.plots import store_data_dict_to_mongo
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import (
    CAR_DATA_CHANNELS,
    WINDOW_MARGIN_S,
    XY_UNITS_PER_METRE,
    build_session_store,
    extract_channels_window,
    extract_positions_window,
    get_circuit_info_for_session,
    resolve_driver,
    shared_session_stores,
)

logger = get_logger(__name__)

DATA_TYPE = "lap_frames"

# Bump whenever the stored block's shape or meaning changes. Blocks written by
# an older version are ignored and regenerated rather than silently mixed in:
# v1 was written before the CarData channel map was corrected, so its 'gear'
# actually held DRS and its 'rpm' held gear.
BLOCK_SCHEMA_VERSION = 2

# Channels are requested by their raw feed key and mapped here, deliberately
# bypassing _helpers.CHANNEL_NAMES: that table mislabels '3' as RPM and '45' as
# Gear (see the warning on it). Probing the 2025 feed shows 0=RPM, 2=Speed,
# 3=Gear, 4=Throttle, 5=Brake, 45=DRS, and no channel '47' at all.
_CHANNELS = list(CAR_DATA_CHANNELS)
_CHANNEL_TO_FIELD = dict(CAR_DATA_CHANNELS)

# Never interpolate across a hole longer than this. A car sitting in the pit box
# publishes nothing; bridging that would slide it through the pit building.
MAX_INTERP_GAP_S = 2.0

# Continuous quantities -- safe to interpolate between bracketing samples.
LERP_FIELDS: Tuple[str, ...] = ("x", "y", "z", "speed", "rpm", "throttle")
# Discrete quantities -- carry the previous sample forward. Averaging gear 4 and
# gear 5 into 4.5 would be a value the car never had.
HOLD_FIELDS: Tuple[str, ...] = ("brake", "gear", "drs", "status")

_INT_FIELDS = frozenset({"gear", "drs"})

# Request budget. The unit cap is the coarse guard; the frame budget is the one
# that actually binds -- 100 driver-laps at 10 Hz is ~85k frames (~15 MB of
# JSON), which the unit cap alone would happily wave through.
MAX_DRIVER_LAPS = 100
MAX_FRAMES = 60_000

MIN_HZ, MAX_HZ = 1, 20
FORMATS = ("frames", "columnar")

FRAME_FIELDS: Tuple[str, ...] = (
    "t", "lap", "session_time", "d",
    "x", "y", "z",
    "speed", "throttle", "brake", "gear", "rpm", "drs",
    "status",
)


def _data_type(tla: str, lap: int) -> str:
    """Stored key for one cached (driver, lap) sample block.

    The registry imports this rather than re-spelling the key, so the backfill
    and the request path cannot drift apart.
    """
    return f"{DATA_TYPE}_{tla.upper()}_{lap}"


# ---------------------------------------------------------------------------
# Pure builders -- no network, no store, unit-testable in isolation
# ---------------------------------------------------------------------------

def build_series(tel_df, pos_df) -> Dict[str, List[Tuple[float, Any]]]:
    """Split merged CarData/Position frames into one sorted series per field.

    Returns ``{field: [(session_time, value), ...]}``. The two streams sample on
    independent clocks, so keeping each field as its own series lets the
    resampler bracket every field against its *own* neighbours instead of
    forcing both streams onto a shared, mostly-empty union timeline.
    """
    series: Dict[str, List[Tuple[float, Any]]] = {f: [] for f in LERP_FIELDS + HOLD_FIELDS}

    # Columns are read by name rather than via itertuples: the DRS channel is
    # literally named '47', which is not a Python identifier, so itertuples
    # would silently rename it and drop DRS on the floor.
    if pos_df is not None and not pos_df.empty:
        times = [float(t) for t in pos_df["Time"]]
        for axis, field in (("X", "x"), ("Y", "y"), ("Z", "z")):
            if axis in pos_df.columns:
                series[field] = [(t, float(v)) for t, v in zip(times, pos_df[axis])]
        if "Status" in pos_df.columns:
            series["status"] = [(t, str(v)) for t, v in zip(times, pos_df["Status"])
                                if v is not None and v == v]

    if tel_df is not None and not tel_df.empty:
        times = [float(t) for t in tel_df["Time"]]
        for col in tel_df.columns:
            field = _CHANNEL_TO_FIELD.get(col)
            if field is None:
                continue
            pairs = []
            for t, val in zip(times, tel_df[col]):
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                if fval != fval:  # NaN
                    continue
                pairs.append((t, fval))
            series[field] = pairs

    for key in series:
        series[key].sort(key=lambda pair: pair[0])
    return series


def detect_gaps(times: Sequence[float], start_t: float, end_t: float,
                max_gap_s: float = MAX_INTERP_GAP_S) -> List[Dict[str, Any]]:
    """Windows inside ``[start_t, end_t]`` where the car's position is unknown.

    Covers three cases: no samples before the range starts, no samples after it
    ends, and a hole in the middle wider than ``max_gap_s``. Bounds are absolute
    session seconds; the caller rebases them onto its own clock.
    """
    gaps: List[Dict[str, Any]] = []
    ordered = sorted(times)
    if not ordered:
        return [{"from": start_t, "to": end_t, "reason": "no_data"}]

    if ordered[0] - start_t > max_gap_s:
        gaps.append({"from": start_t, "to": ordered[0], "reason": "no_data"})
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt - prev > max_gap_s:
            gaps.append({"from": prev, "to": nxt, "reason": "stream_dropout"})
    if end_t - ordered[-1] > max_gap_s:
        gaps.append({"from": ordered[-1], "to": end_t, "reason": "no_data"})

    return [g for g in gaps if g["to"] > start_t and g["from"] < end_t]


def resample_series(series: Sequence[Tuple[float, Any]], grid: Sequence[float],
                    hold: bool = False,
                    max_gap_s: float = MAX_INTERP_GAP_S) -> List[Optional[Any]]:
    """Sample one series onto ``grid`` (both in absolute session seconds).

    ``hold=True`` carries the previous value forward instead of interpolating.
    Returns ``None`` for any grid point outside the series' coverage, or sitting
    inside a hole wider than ``max_gap_s`` -- a gap is reported honestly rather
    than papered over.
    """
    out: List[Optional[Any]] = []
    if not series:
        return [None] * len(grid)

    i = 0
    last = len(series) - 1
    for g in grid:
        while i < last and series[i + 1][0] <= g:
            i += 1
        lo_t, lo_v = series[i]

        if lo_t > g:                      # before the first sample
            out.append(None)
            continue
        if lo_t == g:                     # exact hit -- always trustworthy
            out.append(lo_v)
            continue
        if i >= last:                     # after the last sample
            out.append(None)
            continue

        hi_t, hi_v = series[i + 1]
        if hi_t - lo_t > max_gap_s:       # inside a hole: do not bridge it
            out.append(None)
            continue
        if hold:
            out.append(lo_v)
            continue

        span = hi_t - lo_t
        frac = 0.0 if span <= 0 else (g - lo_t) / span
        out.append(lo_v + (hi_v - lo_v) * frac)

    return out


def build_grid(start_t: float, end_t: float, hz: int) -> List[float]:
    """Uniform absolute-time grid covering ``[start_t, end_t)`` at ``hz``."""
    duration = max(0.0, end_t - start_t)
    n = int(math.ceil(duration * hz))
    step = 1.0 / hz
    return [start_t + i * step for i in range(n)]


def assign_laps(grid: Sequence[float], laps: Sequence[Dict[str, Any]]) -> List[Optional[int]]:
    """Map each grid instant to the lap whose window contains it."""
    out: List[Optional[int]] = []
    ordered = sorted(laps, key=lambda le: le["start_s"])
    i = 0
    for g in grid:
        while i < len(ordered) - 1 and g >= ordered[i]["end_s"]:
            i += 1
        entry = ordered[i] if ordered else None
        if entry is not None and entry["start_s"] <= g < entry["end_s"]:
            out.append(int(entry["number"]))
        elif entry is not None and g >= entry["end_s"] and i == len(ordered) - 1:
            out.append(int(entry["number"]))   # final frame lands exactly on the flag
        else:
            out.append(None)
    return out


def add_distance(frames: List[Dict[str, Any]]) -> None:
    """Add ``d``: metres travelled since the start of each frame's own lap.

    Derived from the resampled X/Y rather than interpolated from the 4 Hz
    stream, so it stays consistent with the coordinates actually being drawn.
    X/Y stay in raw feed units (tenths of a metre) so they overlay the circuit
    outline directly; ``d`` is converted to metres because it is labelled so.
    Resets on every lap change; a frame with no position holds the running
    total rather than inventing movement.
    """
    total = 0.0
    prev_xy: Optional[Tuple[float, float]] = None
    prev_lap: Optional[int] = None

    for fr in frames:
        lap = fr.get("lap")
        if lap != prev_lap:
            total, prev_xy, prev_lap = 0.0, None, lap

        x, y = fr.get("x"), fr.get("y")
        if x is None or y is None:
            fr["d"] = round(total, 3)
            continue
        if prev_xy is not None:
            total += math.hypot(x - prev_xy[0], y - prev_xy[1]) / XY_UNITS_PER_METRE
        prev_xy = (x, y)
        fr["d"] = round(total, 3)


def to_columnar(frames: Sequence[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Array-of-objects -> parallel arrays. Nulls are preserved as ``None``."""
    return {field: [fr.get(field) for fr in frames] for field in FRAME_FIELDS}


def round_frames(frames: List[Dict[str, Any]], precision: Optional[int]) -> List[Dict[str, Any]]:
    """Round every float in place. ``precision=None`` leaves values untouched."""
    if precision is None:
        return frames
    for fr in frames:
        for key, val in fr.items():
            if isinstance(val, float):
                fr[key] = round(val, precision)
    return frames


def estimate_cost(n_drivers: int, n_laps: int, mean_lap_s: float, hz: int) -> Dict[str, Any]:
    """Predicted size of a request, and whether it fits the budget."""
    driver_laps = n_drivers * n_laps
    est_frames = int(round(driver_laps * max(0.0, mean_lap_s) * hz))
    return {
        "driver_laps": driver_laps,
        "max_driver_laps": MAX_DRIVER_LAPS,
        "estimated_frames": est_frames,
        "max_frames": MAX_FRAMES,
        "within_budget": driver_laps <= MAX_DRIVER_LAPS and est_frames <= MAX_FRAMES,
    }


def check_budget(cost: Dict[str, Any], hz: int) -> None:
    """Raise ``ValueError`` (-> HTTP 400) when a request exceeds the budget."""
    if cost["driver_laps"] > MAX_DRIVER_LAPS:
        raise ValueError(
            f"Request covers {cost['driver_laps']} driver-laps, over the limit of "
            f"{MAX_DRIVER_LAPS}. Narrow the lap range or ask for fewer drivers."
        )
    if cost["estimated_frames"] > MAX_FRAMES:
        suggested = max(MIN_HZ, int(hz * MAX_FRAMES / max(1, cost["estimated_frames"])))
        raise ValueError(
            f"Request would produce about {cost['estimated_frames']} frames, over the "
            f"limit of {MAX_FRAMES}. Lower hz (hz={suggested} would fit), shorten the "
            f"lap range, or ask for fewer drivers."
        )


def build_frames(series: Dict[str, List[Tuple[float, Any]]],
                 laps: Sequence[Dict[str, Any]],
                 t0: float, t1: float, hz: int,
                 max_gap_s: float = MAX_INTERP_GAP_S
                 ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resample one driver's series onto a uniform grid. Returns ``(frames, gaps)``."""
    grid = build_grid(t0, t1, hz)
    if not grid:
        return [], []

    columns = {
        field: resample_series(series.get(field, []), grid,
                               hold=field in HOLD_FIELDS, max_gap_s=max_gap_s)
        for field in LERP_FIELDS + HOLD_FIELDS
    }
    lap_of = assign_laps(grid, laps)

    frames: List[Dict[str, Any]] = []
    for idx, g in enumerate(grid):
        fr: Dict[str, Any] = {
            "t": round(g - t0, 6),
            "lap": lap_of[idx],
            "session_time": round(g, 6),
        }
        for field in LERP_FIELDS:
            fr[field] = columns[field][idx]
        for field in HOLD_FIELDS:
            val = columns[field][idx]
            fr[field] = int(val) if val is not None and field in _INT_FIELDS else val
        if fr.get("status") is None:
            fr["status"] = "NoData" if fr.get("x") is None else "Unknown"
        frames.append(fr)

    add_distance(frames)

    pos_times = [t for t, _ in series.get("x", [])]
    gaps = [
        {"from": round(g["from"] - t0, 3), "to": round(g["to"] - t0, 3), "reason": g["reason"]}
        for g in detect_gaps(pos_times, t0, t1, max_gap_s)
    ]
    return frames, gaps


def index_laps(frames: Sequence[Dict[str, Any]],
               laps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotate each lap with the ``[frame_from, frame_to)`` slice covering it.

    Lets a client seek straight to a lap without scanning the frame list.
    """
    bounds: Dict[int, List[int]] = {}
    for idx, fr in enumerate(frames):
        lap = fr.get("lap")
        if lap is None:
            continue
        if lap not in bounds:
            bounds[lap] = [idx, idx + 1]
        else:
            bounds[lap][1] = idx + 1

    out = []
    for entry in laps:
        item = dict(entry)
        span = bounds.get(int(entry["number"]))
        item["frame_from"], item["frame_to"] = (span[0], span[1]) if span else (None, None)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Store-backed assembly
# ---------------------------------------------------------------------------

def lap_windows(store: Any, car_num: str, lap_from: int, lap_to: int) -> List[Dict[str, Any]]:
    """Resolve ``[start_s, end_s]`` and metadata for each lap in the range.

    Window formula is the canonical one used across V2: a lap record carries the
    session time it *completed* at plus its duration, so the lap ran over
    ``[timestamp_s - time_s, timestamp_s]``.
    """
    records = {int(r["lap"]): r for r in store.lap_times().get(car_num, [])
               if r.get("lap") is not None}
    positions = {int(r["lap"]): r.get("position")
                 for r in store.positions_by_lap().get(car_num, [])
                 if r.get("lap") is not None}
    stints = store.stints().get(car_num, [])

    out: List[Dict[str, Any]] = []
    for lap in range(lap_from, lap_to + 1):
        rec = records.get(lap)
        if rec is None:
            continue
        time_s = float(rec.get("time_s") or 0.0)
        timestamp_s = float(rec.get("timestamp_s") or 0.0)
        if time_s <= 0 or timestamp_s <= 0:
            continue
        stint = _stint_for_lap(stints, lap)
        out.append({
            "number": lap,
            "lap_time_s": round(time_s, 3),
            "start_s": round(timestamp_s - time_s, 3),
            "end_s": round(timestamp_s, 3),
            "pit_in": bool(rec.get("pit_in", False)),
            "pit_out": bool(rec.get("pit_out", False)),
            "position": positions.get(lap),
            "compound": stint.get("compound") if stint else None,
            "tyre_life": stint.get("tyre_life_end") if stint else None,
        })
    return out


def _stint_for_lap(stints: Iterable[Dict[str, Any]], lap: int) -> Optional[Dict[str, Any]]:
    for st in stints:
        start, end = st.get("start_lap"), st.get("end_lap")
        if start is None:
            continue
        if lap >= start and (end is None or lap <= end):
            return st
    return None


def _driver_block(store: Any, driver_info: Dict[str, Any], car_num: str,
                  tla: str) -> Dict[str, Any]:
    return {
        "tla": driver_info.get("tla", tla),
        "car_number": car_num,
        "name": driver_info.get("name", ""),
        "team": driver_info.get("team", ""),
        "color": driver_info.get("color", ""),
    }


def _samples_payload(tel_df, pos_df) -> Dict[str, List[List[Any]]]:
    """Cache-unit shape: one series per field, as ``[[time, value], ...]`` pairs.

    Stored rather than resampled frames so the request layer can lay a single
    continuous grid across a whole lap range.
    """
    series = build_series(tel_df, pos_df)
    return {field: [[t, v] for t, v in pairs] for field, pairs in series.items()}


def _series_from_payload(payload: Dict[str, Any]) -> Dict[str, List[Tuple[float, Any]]]:
    raw = payload.get("series", {}) or {}
    return {field: [(float(t), v) for t, v in raw.get(field, [])]
            for field in LERP_FIELDS + HOLD_FIELDS}


class LapFrames:
    """Callable: ``LapFrames()(year, identifier, session, driver, lap) -> dict``.

    The durable cache unit -- the merged raw samples for one driver's one lap.
    Registered in the feature catalog so the admin backfill can pre-warm it.
    """

    def __call__(self, y: int, identifier: Union[int, str], e: str,
                 driver: str, lap: int) -> Dict[str, Any]:
        tla = driver.strip().upper()

        def _generate() -> Dict[str, Any]:
            store = build_session_store(y, identifier, e)
            if store is None:
                raise DataNotAvailableError(
                    year=y, gp=identifier, session=e, source="livetiming",
                    reason="Session could not be resolved.",
                )
            payload = _generate_block(store, tla, lap)
            try:
                store_data_dict_to_mongo(
                    year=y, round_nr=store.round_nr, session_name=e,
                    event_name=store.event_name, data_type=_data_type(tla, lap),
                    data=payload, version="v2",
                )
            except Exception as exc:  # persistence is best-effort
                logger.debug("Failed to persist lap_frames to Mongo: %s", exc)
            return payload

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=_data_type(tla, lap), generator=_generate, version="v2",
        )


def _generate_block(store: Any, tla: str, lap: int) -> Dict[str, Any]:
    """Build one cached (driver, lap) sample block from the raw streams."""
    car_num, _info = resolve_driver(store, tla)
    windows = lap_windows(store, car_num, lap, lap)
    if not windows:
        raise DataNotAvailableError(
            year=store.year, gp=store.identifier, session=store.session_name,
            source="livetiming",
            reason=f"Lap {lap} not recorded for {tla}.",
        )
    win = windows[0]
    tel = extract_channels_window(
        store.base_url, store.client, [car_num], win["start_s"], win["end_s"],
        channels=_CHANNELS, store=store, raw_names=True,
    ).get(car_num)
    pos = extract_positions_window(
        store.base_url, store.client, [car_num], win["start_s"], win["end_s"], store=store,
    ).get(car_num)
    return {"schema": BLOCK_SCHEMA_VERSION, "lap": win,
            "series": _samples_payload(tel, pos)}


def build_track_payload(store: Any) -> Optional[Dict[str, Any]]:
    """Circuit geometry for drawing the track under the cars.

    Session-level and driver-free -- distinct from ``/api/v2/track-map-data``,
    which colours one driver's fastest lap by speed or gear.
    """
    info = get_circuit_info_for_session(store)
    if not info:
        return None
    xs, ys = info.get("x") or [], info.get("y") or []
    return {
        "rotation": info.get("rotation", 0),
        "corners": info.get("corners", []),
        "outline": [[float(x), float(y)] for x, y in zip(xs, ys)],
    }


class LapsData:
    """Callable: the request-level assembly across drivers x laps."""

    def __call__(self, y: int, identifier: Union[int, str], e: str,
                 drivers: Sequence[str], lap_from: int, lap_to: int,
                 hz: int = 10, fmt: str = "frames", precision: Optional[int] = 2,
                 include_track: bool = False) -> Dict[str, Any]:
        if lap_to < lap_from:
            raise ValueError(f"lap_to ({lap_to}) must be >= lap_from ({lap_from}).")
        if not (MIN_HZ <= hz <= MAX_HZ):
            raise ValueError(f"hz must be between {MIN_HZ} and {MAX_HZ}.")
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {', '.join(FORMATS)}.")
        tlas = [d.strip().upper() for d in drivers if d and d.strip()]
        if not tlas:
            raise ValueError("At least one driver is required.")

        # The ContextVar backing the scope does not propagate reliably from the
        # event loop into a threadpool worker, so it is opened here -- inside
        # the worker -- rather than by the router.
        with shared_session_stores():
            return self._build(y, identifier, e, tlas, lap_from, lap_to,
                               hz, fmt, precision, include_track)

    def _build(self, y, identifier, e, tlas, lap_from, lap_to,
               hz, fmt, precision, include_track) -> Dict[str, Any]:
        store = build_session_store(y, identifier, e)
        if store is None:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="Session could not be resolved.",
            )

        resolved = [(tla, *resolve_driver(store, tla)) for tla in tlas]

        windows: Dict[str, List[Dict[str, Any]]] = {}
        for tla, car_num, _info in resolved:
            windows[tla] = lap_windows(store, car_num, lap_from, lap_to)

        n_laps = max((len(w) for w in windows.values()), default=0)
        if n_laps == 0:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason=f"No recorded laps in range {lap_from}-{lap_to} for {', '.join(tlas)}.",
            )
        mean_lap = _mean_lap_time(windows)
        cost = estimate_cost(len(tlas), n_laps, mean_lap, hz)
        check_budget(cost, hz)          # reject before any stream is touched

        series_by_driver = self._gather_series(store, resolved, windows)

        drivers_out: List[Dict[str, Any]] = []
        total_frames = 0
        for tla, car_num, info in resolved:
            laps = windows[tla]
            if not laps:
                drivers_out.append({
                    "driver": _driver_block(store, info, car_num, tla),
                    "laps": [], "gaps": [],
                    **({"columns": to_columnar([])} if fmt == "columnar" else {"frames": []}),
                })
                continue

            t0 = min(le["start_s"] for le in laps)
            t1 = max(le["end_s"] for le in laps)
            frames, gaps = build_frames(series_by_driver[tla], laps, t0, t1, hz)
            round_frames(frames, precision)
            total_frames += len(frames)

            block: Dict[str, Any] = {
                "driver": _driver_block(store, info, car_num, tla),
                "laps": index_laps(frames, laps),
                "gaps": gaps,
            }
            block.update({"columns": to_columnar(frames)} if fmt == "columnar"
                         else {"frames": frames})
            drivers_out.append(block)

        return {
            "session_info": {
                "year": store.year,
                "event_name": store.event_name,
                "session_name": store.session_name,
                "round": store.round_nr,
            },
            "range": {
                "lap_from": lap_from, "lap_to": lap_to, "hz": hz,
                "format": fmt, "precision": precision,
                "frame_count": total_frames,
                "driver_laps": cost["driver_laps"],
            },
            "drivers": drivers_out,
            "track": build_track_payload(store) if include_track else None,
        }

    def _gather_series(self, store, resolved, windows) -> Dict[str, Dict[str, List]]:
        """Cached blocks where available; one shared stream pass for the rest.

        This is the whole point of the module: N drivers x M laps of misses cost
        *one* walk of CarData.z and *one* of Position.z, not N x M of each.
        """
        out: Dict[str, Dict[str, List]] = {tla: {f: [] for f in LERP_FIELDS + HOLD_FIELDS}
                                           for tla, _, _ in resolved}
        misses: List[Tuple[str, str, Dict[str, Any]]] = []

        for tla, car_num, _info in resolved:
            for entry in windows[tla]:
                cached = _cached_block(store, tla, entry["number"])
                if cached is None:
                    misses.append((tla, car_num, entry))
                else:
                    _extend(out[tla], _series_from_payload(cached))

        if misses:
            lo = min(m[2]["start_s"] for m in misses)
            hi = max(m[2]["end_s"] for m in misses)
            car_nums = {m[1] for m in misses}
            logger.info("lap_frames: %d cache miss(es); one stream pass over [%.1f, %.1f] "
                        "for %d driver(s)", len(misses), lo, hi, len(car_nums))
            tel = extract_channels_window(store.base_url, store.client, car_nums, lo, hi,
                                          channels=_CHANNELS, store=store,
                                          raw_names=True)
            pos = extract_positions_window(store.base_url, store.client, car_nums, lo, hi,
                                           store=store)
            for tla, car_num, entry in misses:
                block = _slice_block(tel.get(car_num), pos.get(car_num), entry)
                _extend(out[tla], _series_from_payload(block))
                _persist_block(store, tla, entry["number"], block)

        for tla in out:
            for field in out[tla]:
                out[tla][field] = _dedupe(out[tla][field])
        return out


def _dedupe(pairs: List[Tuple[float, Any]]) -> List[Tuple[float, Any]]:
    """Sort by time, keeping one sample per instant.

    Lap blocks overlap by ``WINDOW_MARGIN_S`` so edge frames have something to
    interpolate against, which means consecutive laps contribute the same
    samples twice. Duplicates would not corrupt the resampler, but they inflate
    every series by the overlap and make bracketing needlessly ambiguous.
    """
    out: List[Tuple[float, Any]] = []
    seen = set()
    for t, v in sorted(pairs, key=lambda pair: pair[0]):
        if t in seen:
            continue
        seen.add(t)
        out.append((t, v))
    return out


def _slice_block(tel_df, pos_df, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Clip one lap's window out of the shared multi-lap scan.

    Keeps ``WINDOW_MARGIN_S`` of samples on either side. A lap boundary almost
    never lands exactly on a sample instant, so without the overhang the first
    and last frame of a range have nothing to interpolate between and come back
    null -- a visible stutter at the ends of every animation.
    """
    lo = entry["start_s"] - WINDOW_MARGIN_S
    hi = entry["end_s"] + WINDOW_MARGIN_S
    tel = tel_df[(tel_df["Time"] >= lo) & (tel_df["Time"] <= hi)] \
        if tel_df is not None and not tel_df.empty else tel_df
    pos = pos_df[(pos_df["Time"] >= lo) & (pos_df["Time"] <= hi)] \
        if pos_df is not None and not pos_df.empty else pos_df
    return {"schema": BLOCK_SCHEMA_VERSION, "lap": entry,
            "series": _samples_payload(tel, pos)}


def _extend(target: Dict[str, List], addition: Dict[str, List]) -> None:
    for field, pairs in addition.items():
        if field in target:
            target[field].extend(pairs)


def _cached_block(store: Any, tla: str, lap: int) -> Optional[Dict[str, Any]]:
    """Read a stored sample block without generating one. Never raises."""
    try:
        from src.repositories.plots import get_plot_data_from_mongo
        row = get_plot_data_from_mongo(
            year=store.year, identifier=store.identifier,
            event_name=store.session_name, data_type=_data_type(tla, lap),
            version="v2",
        )
    except Exception as exc:
        logger.debug("lap_frames cache read failed for %s lap %s: %s", tla, lap, exc)
        return None
    if not row:
        return None
    data = row.get("data") if isinstance(row, dict) else None
    if not isinstance(data, dict) or "series" not in data:
        return None
    if data.get("schema") != BLOCK_SCHEMA_VERSION:
        logger.debug("lap_frames: ignoring stale v%s block for %s lap %s",
                     data.get("schema"), tla, lap)
        return None
    return data


def _persist_block(store: Any, tla: str, lap: int, block: Dict[str, Any]) -> None:
    try:
        store_data_dict_to_mongo(
            year=store.year, round_nr=store.round_nr, session_name=store.session_name,
            event_name=store.event_name, data_type=_data_type(tla, lap),
            data=block, version="v2",
        )
    except Exception as exc:  # persistence is best-effort
        logger.debug("Failed to persist lap_frames block %s/%s: %s", tla, lap, exc)


def _mean_lap_time(windows: Dict[str, List[Dict[str, Any]]]) -> float:
    times = [le["lap_time_s"] for laps in windows.values() for le in laps]
    return sum(times) / len(times) if times else 0.0


class TrackMapData:
    """Callable: ``TrackMapData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        store = build_session_store(y, identifier, e)
        if store is None:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="Session could not be resolved.",
            )
        track = build_track_payload(store)
        if track is None:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No circuit geometry available for this session.",
            )
        return {
            "session_info": {
                "year": store.year,
                "event_name": store.event_name,
                "session_name": store.session_name,
                "round": store.round_nr,
            },
            "track": track,
        }


__all__ = ["LapFrames", "LapsData", "TrackMapData", "DATA_TYPE"]
