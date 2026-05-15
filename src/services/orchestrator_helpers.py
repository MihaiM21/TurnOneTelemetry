from datetime import datetime, timezone
from src.ingestion.reference import get_season_events, get_season_drivers_and_teams


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
        # Enumerate gives us the index (0, 1, 2...), so we add 1 to get the Round Number
        for i, race in enumerate(race_list):
            round_number = i + 1

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