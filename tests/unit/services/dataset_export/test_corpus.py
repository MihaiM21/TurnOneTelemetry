import json
import threading

import pandas as pd
import pytest

from src.services.dataset_export import corpus as C
from src.services.dataset_export.schema import TABLES, coerce, empty_frame

TARGET = (2024, 5, "Miami Grand Prix", "R")
QUALI_TARGET = (2024, 5, "Miami Grand Prix", "Q")


def _rows(table_name, rows):
    spec = TABLES[table_name]
    full_rows = []
    for row in rows:
        full = {c: None for c in spec.column_names}
        full.update(
            {
                "year": 2024,
                "round": 5,
                "session": "R",
                "session_key": "2024_05_R",
            }
        )
        full.update(row)
        full_rows.append(full)
    return coerce(pd.DataFrame(full_rows, columns=spec.column_names), spec)


def _empty_tables():
    return {name: empty_frame(spec) for name, spec in TABLES.items()}


def race_tables():
    tables = {}

    tables["sessions"] = _rows(
        "sessions",
        [
            {
                "gp_name": "Miami Grand Prix",
                "official_name": "FORMULA 1 CRYPTO.COM MIAMI GRAND PRIX 2024",
                "circuit_short_name": "Miami International Autodrome",
                "country": "United States",
                "location": "Miami",
                "session_type": "Race",
                "start_utc": "2024-05-05T20:00:00Z",
                "total_laps": 57,
            }
        ],
    )

    tables["drivers"] = _rows(
        "drivers",
        [
            {"driver_number": "1", "driver_code": "VER", "full_name": "Max Verstappen", "team_name": "Red Bull"},
            {"driver_number": "16", "driver_code": "LEC", "full_name": "Charles Leclerc", "team_name": "Ferrari"},
            {"driver_number": "55", "driver_code": "SAI", "full_name": "Carlos Sainz", "team_name": "Ferrari"},
        ],
    )

    tables["results"] = _rows(
        "results",
        [
            {
                "driver_number": "1",
                "driver_code": "VER",
                "team_name": "Red Bull",
                "position": 1,
                "classified_status": "FINISHED",
                "laps_completed": 57,
                "gap_to_winner": "WINNER",
                "best_lap_time_s": 89.5,
            },
            {
                "driver_number": "16",
                "driver_code": "LEC",
                "team_name": "Ferrari",
                "position": 2,
                "classified_status": "FINISHED",
                "laps_completed": 57,
                "gap_to_winner": "+5.638",
                "best_lap_time_s": 89.9,
            },
            {
                "driver_number": "55",
                "driver_code": "SAI",
                "team_name": "Ferrari",
                "position": None,
                "classified_status": "DNF",
                "laps_completed": 40,
                "gap_to_winner": None,
                "best_lap_time_s": 90.5,
            },
        ],
    )

    tables["laps"] = _rows(
        "laps",
        [
            {"driver_number": "1", "driver_code": "VER", "lap": 30, "lap_time_s": 89.5, "is_overall_fastest": True},
            {"driver_number": "16", "driver_code": "LEC", "lap": 31, "lap_time_s": 89.9, "is_overall_fastest": False},
        ],
    )

    tables["stints"] = _rows(
        "stints",
        [
            {"driver_number": "1", "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 18,
             "lap_count": 18},
            {"driver_number": "1", "stint_number": 2, "compound": "HARD", "start_lap": 19, "end_lap": 57,
             "lap_count": 39},
            {"driver_number": "16", "stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 20,
             "lap_count": 20},
            {"driver_number": "16", "stint_number": 2, "compound": "HARD", "start_lap": 21, "end_lap": 57,
             "lap_count": 37},
            {"driver_number": "55", "stint_number": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 15,
             "lap_count": 15},
        ],
    )

    tables["pit_stops"] = _rows(
        "pit_stops",
        [
            {"driver_number": "1", "stop_n": 1, "lap": 18, "pit_lane_time_s": 2.4},
            {"driver_number": "16", "stop_n": 1, "lap": 20, "pit_lane_time_s": 2.6},
        ],
    )

    tables["track_status"] = _rows(
        "track_status",
        [
            {"status_label": "SC", "start_lap": 12, "end_lap": 15},
        ],
    )

    tables["weather"] = _rows(
        "weather",
        [
            {"air_temp": 28.0, "track_temp": 40.0, "humidity": 55.0, "rainfall": False},
            {"air_temp": 30.0, "track_temp": 45.0, "humidity": 60.0, "rainfall": False},
        ],
    )

    tables["race_control"] = _rows(
        "race_control",
        [
            {"lap": 12, "category": "SafetyCar", "message": "SAFETY CAR DEPLOYED"},
            {"lap": 9, "category": "Flag", "flag": "YELLOW", "message": "YELLOW FLAG SECTOR 3"},
            {"lap": 20, "category": None,
             "message": "CAR 55 (SAI) TIME 1:31.000 DELETED - TRACK LIMITS AT TURN 4 LAP 20"},
            {"lap": 25, "category": "Other", "message": "GREEN LIGHT - TRACK CLEAR"},
        ],
    )

    tables["car_telemetry"] = empty_frame(TABLES["car_telemetry"])
    tables["position_telemetry"] = empty_frame(TABLES["position_telemetry"])

    return tables


def quali_tables():
    tables = {name: empty_frame(spec) for name, spec in TABLES.items()}

    sessions = _rows(
        "sessions",
        [
            {
                "gp_name": "Miami Grand Prix",
                "circuit_short_name": "Miami International Autodrome",
                "country": "United States",
                "session_type": "Qualifying",
                "start_utc": "2024-05-04T21:00:00Z",
            }
        ],
    )
    sessions["session"] = "Q"
    sessions["session_key"] = "2024_05_Q"
    tables["sessions"] = sessions

    results = _rows(
        "results",
        [
            {"driver_number": "1", "driver_code": "VER", "team_name": "Red Bull", "position": 1,
             "best_lap_time_s": 87.241},
            {"driver_number": "16", "driver_code": "LEC", "team_name": "Ferrari", "position": 2,
             "best_lap_time_s": 87.5},
        ],
    )
    results["session"] = "Q"
    results["session_key"] = "2024_05_Q"
    tables["results"] = results

    drivers = _rows(
        "drivers",
        [
            {"driver_number": "1", "driver_code": "VER", "full_name": "Max Verstappen", "team_name": "Red Bull"},
            {"driver_number": "16", "driver_code": "LEC", "full_name": "Charles Leclerc", "team_name": "Ferrari"},
        ],
    )
    drivers["session"] = "Q"
    drivers["session_key"] = "2024_05_Q"
    tables["drivers"] = drivers

    return tables


# --------------------------------------------------------------------- format_lap_time


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (93.6, "1:33.600"),
        (60.0, "1:00.000"),
        (0, "0:00.000"),
        (None, "n/a"),
        (float("nan"), "n/a"),
        (-1.0, "n/a"),
        ("not a number", "n/a"),
    ],
)
def test_format_lap_time(seconds, expected):
    assert C.format_lap_time(seconds) == expected


# ------------------------------------------------------------------------ narrative


def test_session_narrative_race_contains_expected_facts():
    text = C.session_narrative(TARGET, race_tables())

    assert "# 2024 Miami Grand Prix — Race" in text
    assert "Max Verstappen (VER)" in text
    assert "HARD (19-57), 1 stop" in text
    assert "SC laps 12-15" in text
    assert "rain: no" in text
    assert "Did not finish:" in text
    assert "VER lap 18 (2.4 s)" in text


def test_session_narrative_deleted_lap_message_is_notable():
    text = C.session_narrative(TARGET, race_tables())
    assert "DELETED" in text
    assert "GREEN LIGHT" not in text  # not a notable category/keyword


def test_session_narrative_quali_uses_best_lap_time_not_gap():
    text = C.session_narrative(QUALI_TARGET, quali_tables())
    assert "1:27.241" in text
    assert "## Strategy" not in text


def test_session_narrative_empty_tables_has_only_header():
    text = C.session_narrative(TARGET, _empty_tables())
    assert text.strip() == "# 2024 Miami Grand Prix — Race"


def test_session_narrative_never_raises_on_empty_tables():
    for session in ("R", "S", "Q", "SQ", "FP1", "FP2", "FP3"):
        target = (2024, 1, "Test GP", session)
        C.session_narrative(target, _empty_tables())


def test_narrative_record_shape():
    record = C.narrative_record(TARGET, race_tables())
    assert record["id"] == "2024_05_R"
    assert record["session_key"] == "2024_05_R"
    assert record["year"] == 2024
    assert record["round"] == 5
    assert record["session"] == "R"
    assert record["gp_name"] == "Miami Grand Prix"
    assert record["source"] == "t1api-dataset-export"
    assert "Max Verstappen" in record["text"]


# -------------------------------------------------------------------------- qa_pairs


def test_qa_pairs_includes_expected_types_and_answers():
    pairs = C.qa_pairs(TARGET, race_tables())
    by_type = {}
    for rec in pairs:
        by_type.setdefault(rec["type"], []).append(rec)

    assert "winner" in by_type
    assert "Max Verstappen (VER) won the Miami Grand Prix Race" in by_type["winner"][0]["answer"]

    assert "podium" not in by_type  # only 2 finishers -> no full podium

    assert "pit_count" in by_type
    pit_answers = {r["answer"] for r in by_type["pit_count"]}
    assert any("made 1 pit stop" in a for a in pit_answers)

    assert "strategy" in by_type
    strategy_answers = " ".join(r["answer"] for r in by_type["strategy"])
    assert "HARD (19-57), 1 stop" in strategy_answers

    assert "dnf" in by_type
    assert "Carlos Sainz (SAI) did not finish" in by_type["dnf"][0]["answer"]

    for rec in pairs:
        assert rec["messages"] == [
            {"role": "user", "content": rec["question"]},
            {"role": "assistant", "content": rec["answer"]},
        ]
        assert rec["id"] == f"{rec['session_key']}:{rec['type']}:" + rec["id"].rsplit(":", 1)[1]


def test_qa_pairs_podium_present_with_three_finishers():
    tables = race_tables()
    results = tables["results"].copy()
    results.loc[results["driver_number"] == "55", "position"] = 3
    results.loc[results["driver_number"] == "55", "classified_status"] = "FINISHED"
    tables["results"] = results

    pairs = C.qa_pairs(TARGET, tables)
    podium = [r for r in pairs if r["type"] == "podium"]
    assert len(podium) == 1
    assert "1) Max Verstappen (VER)" in podium[0]["answer"]
    assert "3) Carlos Sainz (SAI)" in podium[0]["answer"]


def test_qa_pairs_pole_for_qualifying():
    pairs = C.qa_pairs(QUALI_TARGET, quali_tables())
    pole = [r for r in pairs if r["type"] == "pole"]
    assert len(pole) == 1
    assert "Max Verstappen (VER) took pole position" in pole[0]["answer"]
    winner = [r for r in pairs if r["type"] == "winner"]
    assert winner == []  # winner only applies to R/S


def test_qa_pairs_is_deterministic_across_runs():
    tables = race_tables()
    first = json.dumps(C.qa_pairs(TARGET, tables), sort_keys=True)
    second = json.dumps(C.qa_pairs(TARGET, tables), sort_keys=True)
    assert first == second


def test_qa_pairs_empty_tables_yields_no_pairs():
    assert C.qa_pairs(TARGET, _empty_tables()) == []


def test_qa_pairs_never_raises_on_empty_tables():
    for session in ("R", "S", "Q", "SQ", "FP1", "FP2", "FP3"):
        target = (2024, 1, "Test GP", session)
        assert C.qa_pairs(target, _empty_tables()) == []


# ------------------------------------------------------------------------ write_corpus


def test_write_corpus_writes_both_files(tmp_path):
    lock = threading.Lock()
    result = C.write_corpus(TARGET, race_tables(), tmp_path, lock)

    narratives_path = tmp_path / "corpus" / C.NARRATIVES_FILENAME
    qa_path = tmp_path / "corpus" / C.QA_FILENAME
    assert narratives_path.exists()
    assert qa_path.exists()
    assert result["narratives"] == 1
    assert result["qa"] > 0

    narrative_lines = narratives_path.read_text(encoding="utf-8").splitlines()
    assert len(narrative_lines) == 1
    qa_lines = qa_path.read_text(encoding="utf-8").splitlines()
    assert len(qa_lines) == result["qa"]


def test_write_corpus_resume_is_idempotent_and_keeps_other_sessions(tmp_path):
    lock = threading.Lock()
    other_target = (2024, 6, "Other GP", "R")

    C.write_corpus(other_target, race_tables(), tmp_path, lock)
    C.write_corpus(TARGET, race_tables(), tmp_path, lock)
    result = C.write_corpus(TARGET, race_tables(), tmp_path, lock)

    narratives_path = tmp_path / "corpus" / C.NARRATIVES_FILENAME
    qa_path = tmp_path / "corpus" / C.QA_FILENAME

    narrative_records = [json.loads(line) for line in narratives_path.read_text(encoding="utf-8").splitlines()]
    session_keys = [r["session_key"] for r in narrative_records]
    assert session_keys.count("2024_05_R") == 1
    assert session_keys.count("2024_06_R") == 1

    qa_records = [json.loads(line) for line in qa_path.read_text(encoding="utf-8").splitlines()]
    ids = [r["id"] for r in qa_records if r["session_key"] == "2024_05_R"]
    assert len(ids) == len(set(ids))
    assert len(ids) == result["qa"]
