"""Verbatim raw-stream export for the dataset export ``raw`` tier.

Dumps every livetiming stream a :class:`SessionDataStore` exposes, unmodified,
as gzip-compressed JSON Lines (list streams) or plain JSON (dict payloads).
This is separate from the ``telemetry``/``tables`` tiers, which reshape the
same underlying data into flat, typed columns -- ``raw`` exists for consumers
who want the original livetiming shape instead.
"""

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.v2.session_store import EXTRA_STREAMS, SessionDataStore
from src.services.dataset_export.writer import FileStat, write_json, write_jsonl_gz

logger = get_logger(__name__)


def _extra_accessor(name: str) -> Callable[[SessionDataStore], Any]:
    return lambda store: store.extra_stream(name)


# Named list-stream accessors, in write order. The two big compressed streams
# are listed last deliberately -- ``write_raw_streams`` relies on RAW_STREAMS'
# iteration order to write them after everything else.
RAW_STREAMS: Dict[str, Callable[[SessionDataStore], Any]] = {
    "timing_data": lambda store: store.timing_data(),
    "timing_app_data": lambda store: store.timing_app_data(),
    "track_status": lambda store: store.track_status(),
    "weather_data": lambda store: store.weather_data(),
    "race_control": lambda store: store.race_control(),
    **{name: _extra_accessor(name) for name in EXTRA_STREAMS},
    "car_data": lambda store: store.car_data(),
    "position_data": lambda store: store.position_data(),
}

# Dict payloads, written as plain (uncompressed) JSON documents.
JSON_DOCS: Tuple[str, ...] = ("session_info", "driver_list")

# The big streams whose in-process cache entry should be dropped right after
# writing, so a full-session raw export doesn't pin both decoded streams
# (tens of megabytes each) in memory for the rest of the export run.
_BIG_STREAMS = ("car_data", "position_data")


def write_raw_streams(
    store: SessionDataStore,
    dest: Path,
    root: Path,
    streams: Optional[Iterable[str]] = None,
    *,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[Dict[str, FileStat], List[str]]:
    """Write every requested raw stream for ``store`` under ``dest``.

    Args:
        store: Resolved session store to read streams from.
        dest: Directory to write files into (created as needed).
        root: Export root, for relative-pathing the returned ``FileStat``s.
        streams: Names to export (any key of :data:`RAW_STREAMS` or
            :data:`JSON_DOCS`). Defaults to all of them, in a fixed order with
            the two big compressed streams written last.
        cancelled: Optional zero-arg callable polled between streams; when it
            returns True, the function stops and returns what was written so
            far.

    Returns:
        ``(written, absent)`` -- a dict of stream name to :class:`FileStat` for
        everything written, and a list of stream names that raised
        :class:`DataNotAvailableError` (normal for older seasons missing a
        stream). Any other exception propagates.
    """
    if streams is None:
        names = list(RAW_STREAMS.keys()) + list(JSON_DOCS)
    else:
        names = list(streams)

    written: Dict[str, FileStat] = {}
    absent: List[str] = []

    for name in names:
        if cancelled is not None and cancelled():
            break

        try:
            if name in JSON_DOCS:
                payload = getattr(store, name)()
                stat = write_json(payload, dest / f"{name}.json", root)
            elif name in RAW_STREAMS:
                records = RAW_STREAMS[name](store)
                stat = write_jsonl_gz(records, dest / f"{name}.jsonl.gz", root)
            else:
                logger.warning("Unknown raw stream requested: %s", name)
                continue
        except DataNotAvailableError as exc:
            logger.debug("Raw stream %s not available: %s", name, exc)
            absent.append(name)
            continue

        written[name] = stat

        if name in _BIG_STREAMS:
            cache = getattr(store, "_cache", None)
            if cache is not None:
                cache.pop(name, None)

    return written, absent


__all__ = ["RAW_STREAMS", "JSON_DOCS", "write_raw_streams"]
