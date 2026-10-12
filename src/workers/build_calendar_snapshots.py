"""Generate the committed historical calendar snapshots (``domain/data/calendars/{year}.json``).

    python -m src.workers.build_calendar_snapshots --range 2018 2025
    python -m src.workers.build_calendar_snapshots 2024 --dry-run

Each snapshot merges the livetiming index (identity + session paths, when livetiming
serves the year), the curated schedule (2025/2026) and the Jolpica schedule (official
championship round numbers) through :func:`src.ingestion.calendar.build_calendar`.
Years livetiming no longer serves (2022 answers 403) or truncates (2018, 2024) still
come out complete because the Jolpica rows fill the missing rounds.

Historical calendars never change, so run this once per finished season and commit the
result; only the current year is built live.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

import requests

from src.core.exceptions import DataNotAvailableError, SessionNotFoundError, UpstreamUnavailableError
from src.core.logging import get_logger
from src.domain.data.seasons import f1_2025_races_data, f1_2026_races_data
from src.ingestion import calendar as cal
from src.ingestion.reference import fetch_livetiming_index

logger = get_logger(__name__)

JOLPICA_URL = "https://api.jolpi.ca/ergast/f1/{year}.json"
_CURATED = {2025: f1_2025_races_data, 2026: f1_2026_races_data}


def fetch_reference_schedule(year: int) -> list[dict]:
    resp = requests.get(JOLPICA_URL.format(year=year), params={"limit": 100}, timeout=30)
    resp.raise_for_status()
    races = resp.json()["MRData"]["RaceTable"]["Races"]
    return cal.adapt_reference_schedule(races)


def build_year(year: int) -> list[dict]:
    overrides = cal.load_overrides()
    curated_raw = _CURATED.get(year)
    curated = cal.adapt_curated(curated_raw, overrides) if curated_raw else None

    live: Optional[list[dict]] = None
    try:
        live = cal.adapt_livetiming_index(fetch_livetiming_index(year))
    except (SessionNotFoundError, DataNotAvailableError, UpstreamUnavailableError) as exc:
        logger.warning("%s: no livetiming index (%s); calendar will have no meeting keys", year, exc)

    reference = fetch_reference_schedule(year)
    if not reference:
        raise RuntimeError(f"Jolpica returned no races for {year}; refusing to write a guess")
    return cal.build_calendar(year, live=live, curated=curated, reference=reference, overrides=overrides)


def _summary(events: list[dict]) -> str:
    races = [e for e in events if e["kind"] == cal.KIND_RACE and e["status"] != cal.STATUS_CANCELLED]
    no_key = [e["grandPrix"] for e in races if e.get("key") is None]
    return f"{len(races)} races, {sum(e['kind'] == cal.KIND_TESTING for e in events)} testing, " \
           f"{sum(e['status'] == cal.STATUS_CANCELLED for e in events)} cancelled, no livetiming key: {len(no_key)}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("years", nargs="*", type=int, help="years to build")
    parser.add_argument("--range", dest="span", nargs=2, type=int, metavar=("FIRST", "LAST"))
    parser.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    args = parser.parse_args(argv)
    years = list(args.years)
    if args.span:
        years += list(range(args.span[0], args.span[1] + 1))
    if not years:
        parser.error("give at least one year")

    for year in years:
        events = build_year(year)
        print(f"{year}: {_summary(events)}")  # CLI output is the interface here
        if not args.dry_run:
            print(f"  wrote {cal.dump_snapshot(year, events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
