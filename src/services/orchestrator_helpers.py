from datetime import datetime, timedelta, timezone
from src.ingestion.reference import get_season_events, get_season_drivers_and_teams
from src.core.logging import get_logger

logger = get_logger(__name__)

_SPRINT_NAME_TOKENS = ("sprint",)


def get_latest_finished_session():
    """
    Iterates through all 2025/2026 data to find the session
    closest to 'now' that has already finished.
    """
    now = datetime.now(timezone.utc)
    latest_session = None

    # Look at the current year and the previous year so the dashboard keeps
    # working into a new season without a code change. Older years are
    # reachable through the V2 historical-season path but not relevant here.
    current_year = now.year
    candidate_years = [current_year - 1, current_year]
    datasets = []
    for y in candidate_years:
        try:
            datasets.append((y, get_season_events(y)))
        except Exception:
            # Season may not be published in our curated/livetiming data yet.
            continue

    for year, race_list in datasets:
        # ``round`` is the official championship round (== FastF1's RoundNumber), so it
        # addresses the same race in V1 and V2. Cancelled events and pre-season
        # testing are not in this list.
        for race in race_list:
            round_number = race.get("round")
            if not isinstance(round_number, int):
                continue

            for session in race.get("sessions") or []:
                end_time = session.get("endTime")
                if end_time is None:
                    continue
                # Check if session is finished
                if end_time < now:

                    # If this session ended *after* the currently stored one, it's the new latest
                    if latest_session is None or end_time > latest_session["endTime"]:
                        sessionType = simplify_session_name(session["name"])

                        latest_session = {
                            "year": year,
                            "round": round_number,
                            "grandPrix": race["grandPrix"],
                            "circuit": race["circuit"],
                            "country": race["country"],
                            "session_name": sessionType,
                            "startTime": session["startTime"],
                            "endTime": end_time,
                            "is_sprint_weekend": bool(race.get("hasSprint")),
                        }

    return latest_session


_SESSION_ABBREVIATIONS = {
    "free practice 1": "FP1",
    "practice 1": "FP1",
    "fp1": "FP1",
    "free practice 2": "FP2",
    "practice 2": "FP2",
    "fp2": "FP2",
    "free practice 3": "FP3",
    "practice 3": "FP3",
    "fp3": "FP3",
    "qualifying": "Q",
    "q": "Q",
    "sprint qualifying": "SQ",
    # 2023 called the sprint's qualifying session a Shootout.
    "sprint shootout": "SQ",
    "sq": "SQ",
    "sprint": "S",
    "s": "S",
    "race": "R",
    "r": "R",
}


def simplify_session_name(session_name):
    """Map any session spelling to its abbreviation (``FP1``, ``Q``, ``SQ``, ``S``, ``R``).

    The curated schedules say ``Free Practice 1`` while livetiming's Index.json
    says ``Practice 1`` (and ``Sprint Shootout`` in 2023); both must land on the
    same abbreviation because it keys caches, partitions and filters. Unknown
    names pass through unchanged.
    """
    if not isinstance(session_name, str):
        return session_name
    return _SESSION_ABBREVIATIONS.get(session_name.strip().lower(), session_name)


def _parse_gmt_offset(raw: str) -> timedelta:
    """Parse a livetiming GmtOffset like '11:00:00' or '-04:00:00' into a timedelta."""
    if not raw:
        return timedelta(0)
    sign = 1
    text = raw.strip()
    if text.startswith('+'):
        text = text[1:]
    elif text.startswith('-'):
        sign = -1
        text = text[1:]
    parts = text.split(':')
    try:
        hours = int(parts[0]) if len(parts) > 0 else 0
        minutes = int(parts[1]) if len(parts) > 1 else 0
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return timedelta(0)
    return sign * timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _parse_livetiming_datetime(raw: str, gmt_offset: timedelta) -> "datetime | None":
    """Parse a 'YYYY-MM-DDTHH:MM:SS' local timestamp and return it as UTC."""
    if not raw:
        return None
    try:
        local = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (local - gmt_offset).replace(tzinfo=timezone.utc)


def _meeting_has_sprint(meeting: dict) -> bool:
    for session in meeting.get('Sessions', []) or []:
        name = (session.get('Name') or '').lower()
        type_ = (session.get('Type') or '').lower()
        if any(token in name or token in type_ for token in _SPRINT_NAME_TOKENS):
            return True
    return False


def get_latest_finished_session_v2():
    """
    V2 equivalent of :func:`get_latest_finished_session`. Both read the season
    calendar (``reference.get_season_events``): official rounds, no pre-season
    testing, no cancelled events, and only events livetiming actually lists (an
    event without a meeting key has no V2 data to analyse). Returns the same dict
    shape so :func:`latest_session_analised_v2` can consume it interchangeably.
    """
    now = datetime.now(timezone.utc)
    current_year = now.year
    candidate_years = [current_year - 1, current_year]

    latest_session = None

    for year in candidate_years:
        try:
            events = get_season_events(year)
        except Exception as exc:
            logger.debug("V2 latest-session: skipping %s (%s)", year, exc)
            continue

        for event in events:
            if event.get("key") is None or not isinstance(event.get("round"), int):
                continue
            for session in event.get("sessions") or []:
                end_utc = session.get("endTime")
                if end_utc is None or end_utc >= now:
                    continue
                if latest_session is None or end_utc > latest_session["endTime"]:
                    latest_session = {
                        "year": year,
                        "round": event["round"],
                        "grandPrix": event.get("grandPrix", ""),
                        "circuit": event.get("circuit", ""),
                        "country": event.get("country", ""),
                        "session_name": simplify_session_name(session.get("name", "")),
                        "startTime": session.get("startTime"),
                        "endTime": end_utc,
                        "is_sprint_weekend": bool(event.get("hasSprint")),
                    }

    return latest_session
