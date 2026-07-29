"""13P: periodic WATCHDOG=1 pings during a scheduler cycle, not only after.

run_cycle() can block for many minutes on a single coding-agent subprocess
call. These tests verify the WatchdogHeartbeat background thread pings
notify("WATCHDOG=1") on its interval while a cycle runs, and stops cleanly
(no leaked thread) once the cycle returns -- including when it raises.
"""

import threading
import time
import types

import pytest

import core.scheduler as scheduler
from core.scheduler import WatchdogHeartbeat


def _heartbeat_threads():
    return [
        t for t in threading.enumerate() if t.name == "watchdog-heartbeat"
    ]


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class TestWatchdogHeartbeat:

    def test_pings_periodically_while_running(self):
        calls = []

        hb = WatchdogHeartbeat(interval=0.02, notify_fn=calls.append)
        hb.start()

        try:
            assert _wait_for(lambda: len(calls) >= 3)
        finally:
            hb.stop()

        assert all(c == "WATCHDOG=1" for c in calls)

    def test_stop_terminates_thread_without_leak(self):
        hb = WatchdogHeartbeat(interval=0.02, notify_fn=lambda msg: None)
        hb.start()

        assert len(_heartbeat_threads()) == 1

        hb.stop()

        assert _heartbeat_threads() == []
        assert hb._thread is None

    def test_stop_is_prompt_despite_long_interval(self):
        # The thread waits on the stop event, not a blind sleep, so stopping
        # must not take anywhere near a full interval.
        hb = WatchdogHeartbeat(interval=60, notify_fn=lambda msg: None)
        hb.start()

        started = time.monotonic()
        hb.stop()

        assert time.monotonic() - started < 5
        assert _heartbeat_threads() == []

    def test_no_pings_after_stop(self):
        calls = []

        hb = WatchdogHeartbeat(interval=0.02, notify_fn=calls.append)
        hb.start()

        assert _wait_for(lambda: len(calls) >= 1)
        hb.stop()

        count_at_stop = len(calls)
        time.sleep(0.1)

        assert len(calls) == count_at_stop

    def test_no_thread_when_module_notify_is_none(self, monkeypatch):
        # Outside systemd (ImportError fallback) notify is None: the
        # heartbeat must be a no-op, not a crash.
        monkeypatch.setattr(scheduler, "notify", None)

        hb = WatchdogHeartbeat(interval=0.02)
        hb.start()

        assert _heartbeat_threads() == []

        hb.stop()

    def test_ping_exception_does_not_kill_heartbeat(self):
        calls = []

        def flaky(msg):
            calls.append(msg)
            if len(calls) == 1:
                raise RuntimeError("sd_notify failed")

        hb = WatchdogHeartbeat(interval=0.02, notify_fn=flaky)
        hb.start()

        try:
            assert _wait_for(lambda: len(calls) >= 3)
        finally:
            hb.stop()

    def test_context_manager_starts_and_stops(self):
        calls = []

        with WatchdogHeartbeat(interval=0.02, notify_fn=calls.append):
            assert len(_heartbeat_threads()) == 1
            assert _wait_for(lambda: len(calls) >= 1)

        assert _heartbeat_threads() == []

    def test_context_manager_stops_on_exception(self):
        with pytest.raises(RuntimeError):
            with WatchdogHeartbeat(interval=0.02, notify_fn=lambda msg: None):
                assert len(_heartbeat_threads()) == 1
                raise RuntimeError("cycle blew up")

        assert _heartbeat_threads() == []

    def test_default_interval_well_under_watchdog_sec(self):
        # systemd unit runs with WatchdogSec=600; the ping interval must
        # leave a wide margin.
        assert scheduler.WATCHDOG_PING_INTERVAL <= 60

        hb = WatchdogHeartbeat(notify_fn=lambda msg: None)
        assert hb.interval == scheduler.WATCHDOG_PING_INTERVAL


class TestSchedulerLoopIntegration:
    """Drive scheduler.start() through exactly one loop iteration."""

    class _StopLoop(Exception):
        pass

    def _run_one_iteration(self, monkeypatch, tmp_path, run_cycle_fn, pings):
        monkeypatch.setattr(scheduler, "WATCHDOG_PING_INTERVAL", 0.02)
        monkeypatch.setattr(scheduler, "notify", pings.append)
        monkeypatch.setattr(scheduler, "run_cycle", run_cycle_fn)
        monkeypatch.setattr(
            scheduler, "Path", lambda p: tmp_path / "heartbeat"
        )

        def stop_loop(seconds):
            raise self._StopLoop()

        # time.sleep(INTERVAL) between iterations is outside the try/except,
        # so raising here exits the otherwise-infinite loop after one cycle.
        # Stub the module attribute rather than time.sleep itself so the
        # tests' own time.sleep calls keep working.
        monkeypatch.setattr(
            scheduler, "time", types.SimpleNamespace(sleep=stop_loop)
        )

        with pytest.raises(self._StopLoop):
            scheduler.start()

    def test_heartbeat_pings_during_long_cycle(self, monkeypatch, tmp_path):
        pings = []
        pings_seen_mid_cycle = []

        def slow_cycle():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                mid = [p for p in pings if p == "WATCHDOG=1"]
                if len(mid) >= 3:
                    pings_seen_mid_cycle.extend(mid)
                    break
                time.sleep(0.005)
            return {"findings": [], "incidents": [], "decisions": []}

        self._run_one_iteration(monkeypatch, tmp_path, slow_cycle, pings)

        # Multiple pings landed while run_cycle() was still executing --
        # the pre-13P behavior (single ping only after return) would show 0.
        assert len(pings_seen_mid_cycle) >= 3

    def test_heartbeat_thread_stopped_after_cycle(self, monkeypatch, tmp_path):
        def cycle():
            assert len(_heartbeat_threads()) == 1
            return {"findings": [], "incidents": [], "decisions": []}

        self._run_one_iteration(monkeypatch, tmp_path, cycle, [])

        assert _heartbeat_threads() == []

    def test_heartbeat_thread_stopped_when_cycle_raises(
        self, monkeypatch, tmp_path
    ):
        def failing_cycle():
            assert len(_heartbeat_threads()) == 1
            raise RuntimeError("generation exploded")

        self._run_one_iteration(monkeypatch, tmp_path, failing_cycle, [])

        assert _heartbeat_threads() == []
