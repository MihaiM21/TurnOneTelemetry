"""
Driver Performance Radar (V2).

A polar "spider" chart scoring drivers across performance dimensions — built to
be both a defensible analytics artifact and a striking, shareable graphic. Every
spoke is computed from measured data (telemetry or results) and normalized
honestly; nothing is hand-waved.

Three scopes, each with its own axis set (see :mod:`_radar_axes`):

  * **session** — one session's telemetry + timing. Cheap field-relative axes
    (top speed, race/one-lap pace, consistency) come from a single
    ``TimingData`` parse for the whole grid; the two telemetry ratio axes
    (cornering, braveness) are fetched only for the drivers being charted.
  * **season** — a whole season of jolpica results (race pace, qualifying,
    consistency, racecraft, reliability, peak). The full-field payload is built
    and cached once, then filtered to the requested drivers, so percentiles stay
    computed against the entire grid regardless of who is shown.
  * **career** — the season builder run over several years of results combined.

Public surface mirrors every other V2 feature: ``*Data`` / ``*Plot`` callables
invoked from the routers via ``run_in_threadpool``. ``build_radar_payload`` is
the pure seam shared by the renderer and the unit tests.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2 import _radar_render as radar_render
from src.services.analysis.v2._race_helpers import extract_lap_times
from src.services.analysis.v2._radar_axes import (
    AxisSpec,
    SEASON_AXES,
    SINGLE_SESSION_AXES,
    season_field_metrics,
    single_session_field_metrics,
    telemetry_ratios_for_driver,
)
from src.services.analysis.v2._radar_scale import scale_value
from src.services.analysis.v2._season_helpers import (
    fetch_season_results,
    season_cached_or_generate,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import colors as colors_module

logger = get_logger(__name__)

_MAX_DRIVERS = 3
_DEFAULT_SESSION_DRIVERS = 3
_DEFAULT_SEASON_DRIVERS = 3
_PERCENTILE_METHOD = "percentile"


# ----------------------------------------------------------------------
# Pure payload builder (unit-testable without any network)
# ----------------------------------------------------------------------
def build_radar_payload(
    scope: str,
    axes: Sequence[AxisSpec],
    rows: List[Dict[str, Any]],
    population: Optional[Dict[str, List[float]]] = None,
) -> Dict[str, Any]:
    """Assemble a scaled radar payload from raw per-driver metrics.

    ``rows`` is ``[{tla, team, color, raw: {axis_key: value}}, ...]`` for the
    drivers to chart. ``population`` maps each percentile axis key to the full
    field's raw values (so percentiles are honest even when only a few drivers
    are shown); it is ignored for ``"absolute"`` axes. Returns
    ``{scope, axes: [name...], drivers: [{tla, team, color, values, raw}], hero}``
    where ``values`` are 0-100 (or ``None`` for a missing spoke), aligned to
    ``axes`` order.
    """
    population = population or {}
    drivers_payload: List[Dict[str, Any]] = []

    for row in rows:
        raw = row.get("raw", {})
        values: List[Optional[float]] = []
        for axis in axes:
            pop = population.get(axis.key, [])
            values.append(
                scale_value(
                    raw.get(axis.key), pop,
                    higher_is_better=axis.higher_is_better,
                    method=axis.method, lo=axis.lo, hi=axis.hi,
                )
            )
        drivers_payload.append({
            "tla": row["tla"],
            "team": row.get("team", ""),
            "color": row.get("color", "#777777"),
            "values": values,
            "raw": {axis.key: raw.get(axis.key) for axis in axes},
        })

    return {
        "scope": scope,
        "axes": [axis.name for axis in axes],
        "drivers": drivers_payload,
        "hero": len(drivers_payload) == 1,
    }


def _population_from_field(
    field: Dict[str, Dict[str, Optional[float]]], axes: Sequence[AxisSpec]
) -> Dict[str, List[float]]:
    """Collect the full-field raw values per percentile axis for normalization."""
    population: Dict[str, List[float]] = {}
    for axis in axes:
        if axis.method != _PERCENTILE_METHOD:
            continue
        population[axis.key] = [
            m[axis.key] for m in field.values()
            if m.get(axis.key) is not None
        ]
    return population


def _parse_drivers_arg(drivers: Optional[Union[str, Sequence[str]]]) -> List[str]:
    if not drivers:
        return []
    if isinstance(drivers, str):
        parts = drivers.split(",")
    else:
        parts = list(drivers)
    return [p.strip().upper() for p in parts if p and p.strip()]


# ----------------------------------------------------------------------
# Single-session scope
# ----------------------------------------------------------------------
def _resolve_session_nums(
    driver_list: Dict[str, Dict[str, Any]], tlas: List[str],
    year: int, identifier: Any, session: str,
) -> List[str]:
    """Map requested TLAs to car numbers, preserving request order."""
    by_tla = {str(info.get("tla", "")).upper(): num for num, info in driver_list.items()}
    nums: List[str] = []
    unknown: List[str] = []
    for tla in tlas:
        num = by_tla.get(tla)
        if num is None:
            unknown.append(tla)
        else:
            nums.append(num)
    if unknown:
        valid = ", ".join(sorted(by_tla))
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session, source="livetiming",
            reason=f"Unknown driver(s) {', '.join(unknown)}. Valid drivers: {valid}",
        )
    return nums


def _default_session_nums(
    field: Dict[str, Dict[str, Optional[float]]], n: int
) -> List[str]:
    """Default charted drivers: fastest by best single lap (``quali_pace``)."""
    ranked = sorted(
        (num for num, m in field.items() if m.get("quali_pace") is not None),
        key=lambda num: field[num]["quali_pace"],
    )
    return ranked[:n]


def _build_session_payload(
    y: int, identifier: Union[int, str], session: str, tlas: List[str]
) -> Dict[str, Any]:
    store = SessionDataStore(y, identifier, session)
    timing_entries = store.timing_data()
    lap_times = extract_lap_times(store)
    field = single_session_field_metrics(timing_entries, lap_times)
    driver_list = store.driver_list()

    if tlas:
        nums = _resolve_session_nums(driver_list, tlas, y, identifier, session)[:_MAX_DRIVERS]
    else:
        nums = _default_session_nums(field, _DEFAULT_SESSION_DRIVERS)

    if not nums:
        raise DataNotAvailableError(
            year=y, gp=identifier, session=session, source="livetiming",
            reason="No drivers with timing data available for a radar",
        )

    base_url, client = store.base_url, store.client
    rows: List[Dict[str, Any]] = []
    for num in nums:
        info = driver_list.get(num, {})
        tla = str(info.get("tla", num)).upper()
        team = info.get("team", "Unknown")
        raw = dict(field.get(num, {}))
        raw.update(telemetry_ratios_for_driver(base_url, client, num))
        rows.append({
            "tla": tla,
            "team": team,
            "color": info.get("color") or colors_module.get_driver_color(tla),
            "raw": raw,
        })

    population = _population_from_field(field, SINGLE_SESSION_AXES)
    return build_radar_payload("session", SINGLE_SESSION_AXES, rows, population)


class DriverRadarData:
    """Callable: ``DriverRadarData()(year, identifier, session, drivers=None) -> dict``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        drivers: Optional[Union[str, Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        tlas = _parse_drivers_arg(drivers)[:_MAX_DRIVERS]
        key = "_".join(sorted(tlas)) if tlas else "auto"

        def _generate() -> Dict[str, Any]:
            return _build_session_payload(y, identifier, e, tlas)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=f"driver_radar_{key}", generator=_generate, version="v2",
        )


class DriverRadarPlot:
    """Callable: ``DriverRadarPlot()(year, identifier, session, drivers=None, portrait=False) -> png``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        drivers: Optional[Union[str, Sequence[str]]] = None,
        portrait: bool = False,
    ) -> str:
        payload = DriverRadarData()(y, identifier, e, drivers)
        if not payload.get("drivers"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No driver radar data available to plot",
            )
        store = SessionDataStore(y, identifier, e)
        subtitle = f"{y} {store.event_name} {e}"
        return radar_render.render_radar(
            payload, title="Driver Performance Radar", subtitle=subtitle,
            scope="session", year=y, event=store.event_name, session=e,
            portrait=portrait,
        )


# ----------------------------------------------------------------------
# Season / career scope
# ----------------------------------------------------------------------
def _season_meta(rows: List[Dict[str, Any]], driver: str) -> Dict[str, Any]:
    for row in rows:
        if row.get("driver_code") == driver:
            team = row.get("constructor", "")
            return {
                "team": team,
                "color": colors_module.get_team_color(team) if team else "#777777",
            }
    return {"team": "", "color": "#777777"}


def _build_season_full_payload(
    race_rows: List[Dict[str, Any]],
    quali_rows: List[Dict[str, Any]],
    scope: str,
) -> Dict[str, Any]:
    """Full-field season/career payload (every driver, scaled vs full field)."""
    field = season_field_metrics(race_rows, quali_rows)
    all_rows = race_rows + quali_rows

    rows: List[Dict[str, Any]] = []
    for driver in sorted(field):
        meta = _season_meta(all_rows, driver)
        rows.append({
            "tla": driver,
            "team": meta["team"],
            "color": meta["color"],
            "raw": field[driver],
        })

    population = _population_from_field(field, SEASON_AXES)
    return build_radar_payload(scope, SEASON_AXES, rows, population)


def _default_season_drivers(payload: Dict[str, Any], n: int) -> List[str]:
    """Default charted drivers: best race pace (lowest raw ``race_pace``)."""
    ranked = sorted(
        (d for d in payload["drivers"] if d["raw"].get("race_pace") is not None),
        key=lambda d: d["raw"]["race_pace"],
    )
    return [d["tla"] for d in ranked[:n]]


def _filter_radar_drivers(payload: Dict[str, Any], selected: List[str]) -> Dict[str, Any]:
    wanted = set(selected)
    filtered = [d for d in payload["drivers"] if d["tla"] in wanted]
    return {
        "scope": payload["scope"],
        "axes": payload["axes"],
        "drivers": filtered,
        "hero": len(filtered) == 1,
    }


class SeasonRadarData:
    """Callable: ``SeasonRadarData()(year, drivers=None) -> dict``.

    The full-field payload is cached once per season; an explicit ``drivers``
    selection is applied AFTER retrieval so filters share one cached payload.
    """

    def __call__(
        self, y: int, drivers: Optional[Union[str, Sequence[str]]] = None
    ) -> Dict[str, Any]:
        def _generate() -> Dict[str, Any]:
            race_rows = fetch_season_results(y, "race")
            quali_rows = fetch_season_results(y, "qualifying")
            return _build_season_full_payload(race_rows, quali_rows, "season")

        payload = season_cached_or_generate(y, "driver_radar_season", _generate)

        selected = _parse_drivers_arg(drivers)[:_MAX_DRIVERS]
        if not selected:
            selected = _default_season_drivers(payload, _DEFAULT_SEASON_DRIVERS)
        return _filter_radar_drivers(payload, selected)


class SeasonRadarPlot:
    """Callable: ``SeasonRadarPlot()(year, drivers=None, portrait=False) -> png``."""

    def __call__(
        self, y: int, drivers: Optional[Union[str, Sequence[str]]] = None,
        portrait: bool = False,
    ) -> str:
        payload = SeasonRadarData()(y, drivers)
        if not payload.get("drivers"):
            raise DataNotAvailableError(
                year=y, gp="season", session="Season", source="jolpica",
                reason="No season radar data available",
            )
        return radar_render.render_radar(
            payload, title="Season Performance Radar", subtitle=f"{y} Season",
            scope="season", year=y, portrait=portrait,
        )


def _career_years(years: Optional[Union[str, Sequence[int]]], default_year: int) -> List[int]:
    """Parse a ``years`` argument: "2021-2024", "2021,2023", or a list."""
    if not years:
        return [default_year]
    if isinstance(years, (list, tuple)):
        return [int(y) for y in years]
    text = str(years).strip()
    if "-" in text:
        lo, hi = text.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(y) for y in text.split(",") if y.strip()]


class CareerRadarData:
    """Callable: ``CareerRadarData()(years, drivers=None) -> dict``.

    Runs the season builder over several years of combined results. Cached under
    the year span; treated as immutable once the latest year is complete.
    """

    def __call__(
        self, years: Union[str, Sequence[int]],
        drivers: Optional[Union[str, Sequence[str]]] = None,
        default_year: int = 0,
    ) -> Dict[str, Any]:
        year_list = _career_years(years, default_year)
        span = f"{min(year_list)}_{max(year_list)}"

        def _generate() -> Dict[str, Any]:
            race_rows: List[Dict[str, Any]] = []
            quali_rows: List[Dict[str, Any]] = []
            for yr in year_list:
                race_rows.extend(fetch_season_results(yr, "race"))
                quali_rows.extend(fetch_season_results(yr, "qualifying"))
            return _build_season_full_payload(race_rows, quali_rows, "career")

        # Cache against the newest year so a still-running season stays volatile.
        payload = season_cached_or_generate(max(year_list), f"driver_radar_career_{span}", _generate)

        selected = _parse_drivers_arg(drivers)[:_MAX_DRIVERS]
        if not selected:
            selected = _default_season_drivers(payload, _DEFAULT_SEASON_DRIVERS)
        return _filter_radar_drivers(payload, selected)


class CareerRadarPlot:
    """Callable: ``CareerRadarPlot()(years, drivers=None, portrait=False) -> png``."""

    def __call__(
        self, years: Union[str, Sequence[int]],
        drivers: Optional[Union[str, Sequence[str]]] = None,
        portrait: bool = False, default_year: int = 0,
    ) -> str:
        payload = CareerRadarData()(years, drivers, default_year=default_year)
        if not payload.get("drivers"):
            raise DataNotAvailableError(
                year=max(_career_years(years, default_year)), gp="career", session="Career",
                source="jolpica", reason="No career radar data available",
            )
        year_list = _career_years(years, default_year)
        span = f"{min(year_list)}-{max(year_list)}"
        return radar_render.render_radar(
            payload, title="Career Performance Radar", subtitle=f"Seasons {span}",
            scope="career", year=max(year_list), span=span, portrait=portrait,
        )


if __name__ == "__main__":
    print("Testing V2 Driver Radar...")
    try:
        data = DriverRadarData()(2025, 1, "R", "VER,NOR")
        print(f"Session radar drivers: {[d['tla'] for d in data['drivers']]}")
        print(f"Axes: {data['axes']}")
        for d in data["drivers"]:
            print(f"  {d['tla']}: {d['values']}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
