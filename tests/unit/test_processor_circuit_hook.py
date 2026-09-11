"""The background processor derives a circuit layout after a session completes.

Offline: ``BackgroundProcessor`` is built with its Mongo-backed
``_load_processed_sessions`` and ``PlotDataGenerator`` stubbed, and every
async collaborator of ``process_session`` is replaced so the test only
exercises the hook wiring (flag on/off, ``gp_name`` threading, fail-open).
"""
import asyncio
from types import SimpleNamespace

import pytest

from src.workers import processor as mod


class _Generator:
    def generate_all_session_data(self, year, gp, session, gp_name=None):
        return {"generated": True}


@pytest.fixture
def proc(monkeypatch):
    monkeypatch.setattr(mod.BackgroundProcessor, "_load_processed_sessions", lambda self: None)
    monkeypatch.setattr(mod, "PlotDataGenerator", _Generator)
    p = mod.BackgroundProcessor(check_interval=1)
    # process_session's post-generation steps touch Redis/Mongo helpers; make
    # them inert so the test stays offline regardless of what they do.
    for name in ("_invalidate_session_cache", "_persist_processed_session"):
        if hasattr(p, name):
            monkeypatch.setattr(p, name, lambda *a, **k: None)
    return p


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_hook_runs_with_gp_name_when_flag_on(proc, monkeypatch):
    calls = []
    monkeypatch.setattr(mod.settings, "auto_derive_circuits", True)
    monkeypatch.setattr(mod.settings, "enable_v2_stream_prewarm", False)
    monkeypatch.setattr(proc, "ensure_circuit_layout", lambda *a: calls.append(a))

    result = _run(proc.process_session(2026, 16, "R", gp_name="Spanish Grand Prix"))

    assert calls == [(2026, 16, "R", "Spanish Grand Prix")]
    assert result.get("status") != "error"


def test_hook_skipped_when_flag_off(proc, monkeypatch):
    calls = []
    monkeypatch.setattr(mod.settings, "auto_derive_circuits", False)
    monkeypatch.setattr(mod.settings, "enable_v2_stream_prewarm", False)
    monkeypatch.setattr(proc, "ensure_circuit_layout", lambda *a: calls.append(a))

    _run(proc.process_session(2026, 16, "R", gp_name="Spanish Grand Prix"))

    assert calls == []


def test_hook_failure_does_not_fail_processing(proc, monkeypatch):
    monkeypatch.setattr(mod.settings, "auto_derive_circuits", True)
    monkeypatch.setattr(mod.settings, "enable_v2_stream_prewarm", False)

    def _boom(*a):
        raise RuntimeError("livetiming down")

    monkeypatch.setattr(proc, "ensure_circuit_layout", _boom)

    result = _run(proc.process_session(2026, 16, "R", gp_name="Spanish Grand Prix"))

    assert result.get("status") != "error"
    assert "2026_16_R" in proc.processed_sessions


def test_ensure_circuit_layout_delegates_by_gp_name(proc, monkeypatch):
    seen = {}

    def _fake(year, gp, session):
        seen.update(year=year, gp=gp, session=session)
        return {"circuit_id": "153", "circuit_name": "Madring",
                "stats": {"n_corners": 17, "lap_length_m": 5339.3}}

    monkeypatch.setattr("src.services.circuit_derivation.ensure_circuit_layout", _fake)

    proc.ensure_circuit_layout(2026, 16, "R", gp_name="Spanish Grand Prix")

    assert seen == {"year": 2026, "gp": "Spanish Grand Prix", "session": "R"}


def test_upcoming_sessions_carry_event_name(proc, monkeypatch):
    """The schedule row index is off by one for V2 when testing is listed, so
    the event name must travel with the tuple into ``process_session``."""
    import pandas as pd

    now = pd.Timestamp.now(tz="UTC")
    schedule = pd.DataFrame([
        {"EventName": "Pre-Season Testing", "Session5Date": now - pd.Timedelta(days=30)},
        {"EventName": "Spanish Grand Prix", "Session5Date": now - pd.Timedelta(hours=2)},
    ])
    monkeypatch.setattr(mod.fastf1, "get_event_schedule", lambda year: schedule)

    upcoming = proc.get_upcoming_sessions()

    assert upcoming == [(now.year, 2, "R", upcoming[0][3], "Spanish Grand Prix")]

    seen = SimpleNamespace(args=None)

    async def _fake_process(year, gp, session, gp_name=None):
        seen.args = (year, gp, session, gp_name)
        return {"status": "ok"}

    monkeypatch.setattr(proc, "check_session_completed", lambda *a: True)
    monkeypatch.setattr(proc, "process_session", _fake_process)
    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    _run(proc.check_and_process_sessions())

    assert seen.args == (now.year, 2, "R", "Spanish Grand Prix")


async def _no_sleep(*_a, **_k):
    return None
