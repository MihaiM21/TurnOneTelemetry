"""Per-lap derivation: sector attribution, pit flags, context join, deleted laps."""

from src.services.dataset_export import laps as L


def _e(ts, num, **line):
    return {"_timestamp": ts, "Lines": {num: line}}


def _sector(idx, value):
    return {"Sectors": {str(idx): {"Value": value}}}


def _stream_two_laps_inline_s3():
    """Sector 3 arrives in the same entry as LastLapTime (the common case)."""
    return [
        _e("00:10:00.000", "1", Position="3", GapToLeader="+2.500",
           IntervalToPositionAhead={"Value": "+0.800"}),
        _e("00:10:30.000", "1", **_sector(0, "28.100")),
        _e("00:10:31.000", "1", Speeds={"I1": {"Value": "301"}}),
        _e("00:11:00.000", "1", **_sector(1, "35.200")),
        _e("00:11:01.000", "1", Speeds={"I2": {"Value": "250"}, "ST": {"Value": "320"}}),
        _e("00:11:30.000", "1", Sectors={"2": {"Value": "30.300"}},
           Speeds={"FL": {"Value": "290"}},
           LastLapTime={"Value": "1:33.600", "PersonalFastest": True, "OverallFastest": False},
           NumberOfLaps=5),
        # feed resets sectors for the new lap: must be ignored
        _e("00:11:30.100", "1", Sectors={"0": {"Value": ""}, "1": {"Value": ""}, "2": {"Value": ""}}),
        _e("00:11:35.000", "1", InPit=True),
        _e("00:12:00.000", "1", **_sector(0, "28.900")),
        _e("00:12:30.000", "1", **_sector(1, "36.000")),
        _e("00:13:10.000", "1", Sectors={"2": {"Value": "45.000"}},
           LastLapTime={"Value": "1:49.900"}, NumberOfLaps=6),
    ]


def test_sectors_speeds_and_state_attached_to_closed_lap():
    recs = L.extract_lap_records(_stream_two_laps_inline_s3())["1"]
    assert [r["lap"] for r in recs] == [5, 6]
    lap5 = recs[0]
    assert lap5["lap_time_s"] == 93.6
    assert (lap5["sector1_s"], lap5["sector2_s"], lap5["sector3_s"]) == (28.1, 35.2, 30.3)
    assert (lap5["speed_i1"], lap5["speed_i2"], lap5["speed_fl"], lap5["speed_st"]) == (301, 250, 290, 320)
    assert lap5["position"] == 3
    assert lap5["gap_to_leader_s"] == 2.5
    assert lap5["interval_s"] == 0.8
    assert lap5["is_personal_best"] is True and lap5["is_overall_fastest"] is False
    assert lap5["timestamp_start_s"] is None
    assert lap5["timestamp_end_s"] == 11 * 60 + 30

    lap6 = recs[1]
    assert lap6["is_pit_in"] is True and lap6["is_pit_out"] is False
    assert (lap6["sector1_s"], lap6["sector2_s"], lap6["sector3_s"]) == (28.9, 36.0, 45.0)
    assert lap6["speed_i1"] is None  # speeds were reset with the lap
    assert lap6["timestamp_start_s"] == lap5["timestamp_end_s"]


def test_late_sector3_and_fl_attach_to_previous_lap():
    stream = [
        _e("00:10:30.000", "1", **_sector(0, "28.100")),
        _e("00:11:00.000", "1", **_sector(1, "35.200")),
        _e("00:11:30.000", "1", LastLapTime={"Value": "1:33.600"}, NumberOfLaps=5),
        # sector 3 + FL arrive one entry late, before the new lap's sector 1
        _e("00:11:30.200", "1", Sectors={"2": {"Value": "30.300"}}, Speeds={"FL": {"Value": "290"}}),
        _e("00:12:00.000", "1", **_sector(0, "28.900")),
        # a sector 3 after the new lap started belongs to the new lap
        _e("00:12:50.000", "1", Sectors={"2": {"Value": "31.000"}}),
        _e("00:12:51.000", "1", LastLapTime={"Value": "1:34.000"}, NumberOfLaps=6),
    ]
    recs = L.extract_lap_records(stream)["1"]
    assert recs[0]["sector3_s"] == 30.3 and recs[0]["speed_fl"] == 290
    assert recs[1]["sector3_s"] == 31.0 and recs[1]["sector1_s"] == 28.9


def test_gap_parsing_for_leader_and_lapped_cars():
    stream = [
        _e("00:10:00.000", "1", GapToLeader="LAP 12", IntervalToPositionAhead={"Value": "1L"}),
        _e("00:10:01.000", "1", LastLapTime={"Value": "1:33.600"}, NumberOfLaps=5),
    ]
    rec = L.extract_lap_records(stream)["1"][0]
    assert rec["gap_to_leader_s"] is None and rec["interval_s"] is None


def test_deleted_laps_regex():
    events = [
        {"message": "CAR 44 (HAM) TIME 1:23.456 DELETED - TRACK LIMITS AT TURN 4 LAP 12 15:04:33"},
        {"message": "CAR 1 (VER) TIME 1:20.000 DELETED - TRACK LIMITS AT TURN 9"},  # no lap: skipped
        {"message": "DRS ENABLED"},
        {"category": "Flag"},
    ]
    assert L.deleted_laps_from_rcm(events) == {("44", 12)}


def test_join_lap_context_tyres_track_status_and_flags():
    records = {"1": [
        {"lap": 1, "timestamp_start_s": None, "timestamp_end_s": 100.0, "is_pit_in": False, "is_pit_out": False},
        {"lap": 2, "timestamp_start_s": 100.0, "timestamp_end_s": 200.0, "is_pit_in": True, "is_pit_out": False},
        {"lap": 3, "timestamp_start_s": 200.0, "timestamp_end_s": 300.0, "is_pit_in": False, "is_pit_out": True},
    ]}
    stints = {"1": [
        {"stint_number": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 2, "lap_count": 2, "tyre_life_end": 5},
        {"stint_number": 2, "compound": "HARD", "start_lap": 3, "end_lap": 3, "lap_count": 1, "tyre_life_end": 1},
    ]}
    periods = [
        {"status": "GREEN", "start_time_s": 0.0, "end_time_s": 150.0},
        {"status": "SC", "start_time_s": 150.0, "end_time_s": None},
    ]
    out = L.join_lap_context(records, stints, periods, deleted={("1", 3)})["1"]
    assert [r["compound"] for r in out] == ["SOFT", "SOFT", "HARD"]
    assert [r["tyre_life"] for r in out] == [4, 5, 1]
    assert [r["stint_number"] for r in out] == [1, 1, 2]
    assert [r["track_status_label"] for r in out] == ["GREEN", "GREEN", "SC"]
    assert [r["track_status_code"] for r in out] == [1, 1, 4]
    assert [r["is_deleted"] for r in out] == [False, False, True]
    assert [r["is_pit_lap"] for r in out] == [False, True, True]


def test_join_without_stints_leaves_nulls():
    records = {"7": [{"lap": 1, "timestamp_start_s": None, "timestamp_end_s": 1.0}]}
    out = L.join_lap_context(records, {}, [])["7"][0]
    assert out["compound"] is None and out["tyre_life"] is None and out["track_status_code"] is None


def test_split_time_before_count_and_count_before_time():
    stream = [
        # lap 1: count only (out-lap) -> kept, untimed
        _e("00:10:00.000", "1", NumberOfLaps=1),
        # lap 2: time arrives 20 s before its count
        _e("00:11:30.000", "1", LastLapTime={"Value": "1:30.000", "PersonalFastest": True}),
        _e("00:11:50.000", "1", NumberOfLaps=2),
        # lap 3: count arrives first, time 2 s later -> backfilled
        _e("00:13:20.000", "1", NumberOfLaps=3),
        _e("00:13:22.000", "1", LastLapTime={"Value": "1:29.000"}),
        # lap 4: normal, both together
        _e("00:14:51.000", "1", LastLapTime={"Value": "1:29.500"}, NumberOfLaps=4),
        # trailing time with no count (session end) -> dropped
        _e("00:16:30.000", "1", LastLapTime={"Value": "1:31.000"}),
    ]
    recs = L.extract_lap_records(stream)["1"]
    assert [(r["lap"], r["lap_time_s"]) for r in recs] == [(1, None), (2, 90.0), (3, 89.0), (4, 89.5)]
    assert recs[1]["is_personal_best"] is True
    assert recs[2]["timestamp_end_s"] == 13 * 60 + 20  # closed on the count, not the late time


def test_stale_time_does_not_attach_to_a_much_later_untimed_lap():
    stream = [
        _e("00:10:00.000", "1", NumberOfLaps=5),
        _e("00:10:30.000", "1", LastLapTime={"Value": "1:30.000"}),  # > window after the count-only close
        _e("00:12:00.000", "1", NumberOfLaps=6),
    ]
    recs = L.extract_lap_records(stream)["1"]
    # the lone time is held and pairs with the next count (lap 6), never lap 5
    assert [(r["lap"], r["lap_time_s"]) for r in recs] == [(5, None), (6, 90.0)]
