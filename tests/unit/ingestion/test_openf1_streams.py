"""Offline tests for src/ingestion/openf1_streams.py.

A small synthetic Las Vegas weekend (qualifying + race, two drivers, three laps
each) is served through mocked ``api.openf1.org`` endpoints; the rebuilt
livetiming streams are then fed into the REAL V2 consumers through a duck-typed
store, so the tests pin what the analysis code actually sees.
"""
import json
from datetime import timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from src.core.exceptions import DataNotAvailableError
from src.ingestion import openf1_client, openf1_streams, static_client
from src.ingestion.openf1_streams import UnsupportedStream, fmt_lap_time, fmt_session_time
from src.services.analysis.v2 import _helpers, _race_helpers
from src.services.analysis.v2._lap_duel_core import qualifying_part_bounds

BASE = "https://api.openf1.org/v1"
LIVETIMING = "https://livetiming.formula1.com/static/"
QUALI_KEY = 11416
RACE_KEY = 11420
QUALI_PATH = "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-20_Qualifying/"
RACE_PATH = "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-21_Race/"
QUALI_START = "2026-11-21T04:00:00+00:00"
RACE_START = "2026-11-22T04:00:00+00:00"


def utc(text, plus=0.0):
    # openf1_client.parse_utc, not fromisoformat: Python 3.10 rejects "04:13:15.5".
    return openf1_client.parse_utc(text).replace(tzinfo=timezone.utc) + timedelta(seconds=plus)


def iso(moment):
    return moment.isoformat()


# --------------------------------------------------------------------------- synthetic world
def _laps(num, start, durations, sectors, pit_out_laps=()):
    rows, t = [], utc(start)
    for i, (dur, sec) in enumerate(zip(durations, sectors), start=1):
        rows.append({
            "driver_number": num, "lap_number": i, "date_start": iso(t),
            "duration_sector_1": sec[0], "duration_sector_2": sec[1], "duration_sector_3": sec[2],
            "i1_speed": 300 + i, "i2_speed": 250 + i, "is_pit_out_lap": i in pit_out_laps,
            "lap_duration": dur, "st_speed": 320 + 5 * i,
        })
        t += timedelta(seconds=dur)
    return rows


def _rc(date, category, message, flag=None, scope=None, sector=None, phase=None, lap=1):
    return {"date": date, "driver_number": None, "lap_number": lap, "category": category, "flag": flag,
            "scope": scope, "sector": sector, "qualifying_phase": phase, "message": message}


def _session_row(key, name, stype, start, end):
    return {"session_key": key, "session_type": stype, "session_name": name, "date_start": start,
            "date_end": end, "meeting_key": 1300, "circuit_key": 152, "circuit_short_name": "Las Vegas",
            "country_code": "USA", "country_name": "United States", "location": "Las Vegas",
            "gmt_offset": "-08:00:00", "year": 2026, "is_cancelled": False}


MEETING = {"meeting_key": 1300, "meeting_name": "Las Vegas Grand Prix",
           "meeting_official_name": "FORMULA 1 HEINEKEN LAS VEGAS GRAND PRIX 2026", "location": "Las Vegas",
           "country_key": 19, "country_code": "USA", "country_name": "United States", "circuit_key": 152,
           "circuit_short_name": "Las Vegas", "gmt_offset": "-08:00:00",
           "date_start": "2026-11-19T21:30:00+00:00", "year": 2026}

DRIVERS = [
    {"driver_number": 63, "broadcast_name": "G RUSSELL", "full_name": "George RUSSELL", "name_acronym": "RUS",
     "team_name": "Mercedes", "team_colour": "27F4D2", "first_name": "George", "last_name": "Russell",
     "headshot_url": "https://example.test/rus.png", "country_code": None},
    {"driver_number": 12, "broadcast_name": "K ANTONELLI", "full_name": "Kimi ANTONELLI", "name_acronym": "ANT",
     "team_name": "Mercedes", "team_colour": "#FF8000", "first_name": "Kimi", "last_name": "Antonelli",
     "headshot_url": None, "country_code": "ITA"},
]


class FakeOpenF1:
    """Registers ``api.openf1.org`` callbacks serving one synthetic weekend."""

    PER_SESSION = ("laps", "position", "pit", "stints", "race_control", "weather", "session_result",
                   "intervals", "drivers")
    PER_DRIVER = ("car_data", "location")

    def __init__(self, quali_start=QUALI_START):
        quali_end = iso(utc(quali_start, 3600))
        self.meetings = [MEETING]
        self.sessions = [
            _session_row(QUALI_KEY, "Qualifying", "Qualifying", quali_start, quali_end),
            _session_row(RACE_KEY, "Race", "Race", RACE_START, "2026-11-22T06:00:00+00:00"),
        ]
        self.rows = {ep: {QUALI_KEY: [], RACE_KEY: []} for ep in self.PER_SESSION}
        self.driver_rows = {ep: {} for ep in self.PER_DRIVER}
        self._build_quali()
        self._build_race()

    # -- data ---------------------------------------------------------------------------
    def _build_quali(self):
        r = self.rows
        r["drivers"][QUALI_KEY] = [dict(d, session_key=QUALI_KEY) for d in DRIVERS]
        r["laps"][QUALI_KEY] = (
            _laps(63, "2026-11-21T04:10:00+00:00", [110.0, 85.5, 84.25],
                  [(40.0, 40.0, 30.0), (28.0, 30.5, 27.0), (27.75, 30.0, 26.5)], pit_out_laps=(1,))
            + _laps(12, "2026-11-21T04:10:05+00:00", [111.0, 86.0, 84.9],
                    [(41.0, 40.0, 30.0), (28.5, 30.5, 27.0), (27.9, 30.2, 26.8)], pit_out_laps=(1,)))
        r["race_control"][QUALI_KEY] = [
            _rc("2026-11-21T04:00:00+00:00", "SessionStatus", "SESSION STARTED", phase=1),
            _rc("2026-11-21T04:20:00+00:00", "SessionStatus", "SESSION STARTED", phase=2),
            _rc("2026-11-21T04:40:00+00:00", "SessionStatus", "SESSION STARTED", phase=3),
            _rc("2026-11-21T04:59:00+00:00", "Flag", "CHEQUERED FLAG", flag="CHEQUERED"),
        ]
        r["weather"][QUALI_KEY] = [
            {"date": "2026-11-21T04:00:30+00:00", "humidity": 31.4, "track_temperature": 14.0,
             "air_temperature": 11.9, "wind_direction": 272, "pressure": 944.7, "wind_speed": 0.5, "rainfall": 0},
            {"date": "2026-11-21T04:30:30+00:00", "humidity": 33.0, "track_temperature": 13.5,
             "air_temperature": 11.5, "wind_direction": 270, "pressure": 944.9, "wind_speed": 0.7, "rainfall": 1},
        ]
        r["session_result"][QUALI_KEY] = [
            {"position": 1, "driver_number": 63, "number_of_laps": 3, "dnf": False, "dns": False, "dsq": False},
            {"position": 2, "driver_number": 12, "number_of_laps": 3, "dnf": False, "dns": False, "dsq": False},
        ]
        base = utc("2026-11-21T04:13:00")
        for num, offset in ((63, 0), (12, 0)):
            car = [{"date": iso(base + timedelta(seconds=5 * k)), "brake": 0, "drs": None, "throttle": 100,
                    "rpm": 11000 + k, "driver_number": num, "n_gear": 8, "speed": 200 + k} for k in range(25)]
            loc = [{"date": iso(base + timedelta(seconds=5 * k)), "z": 6053, "x": -4351 + 10 * k,
                    "y": 17754 + offset, "driver_number": num} for k in range(25)]
            if num == 63:  # a frame only this car is in, and a row from before the session origin
                car.append({"date": iso(base + timedelta(seconds=2.5)), "brake": 1, "drs": 12, "throttle": 0,
                            "rpm": 9000, "driver_number": 63, "n_gear": 4, "speed": 150})
                car.append({"date": "2026-11-21T02:00:00+00:00", "brake": 0, "drs": 0, "throttle": 0,
                            "rpm": 0, "driver_number": 63, "n_gear": 0, "speed": 0})
            self.driver_rows["car_data"][(QUALI_KEY, num)] = car
            self.driver_rows["location"][(QUALI_KEY, num)] = loc

    def _build_race(self):
        r = self.rows
        r["drivers"][RACE_KEY] = [dict(d, session_key=RACE_KEY) for d in DRIVERS]
        r["laps"][RACE_KEY] = (
            _laps(63, "2026-11-22T04:03:46.118+00:00", [100.0, 90.0, 95.0],
                  [(33.0, 34.0, 33.0), (30.0, 30.0, 30.0), (31.0, 32.0, 32.0)], pit_out_laps=(3,))
            + _laps(12, "2026-11-22T04:03:47+00:00", [101.0, 91.0, 96.0],
                    [(33.5, 34.0, 33.5), (30.5, 30.0, 30.5), (32.0, 32.0, 32.0)]))
        # Lap 2 starts at t0+100 for car 63; it enters the lane 70 s later and leaves 22 s after that,
        # i.e. after the lap-2 line crossing (t0+190), on lap 3.
        r["pit"][RACE_KEY] = [{"date": "2026-11-22T04:06:58.118000+00:00", "lane_duration": 22.0,
                               "stop_duration": 3.0, "lap_number": 2, "driver_number": 63, "pit_duration": 22.0}]
        r["stints"][RACE_KEY] = [
            {"stint_number": 1, "driver_number": 63, "lap_start": 1, "lap_end": 2, "compound": "SOFT",
             "tyre_age_at_start": 2},
            {"stint_number": 2, "driver_number": 63, "lap_start": 3, "lap_end": 3, "compound": "HARD",
             "tyre_age_at_start": 0},
            {"stint_number": 1, "driver_number": 12, "lap_start": 1, "lap_end": 3, "compound": "MEDIUM",
             "tyre_age_at_start": 0},
        ]
        r["position"][RACE_KEY] = [
            {"date": "2026-11-22T04:03:00+00:00", "driver_number": 63, "position": 1},
            {"date": "2026-11-22T04:03:00+00:00", "driver_number": 12, "position": 2},
            {"date": "2026-11-22T04:06:00+00:00", "driver_number": 63, "position": 2},
            {"date": "2026-11-22T04:06:00+00:00", "driver_number": 12, "position": 1},
        ]
        r["intervals"][RACE_KEY] = [{"date": "2026-11-22T04:05:30+00:00", "driver_number": 12, "interval": 1.2,
                                     "gap_to_leader": 1.2}]
        r["session_result"][RACE_KEY] = [
            {"position": 1, "driver_number": 63, "number_of_laps": 3, "dnf": False, "dns": False, "dsq": False,
             "gap_to_leader": 0, "duration": 285.0},
            {"position": 2, "driver_number": 12, "number_of_laps": 3, "dnf": False, "dns": False, "dsq": False,
             "gap_to_leader": 4.0, "duration": 289.0},
        ]
        r["weather"][RACE_KEY] = [{"date": "2026-11-22T04:00:30+00:00", "humidity": 30.0, "track_temperature": 12.0,
                                   "air_temperature": 10.0, "wind_direction": 100, "pressure": 940.0,
                                   "wind_speed": 1.0, "rainfall": 0}]
        r["race_control"][RACE_KEY] = [
            _rc("2026-11-22T04:01:00+00:00", "Flag", "GREEN LIGHT - PIT EXIT OPEN", flag="GREEN", scope="Track"),
            _rc("2026-11-22T04:04:00+00:00", "SafetyCar", "SAFETY CAR DEPLOYED"),
            _rc("2026-11-22T04:05:00+00:00", "SafetyCar", "SAFETY CAR IN THIS LAP"),
            _rc("2026-11-22T04:06:30+00:00", "Flag", "RED FLAG", flag="RED", scope="Track"),
            _rc("2026-11-22T04:07:00+00:00", "Flag", "GREEN LIGHT", flag="GREEN", scope="Track"),
            _rc("2026-11-22T04:07:30+00:00", "Flag", "YELLOW IN TRACK SECTOR 13", flag="YELLOW", scope="Sector",
                sector=13),
            _rc("2026-11-22T04:08:00+00:00", "Flag", "CLEAR IN TRACK SECTOR 13", flag="CLEAR", scope="Sector",
                sector=13),
        ]

    # -- HTTP ---------------------------------------------------------------------------
    def install(self, rsps):
        rsps.add_callback(responses.GET, f"{BASE}/meetings", callback=self._season("meetings"))
        rsps.add_callback(responses.GET, f"{BASE}/sessions", callback=self._season("sessions"))
        for endpoint in self.PER_SESSION:
            rsps.add_callback(responses.GET, f"{BASE}/{endpoint}", callback=self._per_session(endpoint))
        for endpoint in self.PER_DRIVER:
            rsps.add_callback(responses.GET, f"{BASE}/{endpoint}", callback=self._per_driver(endpoint))

    @staticmethod
    def _reply(rows):
        if not rows:
            return 404, {}, json.dumps({"detail": "No results found."})
        return 200, {}, json.dumps(rows)

    @staticmethod
    def _query(request):
        return {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}

    def _season(self, name):
        def callback(request):
            q = self._query(request)
            rows = getattr(self, name)
            return self._reply([x for x in rows if str(x.get("year")) == q.get("year", str(x.get("year")))])
        return callback

    def _per_session(self, endpoint):
        def callback(request):
            return self._reply(self.rows[endpoint].get(int(self._query(request)["session_key"]), []))
        return callback

    def _per_driver(self, endpoint):
        def callback(request):
            q = self._query(request)
            return self._reply(self.driver_rows[endpoint].get((int(q["session_key"]), int(q["driver_number"])), []))
        return callback


def calls_to(rsps, endpoint):
    return [c for c in rsps.calls if urlparse(c.request.url).path.endswith(f"/{endpoint}")]


def session_path(key, year=2026):
    for meeting in openf1_client.build_index(year)["Meetings"]:
        for s in meeting["Sessions"]:
            if s["Key"] == key:
                return s["Path"]
    raise AssertionError(key)


class FakeStore:
    """Just the stream accessors the V2 helpers use, backed by ``openf1_streams.load``."""

    def __init__(self, path):
        self.base_url = LIVETIMING + path
        self._cache = {}

    def _get(self, filename):
        if filename not in self._cache:
            self._cache[filename] = openf1_streams.load(self.base_url + filename)
        return self._cache[filename]

    def timing_data(self):
        return self._get("TimingData.jsonStream")

    def timing_app_data(self):
        return self._get("TimingAppData.jsonStream")

    def track_status(self):
        return self._get("TrackStatus.jsonStream")

    def car_data(self):
        return self._get("CarData.z.jsonStream")

    def position_data(self):
        return self._get("Position.z.jsonStream")

    def driver_list(self):
        return self._get("DriverList.json")

    def race_control(self):
        return self._get("RaceControlMessages.json")

    def session_info(self):
        return self._get("SessionInfo.json")

    def session_data(self):
        return self._get("SessionData.jsonStream")

    def weather(self):
        return self._get("WeatherData.jsonStream")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _fresh_caches():
    openf1_client.clear_cache()
    openf1_streams.clear_cache()
    static_client.clear_index_cache()
    yield
    openf1_client.clear_cache()
    openf1_streams.clear_cache()
    static_client.clear_index_cache()


@pytest.fixture
def world():
    fake = FakeOpenF1()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        fake.install(rsps)
        fake.rsps = rsps
        yield fake


@pytest.fixture
def quali(world):
    return FakeStore(session_path(QUALI_KEY))


@pytest.fixture
def race(world):
    return FakeStore(session_path(RACE_KEY))


def approx(value, tol=2e-3):
    return pytest.approx(value, abs=tol)


def rel(text):
    """Session time (s) of an ISO instant for the quali/race session origin (scheduled start - 1 h)."""
    return (utc(text).timestamp() - (utc(text[:10] + "T04:00:00").timestamp() - 3600))


# --------------------------------------------------------------------------- formatting
def test_fmt_session_time_and_clamp():
    assert fmt_session_time(3926.118) == "01:05:26.118"
    assert fmt_session_time(0) == "00:00:00.000"
    assert fmt_session_time(-5) == "00:00:00.000"


def test_fmt_lap_time_spelling():
    assert fmt_lap_time(84.25) == "1:24.250"
    assert fmt_lap_time(59.5) == "59.500"
    assert fmt_lap_time(103.497) == "1:43.497"


# --------------------------------------------------------------------------- lap times / pits
def test_lap_times_numbers_and_durations(race):
    laps = _race_helpers._compute_lap_times(race)
    # livetiming publishes no LastLapTime for a race's opening lap, so there is no lap-1 record
    assert [r["lap"] for r in laps["63"]] == [2, 3]
    assert [r["time_s"] for r in laps["63"]] == [approx(90.0), approx(95.0)]
    assert [r["lap"] for r in laps["12"]] == [2, 3]
    assert [r["time_s"] for r in laps["12"]] == [approx(91.0), approx(96.0)]
    # lap 2 of car 63 ends 190 s after the car's first start, on the shared session clock
    assert laps["63"][0]["timestamp_s"] == approx(rel("2026-11-22T04:03:46.118") + 190.0)


def test_race_lap_one_has_number_of_laps_but_no_time_while_quali_keeps_it(race, quali):
    lap_one = [line for e in race.timing_data() for n, line in e["Lines"].items()
               if n == "63" and line.get("NumberOfLaps") == 1]
    assert len(lap_one) == 1 and "LastLapTime" not in lap_one[0]
    quali_laps = _race_helpers._compute_lap_times(quali)
    # Outside races livetiming's NumberOfLaps is OpenF1's lap_number + 1 (measured on
    # 2026 FP1/FP2/SQ/Q); lap 1 keeps its time.
    assert [r["lap"] for r in quali_laps["63"]] == [2, 3, 4]


def test_pit_in_and_out_flags_land_on_in_lap_and_out_lap(race):
    laps = _race_helpers._compute_lap_times(race)["63"]
    assert [(r["pit_in"], r["pit_out"]) for r in laps] == [(True, False), (False, True)]  # laps 2 and 3
    assert not any(r["pit_in"] or r["pit_out"] for r in _race_helpers._compute_lap_times(race)["12"])


def test_pit_stop_lap_and_lane_time(race):
    stops = _race_helpers._compute_pit_stops(race)
    assert list(stops) == ["63"]
    stop = stops["63"][0]
    assert stop["stop_n"] == 1
    assert stop["pit_lane_time_s"] == approx(22.0)
    # livetiming's NumberOfLaps counts completed laps at pit entry: lap 1 done, lap 2 in progress.
    assert stop["lap"] == 1


# --------------------------------------------------------------------------- fastest lap + telemetry clock
def test_fastest_lap_window_matches_lap_time(quali):
    df = _helpers.get_fastest_lap_windows("", None, store=quali)
    rows = {r.DriverNum: r for r in df.itertuples()}
    assert set(rows) == {"63", "12"}
    for row in rows.values():
        assert row.EndTime - row.StartTime == approx(row.LapTime, 1e-6)
    assert rows["63"].LapTime == approx(84.25)
    assert rows["12"].LapTime == approx(84.9)
    assert rows["63"].StartTime == approx(rel("2026-11-21T04:13:15.5"))
    assert rows["63"].EndTime == approx(rel("2026-11-21T04:13:15.5") + 84.25)


def test_car_data_samples_share_the_lap_clock(quali):
    window = _helpers.get_fastest_lap_windows("", None, "63", store=quali).iloc[0]
    car_data = quali.car_data()
    utcs = [e["Utc"] for packet in car_data for e in packet["Entries"]]
    samples = list(_helpers._iter_car_data_samples(car_data))
    assert len(samples) == len(utcs) == 26  # 25 shared grid frames + one frame only car 63 is in

    lap_start = utc("2026-11-21T04:13:15.5")
    lap_end = lap_start + timedelta(seconds=84.25)
    inside = 0
    for (sample_time, cars), stamp in zip(samples, utcs):
        # the sample lands on the same axis as the lap's TimingData timestamps ...
        assert sample_time == approx(utc(stamp).timestamp() - (utc(QUALI_START).timestamp() - 3600))
        # ... so a sample is inside the lap window exactly when its real UTC time is inside the lap
        in_window = window.StartTime <= sample_time <= window.EndTime
        assert in_window == (lap_start <= utc(stamp) <= lap_end), stamp
        inside += in_window
        assert "63" in cars
    assert 0 < inside < len(samples)
    assert inside == 16  # grid samples 4..19 (4400 s..4475 s session time); the 2.5 s frame is outside


def test_car_data_channels_and_pre_session_rows_dropped(quali):
    packets = quali.car_data()
    first = packets[0]["Entries"][0]
    assert first["Utc"] == iso(utc("2026-11-21T04:13:00"))
    assert first["Cars"]["63"]["Channels"] == {"0": 11000, "2": 200, "3": 8, "4": 100, "5": 0, "45": 0}
    all_utcs = [e["Utc"] for p in packets for e in p["Entries"]]
    assert "2026-11-21T02:00:00+00:00" not in all_utcs  # before the origin
    assert all(len(p["Entries"]) <= 8 for p in packets)


def test_position_frames_merge_drivers_sharing_a_timestamp(quali):
    frames = [f for packet in quali.position_data() for f in packet["Position"]]
    assert len(frames) == 25  # one frame per timestamp, not one per (car, timestamp)
    assert set(frames[0]["Entries"]) == {"63", "12"}
    assert frames[0]["Entries"]["63"] == {"Status": "OnTrack", "X": -4351, "Y": 17754, "Z": 6053}
    assert frames[1]["Entries"]["12"]["X"] == -4341


# --------------------------------------------------------------------------- sectors / speeds / results
def test_best_sectors_and_speed_trap(quali):
    best = _race_helpers.best_sectors_from_entries(quali.timing_data())
    assert best["63"] == {"s1": approx(27.75), "s2": approx(30.0), "s3": approx(26.5)}
    assert best["12"] == {"s1": approx(27.9), "s2": approx(30.2), "s3": approx(26.8)}

    speeds = [(num, line["Speeds"]) for entry in quali.timing_data()
              for num, line in entry["Lines"].items() if "Speeds" in line]
    assert ("63", {"ST": {"Value": "335"}}) in speeds
    assert ("63", {"I1": {"Value": "303"}}) in speeds
    assert ("12", {"I2": {"Value": "253"}}) in speeds


def test_fastest_lap_flags(quali):
    flagged = {}
    for entry in quali.timing_data():
        for num, line in entry["Lines"].items():
            last = line.get("LastLapTime")
            if last:
                flagged[(num, last["Value"])] = last
    assert flagged[("63", "1:24.250")].get("OverallFastest") is True
    assert flagged[("63", "1:24.250")].get("PersonalFastest") is True
    assert "OverallFastest" not in flagged[("12", "1:24.900")]
    assert flagged[("12", "1:24.900")].get("PersonalFastest") is True
    assert all("_s" not in v for v in flagged.values())


def test_finishing_order_uses_session_result_not_live_positions(race):
    # live position rows end with car 12 leading; the classification says car 63 won
    assert _helpers.get_finishing_order("", None, store=race) == {"63": 1, "12": 2}


def test_race_gap_fields_only_in_races(race, quali, world):
    gaps = [line for e in race.timing_data() for line in e["Lines"].values() if "GapToLeader" in line]
    assert gaps == [{"GapToLeader": "+1.200", "IntervalToPositionAhead": {"Value": "+1.200"}}]
    quali.timing_data()
    assert calls_to(world.rsps, "intervals") and all(
        "session_key=%d" % RACE_KEY in c.request.url for c in calls_to(world.rsps, "intervals"))


# --------------------------------------------------------------------------- stints
def test_stints_round_trip_through_extract_stints(race):
    data = race.timing_app_data()
    first = data[0]["Lines"]["63"]["Stints"]["0"]
    assert first == {"Compound": "SOFT", "New": "false", "TyresNotChanged": "0", "StartLaps": 2, "TotalLaps": 4}

    stints = _helpers.extract_stints_from_data(data, "63")
    assert [(s["compound"], s["start_lap"], s["end_lap"], s["lap_count"], s["tyre_life_end"]) for s in stints] == [
        ("SOFT", 1, 2, 2, 4), ("HARD", 3, 3, 1, 1)]
    single = _helpers.extract_stints_from_data(data, "12")
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in single] == [("MEDIUM", 1, 3)]


def test_timing_app_data_without_stints_is_not_available(quali):
    with pytest.raises(DataNotAvailableError):
        quali.timing_app_data()


# --------------------------------------------------------------------------- track status
def test_track_status_stream_codes(race):
    statuses = [e["Status"] for e in race.track_status()]
    assert statuses == ["1", "4", "1", "5", "1", "2", "1"]
    sc_end = race.track_status()[2]
    assert sc_end["_timestamp"] == fmt_session_time(rel("2026-11-22T04:03:46.118") + 100.0)


def test_track_status_periods(race):
    periods = _race_helpers._compute_track_status_periods(race)
    assert [p["status"] for p in periods] == ["GREEN", "SC", "GREEN", "RED", "GREEN", "YELLOW", "GREEN"]
    sc = periods[1]
    assert sc["start_time_s"] == approx(rel("2026-11-22T04:04:00"))
    # "SAFETY CAR IN THIS LAP" at 04:05:00 -> the SC ends when the first car completes a lap afterwards
    assert sc["end_time_s"] == approx(rel("2026-11-22T04:03:46.118") + 100.0)
    assert sc["start_lap"] == 1
    red, yellow = periods[3], periods[5]
    assert red["start_time_s"] == approx(rel("2026-11-22T04:06:30"))
    assert red["end_time_s"] == approx(rel("2026-11-22T04:07:00"))
    assert yellow["start_time_s"] == approx(rel("2026-11-22T04:07:30"))
    assert yellow["end_time_s"] == approx(rel("2026-11-22T04:08:00"))
    assert periods[-1]["end_time_s"] is None


def test_virtual_safety_car_codes(world):
    world.rows["race_control"][RACE_KEY] = [
        _rc("2026-11-22T04:04:00+00:00", "SafetyCar", "VIRTUAL SAFETY CAR DEPLOYED"),
        _rc("2026-11-22T04:05:00+00:00", "SafetyCar", "VIRTUAL SAFETY CAR ENDING"),
        _rc("2026-11-22T04:05:10+00:00", "Flag", "GREEN LIGHT", flag="GREEN", scope="Track"),
    ]
    store = FakeStore(session_path(RACE_KEY))
    assert [e["Status"] for e in store.track_status()] == ["6", "7", "1"]
    assert [p["status"] for p in _race_helpers._compute_track_status_periods(store)] == ["VSC", "GREEN"]


def test_short_vsc_spellings_and_result_status_strings(world):
    world.rows["race_control"][RACE_KEY] = [
        _rc("2026-11-22T04:04:00+00:00", "SafetyCar", "VSC DEPLOYED"),
        _rc("2026-11-22T04:05:00+00:00", "SafetyCar", "VSC ENDING"),
    ]
    world.rows["session_result"][RACE_KEY][1]["position"] = "RT"  # a status string, not a place
    world.rows["session_result"][RACE_KEY][1]["dnf"] = False
    store = FakeStore(session_path(RACE_KEY))
    assert [e["Status"] for e in store.track_status()] == ["6", "7"]
    assert _helpers.get_finishing_order("", None, store=store) == {"63": 1, "12": 1}


def test_stint_boundaries_repaired_from_pit_laps(world):
    # OpenF1 says "SOFT 1-1, MEDIUM 2-3" but the only stop was on lap 2 -> SOFT 1-2, MEDIUM 3-3.
    world.rows["stints"][RACE_KEY] = [
        {"stint_number": 1, "driver_number": 63, "lap_start": 1, "lap_end": 1, "compound": "SOFT",
         "tyre_age_at_start": 0},
        {"stint_number": 2, "driver_number": 63, "lap_start": 2, "lap_end": None, "compound": "MEDIUM",
         "tyre_age_at_start": 0},
    ]
    store = FakeStore(session_path(RACE_KEY))
    stints = _helpers.extract_stints_from_data(store.timing_app_data(), "63")
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints] == [("SOFT", 1, 2), ("MEDIUM", 3, 3)]


def test_track_status_without_race_control_is_all_clear(world):
    world.rows["race_control"][RACE_KEY] = []
    store = FakeStore(session_path(RACE_KEY))
    status = store.track_status()
    assert [e["Status"] for e in status] == ["1"]


# --------------------------------------------------------------------------- qualifying parts
def test_session_data_gives_qualifying_part_bounds(quali):
    bounds = qualifying_part_bounds(quali.session_data())
    assert sorted(bounds) == [1, 2, 3]
    assert bounds[1][0] == approx(3600.0) and bounds[1][1] == approx(4800.0)
    assert bounds[2][0] == approx(4800.0) and bounds[2][1] == approx(6000.0)
    assert bounds[3][0] == approx(6000.0) and bounds[3][1] == float("inf")


def test_session_data_status_series(quali):
    started = [e["StatusSeries"][0]["SessionStatus"] for e in quali.session_data() if "StatusSeries" in e]
    assert started == ["Started", "Started", "Started"]


# --------------------------------------------------------------------------- small files
def test_driver_list_shape(race):
    drivers = race.driver_list()
    assert list(drivers) == ["63", "12"]  # ordered by classification -> Line 1, 2
    rus, ant = drivers["63"], drivers["12"]
    assert (rus["Tla"], rus["TeamName"], rus["Line"], rus["RacingNumber"]) == ("RUS", "Mercedes", 1, "63")
    assert rus["TeamColour"] == "27F4D2"
    assert ant["TeamColour"] == "FF8000"  # no leading '#'
    assert ant["Line"] == 2 and ant["CountryCode"] == "ITA"
    assert rus["HeadshotUrl"] == "https://example.test/rus.png" and ant["HeadshotUrl"] == ""


def test_session_info_and_race_control_shape(quali):
    info = quali.session_info()
    assert info["Meeting"]["Key"] == 1300
    assert info["Meeting"]["Circuit"] == {"Key": 152, "ShortName": "Las Vegas"}
    assert info["Type"] == "Qualifying" and info["Name"] == "Qualifying"
    assert info["StartDate"] == "2026-11-20T20:00:00"
    assert info["_source"] == "openf1"

    messages = quali.race_control()["Messages"]
    assert messages[0] == {"Utc": "2026-11-21T04:00:00+00:00", "Lap": 1, "Category": "SessionStatus",
                           "Message": "SESSION STARTED", "QualifyingPhase": 1}
    assert [m["Message"] for m in messages][-1] == "CHEQUERED FLAG"


def test_weather_stream_shape(quali):
    rows = quali.weather()
    assert rows[0]["AirTemp"] == "11.9" and rows[0]["TrackTemp"] == "14.0" and rows[0]["Rainfall"] == "0"
    assert rows[1]["Rainfall"] == "1"
    assert rows[0]["_timestamp"] == fmt_session_time(rel("2026-11-21T04:00:30"))


# --------------------------------------------------------------------------- unsupported / not available
def test_team_radio_is_unsupported(quali):
    with pytest.raises(UnsupportedStream):
        openf1_streams.load(quali.base_url + "TeamRadio.jsonStream")
    assert not openf1_streams.supports("TeamRadio.jsonStream")
    assert openf1_streams.supports("CarData.z.jsonStream")


def test_pre_2023_and_non_session_urls_are_unsupported(world):
    url = LIVETIMING + "2022/2022-03-20_Bahrain_Grand_Prix/2022-03-20_Race/TimingData.jsonStream"
    with pytest.raises(UnsupportedStream):
        openf1_streams.load(url)
    with pytest.raises(UnsupportedStream):
        openf1_streams.load(LIVETIMING + "2026/Index.json")
    assert len(world.rsps.calls) == 0


def test_future_session_has_no_car_data_and_costs_no_requests():
    fake = FakeOpenF1(quali_start="2030-03-01T04:00:00+00:00")
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        fake.install(rsps)
        store = FakeStore(session_path(QUALI_KEY))
        with pytest.raises(DataNotAvailableError):
            store.car_data()
        with pytest.raises(DataNotAvailableError):
            store.position_data()
        assert calls_to(rsps, "car_data") == []
        assert calls_to(rsps, "location") == []
        assert calls_to(rsps, "drivers") == []


def test_unknown_session_is_not_available(world):
    url = LIVETIMING + "2026/2026-03-01_Nowhere_Grand_Prix/2026-03-01_Race/TimingData.jsonStream"
    with pytest.raises(DataNotAvailableError):
        openf1_streams.load(url)
