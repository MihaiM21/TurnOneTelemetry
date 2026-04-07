from datetime import datetime, timezone
from src.data_loader.const_loader import get_season_events, get_season_drivers_and_teams


def get_latest_finished_session():
    """
    Iterates through all 2025/2026 data to find the session
    closest to 'now' that has already finished.
    """
    now = datetime.now(timezone.utc)
    latest_session = None

    # We group the data to allow us to calculate the specific Round Number per season
    datasets = [
        (2025, get_season_events(2025)), 
        (2026, get_season_events(2026)) 
    ]

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