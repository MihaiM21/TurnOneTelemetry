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
        # Prefer livetiming-derived round number when available (curated/livetiming
        # may drift if a race is cancelled or reordered). Falls back to index + 1.
        for i, race in enumerate(race_list):
            round_number = race.get("round") if isinstance(race.get("round"), int) else i + 1

            for session in race["sessions"]:
                # Check if session is finished
                if session["endTime"] < now:

                    # If this session ended *after* the currently stored one, it's the new latest
                    if latest_session is None or session["endTime"] > latest_session["endTime"]:
                        sessionType = simplify_session_name(session["name"])

                        latest_session = {
                            "year": year,
                            "round": round_number,
                            "grandPrix": race["grandPrix"],
                            "circuit": race["circuit"],
                            "country": race["country"],
                            "session_name": sessionType,
                            "startTime": session["startTime"],
                            "endTime": session["endTime"],
                            "is_sprint_weekend": race["hasSprint"]
                        }

    return latest_session


def simplify_session_name(session_name):
    """
    Simplifies session names to standard abbreviations.
    """
    mapping = {
        "Free Practice 1": "FP1",
        "Free Practice 2": "FP2",
        "Free Practice 3": "FP3",
        "Qualifying": "Q",
        "Sprint Qualifying": "SQ",
        "Sprint": "S",
        "Race": "R"
    }
    return mapping.get(session_name, session_name)


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
    V2 equivalent of :func:`get_latest_finished_session` that uses the
    livetiming static index (F1StaticClient) instead of curated reference
    data. Returns the same dict shape so :func:`latest_session_analised_v2`
    can consume it interchangeably.
    """
    # Imported lazily to avoid a circular import at module load time.
    from src.ingestion.static_client import F1StaticClient

    now = datetime.now(timezone.utc)
    current_year = now.year
    candidate_years = [current_year - 1, current_year]

    client = F1StaticClient()
    latest_session = None

    for year in candidate_years:
        try:
            index = client.fetch_season_index(year)
        except Exception as exc:
            logger.debug("V2 latest-session: skipping %s (%s)", year, exc)
            continue

        meetings = index.get('Meetings', []) or []
        for idx, meeting in enumerate(meetings, start=1):
            gmt_offset = _parse_gmt_offset(meeting.get('GmtOffset', ''))
            has_sprint = _meeting_has_sprint(meeting)

            for session in meeting.get('Sessions', []) or []:
                end_utc = _parse_livetiming_datetime(session.get('EndDate', ''), gmt_offset)
                if end_utc is None or end_utc >= now:
                    continue

                if latest_session is None or end_utc > latest_session['endTime']:
                    start_utc = _parse_livetiming_datetime(session.get('StartDate', ''), gmt_offset)
                    session_name = simplify_session_name(session.get('Name', ''))
                    circuit = meeting.get('Circuit') or {}
                    country = meeting.get('Country') or {}
                    latest_session = {
                        "year": year,
                        "round": idx,
                        "grandPrix": meeting.get('Name', ''),
                        "circuit": circuit.get('ShortName', '') if isinstance(circuit, dict) else '',
                        "country": country.get('Name', '') if isinstance(country, dict) else '',
                        "session_name": session_name,
                        "startTime": start_utc,
                        "endTime": end_utc,
                        "is_sprint_weekend": has_sprint,
                    }

    return latest_session