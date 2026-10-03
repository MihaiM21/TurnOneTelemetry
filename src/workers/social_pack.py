"""Admin "social pack" jobs: every relevant chart for one session, in social formats.

After a session the operator picks ``(year, gp, session)``, a set of formats
and optionally some driver pairs, and gets one folder holding every chart that
applies to that session type, a ``highlights.json`` with the numbers worth
quoting, a ``manifest.json`` (items, per-chart errors, timings) and a
``social_pack.zip`` of the lot::

    outputs/social/{year}/{EventNoSpaces}/{session}/
        {format}/{slug}.png
        highlights.json
        manifest.json
        social_pack.zip

Job model mirrors ``dataset_export``: durable in ``admin_jobs`` under its own
``kind``, a hot in-memory cache for the owning process, throttled progress via
``JobWriter`` and cooperative cancellation. Unlike the export there is no
concurrency at all: every chart of one session runs sequentially inside a
single ``shared_session_stores()`` scope so the multi-megabyte livetiming
streams are parsed once, not once per chart. A failing chart is logged,
recorded as a per-item error and does not fail the job.

Only one social-pack job runs at a time.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from src.core.config import settings
from src.core.logging import get_logger
from src.repositories import admin_jobs
from src.services.analysis.v2._helpers import (
    build_session_store,
    get_finishing_order,
    get_qualifying_classification,
    shared_session_stores,
)
from src.services.plotting.output import UnsafeOutputPath, resolve_within
from src.workers._job_progress import CANCELLED, JobWriter, bump_metric, now

logger = get_logger(__name__)

JOB_KIND = "social_pack"

#: Mirrors ``services/plotting/canvas.py:FORMAT_NAMES`` (a test pins the two together)
#: so planning and request validation don't import matplotlib.
FORMAT_NAMES: Tuple[str, ...] = ("landscape", "square", "portrait", "story")
DEFAULT_FORMATS: Tuple[str, ...] = ("portrait", "story", "landscape")

SESSION_ABBREVS: Tuple[str, ...] = ("FP1", "FP2", "FP3", "Q", "SQ", "S", "R")
_SESSION_ALIASES = {
    "PRACTICE 1": "FP1", "PRACTICE 2": "FP2", "PRACTICE 3": "FP3",
    "QUALIFYING": "Q", "SPRINT QUALIFYING": "SQ", "SPRINT": "S", "RACE": "R",
}
_QUALI = frozenset({"Q", "SQ"})
_RACE = frozenset({"R", "S"})
_ALL = frozenset(SESSION_ABBREVS)

ENERGY_CLIPPING_FIRST_YEAR = 2026
MAX_DERIVED_PAIRS = 4
MAX_REQUESTED_PAIRS = 10

ZIP_NAME = "social_pack.zip"
HIGHLIGHTS_NAME = "highlights.json"
MANIFEST_NAME = "manifest.json"

_TLA_RE = re.compile(r"^[A-Za-z]{3}$")
_GP_RE = re.compile(r"^[A-Za-z0-9 ._'-]{1,80}$")
_JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")

Pair = Tuple[str, str]


# --------------------------------------------------------------------------- #
# Chart catalogue
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChartSpec:
    """One chart the pack can render."""

    slug: str
    sessions: frozenset
    kind: str = "session"          # session | pair | season
    min_year: Optional[int] = None
    has_highlights: bool = False


#: Order is render order. Session singletons first, then pairs, then season.
CHARTS: Tuple[ChartSpec, ...] = (
    ChartSpec("quali_results", _QUALI),
    ChartSpec("theoretical_best", _QUALI),
    ChartSpec("sector_gap", _QUALI, has_highlights=True),
    ChartSpec("race_story", _RACE),
    ChartSpec("position_changes", _RACE),
    ChartSpec("pit_strategy", _RACE),
    ChartSpec("top_speed", _ALL),
    ChartSpec("energy_clipping", _ALL, min_year=ENERGY_CLIPPING_FIRST_YEAR, has_highlights=True),
    ChartSpec("field_dominance", _ALL, has_highlights=True),
    ChartSpec("corner_speed_profile", _ALL, has_highlights=True),
    ChartSpec("efficiency_scatter", _ALL, has_highlights=True),
    ChartSpec("lap_duel", _ALL, kind="pair", has_highlights=True),
    ChartSpec("track_comparison", _ALL, kind="pair"),
    ChartSpec("teammate_battle", _ALL, kind="season"),
)
_CHARTS_BY_SLUG = {c.slug: c for c in CHARTS}


@dataclass(frozen=True)
class SocialTask:
    """One render: a chart in one format (for one driver pair, if it is a pair chart)."""

    chart: str
    fmt: str
    drivers: Optional[Pair] = None

    @property
    def spec(self) -> ChartSpec:
        return _CHARTS_BY_SLUG[self.chart]

    @property
    def slug(self) -> str:
        """File stem and highlights key: ``lap_duel_VER_NOR`` for pair charts."""
        if self.drivers:
            return f"{self.chart}_{self.drivers[0]}_{self.drivers[1]}"
        if self.spec.kind == "season":
            return f"season_{self.chart}"
        return self.chart

    @property
    def label(self) -> str:
        return f"{self.slug} {self.fmt}"


# --------------------------------------------------------------------------- #
# Validation / normalisation
# --------------------------------------------------------------------------- #
def normalize_session(session: str) -> str:
    """Canonical session abbreviation (``Race`` -> ``R``); ``ValueError`` if unknown."""
    key = str(session or "").strip().upper()
    key = _SESSION_ALIASES.get(key, key)
    if key not in SESSION_ABBREVS:
        raise ValueError(f"unknown session {session!r}; choose from {', '.join(SESSION_ABBREVS)}")
    return key


def normalize_formats(formats: Optional[Sequence[str]]) -> List[str]:
    wanted = [str(f).strip().lower() for f in (formats or DEFAULT_FORMATS) if str(f).strip()]
    bad = [f for f in wanted if f not in FORMAT_NAMES]
    if bad:
        raise ValueError(f"unknown format(s) {bad}; choose from {', '.join(FORMAT_NAMES)}")
    seen: List[str] = []
    for f in wanted:
        if f not in seen:
            seen.append(f)
    if not seen:
        raise ValueError("at least one format is required")
    return [f for f in FORMAT_NAMES if f in seen]


def normalize_pairs(pairs: Optional[Sequence[Sequence[str]]]) -> Optional[List[Pair]]:
    """``None`` stays ``None`` (derive later); otherwise upper-cased, validated, de-duplicated."""
    if pairs is None:
        return None
    out: List[Pair] = []
    for pair in pairs:
        if len(pair) != 2:
            raise ValueError(f"a pair needs exactly two drivers (got {list(pair)!r})")
        d1, d2 = (str(x).strip().upper() for x in pair)
        if not (_TLA_RE.match(d1) and _TLA_RE.match(d2)):
            raise ValueError(f"drivers must be 3-letter codes (got {d1!r}, {d2!r})")
        if d1 == d2:
            raise ValueError(f"a pair needs two different drivers (got {d1!r} twice)")
        if (d1, d2) not in out:
            out.append((d1, d2))
    if len(out) > MAX_REQUESTED_PAIRS:
        raise ValueError(f"at most {MAX_REQUESTED_PAIRS} pairs per pack")
    return out


def normalize_gp(gp: Union[int, str]) -> Union[int, str]:
    if isinstance(gp, bool):
        raise ValueError("gp must be a round number or event name")
    if isinstance(gp, int):
        if not (settings.min_round <= gp <= settings.max_round):
            raise ValueError(f"gp round must be between {settings.min_round} and {settings.max_round}")
        return gp
    text = str(gp).strip()
    if text.isdigit():
        return normalize_gp(int(text))
    if not _GP_RE.match(text):
        raise ValueError(f"invalid gp {gp!r}")
    return text


def parse_pairs_text(text: str) -> Optional[List[Pair]]:
    """``"VER,NOR"`` per line (UI textarea) -> pairs; blank -> ``None`` (derive)."""
    rows = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not rows:
        return None
    return normalize_pairs([_split_pair(row) for row in rows])


def _split_pair(row: str) -> List[str]:
    parts = [p for p in re.split(r"[\s,;]+", row.strip()) if p]
    if len(parts) != 2:
        raise ValueError(f"expected 'VER,NOR' (got {row!r})")
    return parts


def event_dir_name(event_name: str) -> str:
    """``Italian Grand Prix`` -> ``ItalianGrandPrix``; nothing outside ``[A-Za-z0-9_-]``."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(event_name or ""))
    return cleaned or "Event"


# --------------------------------------------------------------------------- #
# Plan (pure)
# --------------------------------------------------------------------------- #
def plan_social_pack(
    year: int,
    gp: Union[int, str],
    session: str,
    formats: Optional[Sequence[str]] = None,
    pairs: Optional[Sequence[Sequence[str]]] = None,
    include_season: bool = False,
) -> List[SocialTask]:
    """Charts x formats for one session, by session type.

    Pure: no I/O. ``pairs=None`` (or empty) plans **no** pair charts -- the run
    derives pairs from the session's classification and plans again with them
    (see :func:`derive_pairs`). Energy clipping is dropped for ``year < 2026``
    (it is measured for the 2026 power units only). ``gp`` is accepted for
    symmetry with the run signature and future per-event gating; it does not
    change the selection today.
    """
    sess = normalize_session(session)
    fmts = normalize_formats(formats)
    pair_list = normalize_pairs(pairs) or []
    year = int(year)

    tasks: List[SocialTask] = []
    for spec in CHARTS:
        if sess not in spec.sessions:
            continue
        if spec.min_year is not None and year < spec.min_year:
            continue
        if spec.kind == "season" and not include_season:
            continue
        if spec.kind == "pair":
            for pair in pair_list:
                tasks.extend(SocialTask(spec.slug, f, pair) for f in fmts)
        else:
            tasks.extend(SocialTask(spec.slug, f) for f in fmts)
    return tasks


def gated_out(year: int, session: str) -> List[str]:
    """Charts that apply to this session type but are skipped for ``year`` (for job warnings)."""
    sess = normalize_session(session)
    return [c.slug for c in CHARTS
            if sess in c.sessions and c.min_year is not None and int(year) < c.min_year]


# --------------------------------------------------------------------------- #
# Pair derivation
# --------------------------------------------------------------------------- #
def classification_order(store: Any, session: str) -> List[str]:
    """Car numbers in classification order (P1 first).

    Qualifying uses the official classification helper; everything else the
    final ``TimingData`` positions (race result, or fastest-lap order in practice).
    """
    if normalize_session(session) in _QUALI:
        df = get_qualifying_classification(store.base_url, store.client, store=store)
        if df is None or getattr(df, "empty", True):
            return []
        return [str(n) for n in df["DriverNum"].tolist()]
    order = get_finishing_order(store.base_url, store.client, store=store)
    return [str(n) for n in sorted(order, key=lambda n: order[n])]


def derive_pairs(store: Any, session: str, max_pairs: int = MAX_DERIVED_PAIRS) -> List[Pair]:
    """P1 vs P2, then each of the top three against their teammate.

    Order of preference: ``(P1, P2)``, ``(P1, mate)``, ``(P2, mate)``, ``(P3, mate)``.
    Pairs are de-duplicated as unordered sets (P1 and P2 are often teammates, and
    then ``(P1, mate)`` is the same duel) and capped at ``max_pairs``. The first
    driver of each pair is the higher-classified one.
    """
    drivers = store.driver_list()
    order = [n for n in classification_order(store, session) if str(n) in drivers]
    tla = {str(n): str(info.get("tla") or "").strip().upper() for n, info in drivers.items()}
    team = {str(n): info.get("team") for n, info in drivers.items()}

    def teammate_of(num: str) -> Optional[str]:
        mates = [n for n in order if n != num and team.get(n) and team.get(n) == team.get(num)]
        if not mates:  # a teammate who did not classify still makes a duel
            mates = [n for n in drivers if str(n) != num and team.get(str(n)) == team.get(num)
                     and team.get(num)]
        return str(mates[0]) if mates else None

    candidates: List[Tuple[str, str]] = []
    if len(order) >= 2:
        candidates.append((order[0], order[1]))
    for num in order[:3]:
        mate = teammate_of(num)
        if mate:
            candidates.append((num, mate))

    out: List[Pair] = []
    seen = set()
    for a, b in candidates:
        d1, d2 = tla.get(a, ""), tla.get(b, "")
        if not (_TLA_RE.match(d1) and _TLA_RE.match(d2)) or d1 == d2:
            continue
        key = frozenset((d1, d2))
        if key in seen:
            continue
        seen.add(key)
        out.append((d1, d2))
        if len(out) >= max_pairs:
            break
    return out


# --------------------------------------------------------------------------- #
# Chart callers (lazy imports; tests monkeypatch these dicts)
# --------------------------------------------------------------------------- #
# Each takes ``(year, gp, session, task)`` and returns the PNG path (or ``""``).
def _p_quali_results(y, gp, e, t):
    from src.services.analysis.v2.qualifying_results import QualiResultsPlot
    return QualiResultsPlot(y, gp, e, fmt=t.fmt)


def _p_theoretical_best(y, gp, e, t):
    from src.services.analysis.v2.theoretical_best import TheoreticalBestPlot
    return TheoreticalBestPlot()(y, gp, e, fmt=t.fmt)


def _p_sector_gap(y, gp, e, t):
    from src.services.analysis.v2.sector_gap import SectorGapPlot
    return SectorGapPlot()(y, gp, e, fmt=t.fmt)


def _p_top_speed(y, gp, e, t):
    from src.services.analysis.v2.top_speed import TopSpeedPlot_Telemetry
    return TopSpeedPlot_Telemetry(y, gp, e, fmt=t.fmt)


def _p_race_story(y, gp, e, t):
    from src.services.analysis.v2.race_story import RaceStoryPlot
    return RaceStoryPlot()(y, gp, e, fmt=t.fmt)


def _p_position_changes(y, gp, e, t):
    from src.services.analysis.v2.position_changes import PositionChangesPlot
    return PositionChangesPlot()(y, gp, e, fmt=t.fmt)


def _p_pit_strategy(y, gp, e, t):
    from src.services.analysis.v2.pit_strategy import PitStrategyPlot
    return PitStrategyPlot()(y, gp, e, fmt=t.fmt)


def _p_energy_clipping(y, gp, e, t):
    from src.services.analysis.v2.energy_clipping import EnergyClippingPlot
    return EnergyClippingPlot()(y, gp, e, driver=None, fmt=t.fmt)


def _p_field_dominance(y, gp, e, t):
    from src.services.analysis.v2.field_dominance import FieldDominancePlot
    return FieldDominancePlot()(y, gp, e, mode="team", fmt=t.fmt)


def _p_corner_speed_profile(y, gp, e, t):
    from src.services.analysis.v2.car_characteristics import CornerSpeedProfilePlot
    return CornerSpeedProfilePlot()(y, gp, e, fmt=t.fmt)


def _p_efficiency_scatter(y, gp, e, t):
    from src.services.analysis.v2.car_characteristics import EfficiencyScatterPlot
    return EfficiencyScatterPlot()(y, gp, e, fmt=t.fmt)


def _p_lap_duel(y, gp, e, t):
    from src.services.analysis.v2.lap_duel import LapDuelPlot
    return LapDuelPlot()(y, gp, e, t.drivers[0], t.drivers[1], fmt=t.fmt)


def _p_track_comparison(y, gp, e, t):
    from src.services.analysis.v2.track_comparison import TrackComparisonPlot
    return TrackComparisonPlot(y, gp, e, t.drivers[0], t.drivers[1], fmt=t.fmt)


def _p_teammate_battle(y, gp, e, t):
    from src.services.analysis.v2.teammate_battle import TeammateBattlePlot
    return TeammateBattlePlot()(y, fmt=t.fmt)


PLOT_CALLERS: Dict[str, Callable[..., Any]] = {
    "quali_results": _p_quali_results,
    "theoretical_best": _p_theoretical_best,
    "sector_gap": _p_sector_gap,
    "top_speed": _p_top_speed,
    "race_story": _p_race_story,
    "position_changes": _p_position_changes,
    "pit_strategy": _p_pit_strategy,
    "energy_clipping": _p_energy_clipping,
    "field_dominance": _p_field_dominance,
    "corner_speed_profile": _p_corner_speed_profile,
    "efficiency_scatter": _p_efficiency_scatter,
    "lap_duel": _p_lap_duel,
    "track_comparison": _p_track_comparison,
    "teammate_battle": _p_teammate_battle,
}


# Data callables whose payload carries a ``highlights`` key. Cached by the plot
# call that ran just before, so these are cheap.
def _d_sector_gap(y, gp, e, t):
    from src.services.analysis.v2.sector_gap import SectorGapData
    return SectorGapData()(y, gp, e)


def _d_energy_clipping(y, gp, e, t):
    from src.services.analysis.v2.energy_clipping import EnergyClippingData
    return EnergyClippingData()(y, gp, e)


def _d_field_dominance(y, gp, e, t):
    from src.services.analysis.v2.field_dominance import FieldDominanceData
    return FieldDominanceData()(y, gp, e, mode="team")


def _d_corner_speed_profile(y, gp, e, t):
    from src.services.analysis.v2.car_characteristics import CornerSpeedProfileData
    return CornerSpeedProfileData()(y, gp, e)


def _d_efficiency_scatter(y, gp, e, t):
    from src.services.analysis.v2.car_characteristics import EfficiencyScatterData
    return EfficiencyScatterData()(y, gp, e)


def _d_lap_duel(y, gp, e, t):
    from src.services.analysis.v2.lap_duel import LapDuelData
    return LapDuelData()(y, gp, e, t.drivers[0], t.drivers[1])


DATA_CALLERS: Dict[str, Callable[..., Any]] = {
    "sector_gap": _d_sector_gap,
    "energy_clipping": _d_energy_clipping,
    "field_dominance": _d_field_dominance,
    "corner_speed_profile": _d_corner_speed_profile,
    "efficiency_scatter": _d_efficiency_scatter,
    "lap_duel": _d_lap_duel,
}


# --------------------------------------------------------------------------- #
# Filesystem layout
# --------------------------------------------------------------------------- #
def social_root() -> Path:
    root = Path(settings.social_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def pack_relpath(year: int, event_name: str, session: str) -> str:
    return f"{int(year)}/{event_dir_name(event_name)}/{session}"


def pack_dir(rel: str) -> Path:
    """``{social_root}/{rel}`` -- refuses to leave the root (``UnsafeOutputPath``)."""
    return resolve_within(rel, social_root())


def zip_path_for(rel: str) -> Path:
    """The pack's zip, re-resolved through the root guard.

    ``rel`` may come back from a stored job document, so it is treated as
    untrusted: anything that escapes the root raises ``UnsafeOutputPath``.
    """
    return resolve_within(f"{rel}/{ZIP_NAME}", social_root())


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _copy_png(src: str, dest: Path) -> int:
    source = Path(src)
    if not src or not source.is_file():
        raise FileNotFoundError(f"chart produced no file ({src!r})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, dest)
    return dest.stat().st_size


def build_zip(base: Path, *, top: Optional[str] = None,
              cancelled: Optional[Callable[[], bool]] = None) -> Path:
    """Zip ``base`` (``ZIP_STORED``: PNGs are already compressed) to ``base/social_pack.zip``.

    Entries sit under a single ``top`` folder (default: the folder's own name) so
    unzipping never sprays files into the current directory.

    Written as ``.zip.tmp`` and renamed on success so a half-built archive is
    never served; the tmp is removed on any failure.
    """
    cancelled = cancelled or (lambda: False)
    target = base / ZIP_NAME
    tmp = target.with_name(target.name + ".tmp")
    top = top or base.name
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.name.endswith(".tmp") or path == target:
                    continue
                if cancelled():
                    raise RuntimeError("zip build cancelled")
                zf.write(path, arcname=f"{top}/{path.relative_to(base).as_posix()}")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


# --------------------------------------------------------------------------- #
# Job tracking
# --------------------------------------------------------------------------- #
@dataclass
class SocialJob:
    job_id: str
    kind: str = JOB_KIND
    scope: Dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    total: int = 0
    done: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    current: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    pack: Optional[str] = None          # pack dir relative to the social root
    zip_ready: bool = False
    files: int = 0
    bytes_written: int = 0
    pairs: List[List[str]] = field(default_factory=list)
    formats: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "scope": self.scope,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "current": self.current,
            "pack": self.pack,
            "zip_ready": self.zip_ready,
            "files": self.files,
            "bytes_written": self.bytes_written,
            "formats": self.formats,
            "pairs": self.pairs,
            "errors": self.errors[-25:],
            "warnings": self.warnings[-50:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_JOBS: Dict[str, SocialJob] = {}
_JOBS_LOCK = threading.Lock()
_START_LOCK = threading.Lock()
_MAX_JOBS = 50
_TERMINAL = ("completed", "failed", "cancelled")
_EXTRA_FIELDS = ("pack", "zip_ready", "files", "bytes_written", "formats", "pairs")


def _register_job(job: SocialJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
        if len(_JOBS) > _MAX_JOBS:
            for jid in sorted(_JOBS, key=lambda k: _JOBS[k].started_at or ""):
                if _JOBS[jid].status in _TERMINAL:
                    del _JOBS[jid]
                    if len(_JOBS) <= _MAX_JOBS:
                        break


def get_job(job_id: str) -> Optional[SocialJob]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def get_job_dict(job_id: str) -> Optional[Dict[str, Any]]:
    job = get_job(job_id)
    if job is not None:
        return job.as_dict()
    doc = admin_jobs.get_job(job_id)
    if doc is not None and doc.get("kind") != JOB_KIND:
        return None
    return doc


def list_jobs(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    stored = admin_jobs.list_jobs(limit=limit, status=status, kind=JOB_KIND)
    with _JOBS_LOCK:
        live = {jid: job.as_dict() for jid, job in _JOBS.items()}
    merged: List[Dict[str, Any]] = []
    for doc in stored:
        current = live.pop(doc["job_id"], None)
        merged.append({**doc, **current} if current else doc)
    for extra in live.values():
        if status is None or extra.get("status") == status:
            merged.append(extra)
    merged.sort(key=lambda d: d.get("created_at") or d.get("started_at") or "", reverse=True)
    return merged[:limit]


def cancel_job(job_id: str) -> bool:
    flagged = admin_jobs.request_cancel(job_id)
    job = get_job(job_id)
    if job is not None and job.status in ("queued", "running"):
        CANCELLED.add(job_id)
        return True
    return flagged


def running_jobs() -> List[Dict[str, Any]]:
    """Live social-pack jobs, from both the process cache and Mongo."""
    docs = admin_jobs.running_jobs(kind=JOB_KIND)
    with _JOBS_LOCK:
        # The owning process's state is authoritative: it turns terminal a moment
        # before the last progress flush reaches Mongo, and a job that has already
        # finished must not block the next one in that window.
        docs = [d for d in docs
                if not (d["job_id"] in _JOBS and _JOBS[d["job_id"]].status in _TERMINAL)]
        seen = {d["job_id"] for d in docs}
        for jid, job in _JOBS.items():
            if job.status in ("queued", "running") and jid not in seen:
                docs.append(job.as_dict())
    return docs


def pack_download_path(job_id: str) -> Optional[Path]:
    """The finished zip for a job, or ``None`` (unknown job, no zip yet, or a path that escapes the root)."""
    if not _JOB_ID_RE.match(job_id or ""):
        return None
    doc = get_job_dict(job_id)
    if not doc or not doc.get("pack") or not doc.get("zip_ready"):
        return None
    try:
        path = zip_path_for(str(doc["pack"]))
    except UnsafeOutputPath:
        logger.warning("Social pack job %s has a pack path outside the social root: %r", job_id, doc.get("pack"))
        return None
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def run_social_pack(
    job_id: str,
    year: int,
    gp: Union[int, str],
    session: str,
    formats: Optional[Sequence[str]] = None,
    pairs: Optional[Sequence[Sequence[str]]] = None,
    include_season: bool = False,
    *,
    job: Optional[SocialJob] = None,
) -> SocialJob:
    """Render the pack synchronously on the calling thread and return the finished job.

    Never raises for a chart failure; a setup failure (session cannot be
    resolved) marks the job ``failed``.
    """
    year = int(year)
    session = normalize_session(session)
    gp = normalize_gp(gp)
    fmts = normalize_formats(formats)
    explicit_pairs = normalize_pairs(pairs)

    job = job or get_job(job_id) or SocialJob(job_id=job_id)
    job.status = "running"
    job.started_at = job.started_at or now()
    job.formats = fmts
    job.scope = job.scope or {"year": year, "gp": gp, "session": session, "formats": fmts,
                              "pairs": [list(p) for p in explicit_pairs] if explicit_pairs is not None else None,
                              "include_season": include_season}
    _register_job(job)
    writer = JobWriter(job, extra_fields=_EXTRA_FIELDS)
    writer.tick(force=True)

    started = time.monotonic()
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    highlights: Dict[str, Any] = {}
    base: Optional[Path] = None
    event_name = ""
    pairs_source = "request" if explicit_pairs is not None else "derived"
    final_pairs: List[Pair] = list(explicit_pairs or [])

    try:
        with shared_session_stores():
            store = build_session_store(year, gp, session)
            if store is None:
                raise RuntimeError(f"could not resolve {year} {gp} {session} in the livetiming index")
            event_name = str(getattr(store, "event_name", "") or "")
            rel = pack_relpath(year, event_name, session)
            base = pack_dir(rel)
            base.mkdir(parents=True, exist_ok=True)
            job.pack = rel

            if explicit_pairs is None:
                try:
                    final_pairs = derive_pairs(store, session)
                except Exception as exc:  # noqa: BLE001 - pairs are a bonus, not a reason to fail
                    logger.exception("Pair derivation failed for %s %s %s", year, gp, session)
                    job.warnings.append(f"pair derivation failed: {exc}")
                    final_pairs = []
                if not final_pairs:
                    job.warnings.append("no driver pairs derived; pair charts skipped")
            job.pairs = [list(p) for p in final_pairs]
            for slug in gated_out(year, session):
                job.warnings.append(f"{slug} skipped: needs {_CHARTS_BY_SLUG[slug].min_year}+ data")

            tasks = plan_social_pack(year, gp, session, fmts, final_pairs, include_season)
            job.total = len(tasks)
            writer.tick(force=True)

            for task in tasks:
                if writer.cancelled():
                    break
                job.current = task.label
                t0 = time.monotonic()
                try:
                    src = PLOT_CALLERS[task.chart](year, gp, session, task)
                    dest = resolve_within(f"{task.fmt}/{task.slug}.png", base)
                    size = _copy_png(src, dest)
                    items.append({
                        "chart": task.chart, "slug": task.slug, "format": task.fmt,
                        "drivers": list(task.drivers) if task.drivers else None,
                        "file": dest.relative_to(base).as_posix(), "bytes": size,
                        "seconds": round(time.monotonic() - t0, 2),
                    })
                    job.success += 1
                    job.files += 1
                    job.bytes_written += size
                    bump_metric(True)
                    if task.spec.has_highlights and task.slug not in highlights:
                        _collect_highlights(task, year, gp, session, highlights, job)
                except Exception as exc:  # noqa: BLE001 - one bad chart must not sink the pack
                    logger.exception("Social pack chart %s failed for %s %s %s", task.label, year, gp, session)
                    job.failed += 1
                    errors.append({"slug": task.slug, "format": task.fmt, "error": f"{type(exc).__name__}: {exc}",
                                   "seconds": round(time.monotonic() - t0, 2)})
                    writer.note_error(f"{task.label}: {exc}")
                    bump_metric(False)
                finally:
                    job.done += 1
                    writer.tick()

        if writer.cancelled():
            job.status = "cancelled"
        elif job.total and job.failed == job.total:
            job.status = "failed"
        else:
            job.status = "completed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Social pack job %s crashed", job_id)
        job.status = "failed"
        writer.note_error(str(exc))
    finally:
        job.current = None
        job.finished_at = now()
        if base is not None and base.is_dir():
            _finalize(job, base, {
                "schema": 1, "job_id": job_id, "year": year, "gp": gp, "session": session,
                "event_name": event_name, "formats": fmts,
                "pairs": [list(p) for p in final_pairs], "pairs_source": pairs_source,
                "include_season": include_season, "status": job.status,
                "started_at": job.started_at, "finished_at": job.finished_at,
                "timings": {"total_seconds": round(time.monotonic() - started, 2)},
                "items": items, "errors": errors, "warnings": list(job.warnings),
            }, highlights, writer)
        CANCELLED.discard(job_id)
        writer.tick(force=True)
    return job


def _collect_highlights(task: SocialTask, year: int, gp: Any, session: str,
                        highlights: Dict[str, Any], job: SocialJob) -> None:
    caller = DATA_CALLERS.get(task.chart)
    if caller is None:
        return
    try:
        payload = caller(year, gp, session, task)
    except Exception as exc:  # noqa: BLE001 - highlights are optional
        logger.exception("Highlights for %s failed", task.slug)
        job.warnings.append(f"{task.slug}: highlights unavailable ({exc})")
        return
    if isinstance(payload, dict) and payload.get("highlights"):
        highlights[task.slug] = payload["highlights"]


def _finalize(job: SocialJob, base: Path, manifest: Dict[str, Any], highlights: Dict[str, Any],
              writer: JobWriter) -> None:
    """Write highlights + manifest, then zip the folder. Failures here are recorded, not raised."""
    try:
        _atomic_write_json(base / HIGHLIGHTS_NAME, highlights)
        _atomic_write_json(base / MANIFEST_NAME, manifest)
        build_zip(base, top=(job.pack or base.name).replace("/", "_"))
        job.zip_ready = True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not finalize social pack in %s", base)
        job.zip_ready = False
        writer.note_error(f"finalize: {exc}")
        if job.status == "completed":
            job.status = "failed"


def start_social_job(
    *,
    year: int,
    gp: Union[int, str],
    session: str,
    formats: Optional[Sequence[str]] = None,
    pairs: Optional[Sequence[Sequence[str]]] = None,
    include_season: bool = False,
) -> SocialJob:
    """Validate, register and launch a social pack on a daemon thread.

    Raises ``RuntimeError`` when another social-pack job is running and
    ``ValueError`` for an unusable scope; routers map those to 409 / 400.
    """
    year = int(year)
    if not (settings.min_year <= year <= settings.max_year):
        raise ValueError(f"year must be between {settings.min_year} and {settings.max_year}")
    session = normalize_session(session)
    gp = normalize_gp(gp)
    fmts = normalize_formats(formats)
    explicit_pairs = normalize_pairs(pairs)

    with _START_LOCK:
        if running_jobs():
            raise RuntimeError("a social pack job is already running")
        job_id = uuid.uuid4().hex[:12]
        scope = {"year": year, "gp": gp, "session": session, "formats": fmts,
                 "pairs": [list(p) for p in explicit_pairs] if explicit_pairs is not None else None,
                 "include_season": bool(include_season)}
        job = SocialJob(job_id=job_id, scope=scope, started_at=now(), formats=fmts,
                        total=len(plan_social_pack(year, gp, session, fmts, explicit_pairs, include_season)))
        _register_job(job)
        admin_jobs.create_job(job_id, kind=JOB_KIND, scope=scope, selection={"formats": fmts}, total=job.total)

    threading.Thread(
        target=run_social_pack, args=(job_id, year, gp, session, fmts, explicit_pairs, include_season),
        kwargs={"job": job}, name=f"social-pack-{job_id}", daemon=True,
    ).start()
    return job
