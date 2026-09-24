"""TDD: CT 111 weekly legal harvest job (scheduler side).

Covers the Telegram summary formatting, the single-instance lock (no overlap)
and the run() wiring — all with injected fakes so no network/Telegram call is
made.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import legal_harvest_cycle as job  # noqa: E402


def _report(**over):
    report = {
        "ok": True,
        "sources_used": ["parliament-dspace", "parliament-oai"],
        "totals": {"new": 2, "updated": 1, "duplicates": 3, "skipped": 0,
                   "failed": 1, "failures": [{"title": "X Act", "reason": "boom"}]},
        "coverage": {"before": {"with_content": 474}, "after": {"with_content": 478},
                     "delta": {"with_content": 4, "weak_after": ["human_rights"]}},
        "stubs": {"selected": 3,
                  "summary": {"updated": 2, "failed": 1,
                              "failures": [{"title": "Y Act", "reason": "no text"}]}},
        "report_paths": ["/opt/kai-legal-brain/data/reports/harvest_cycle_2026-09-23.json"],
    }
    report.update(over)
    return report


class FormatSummaryTest(unittest.TestCase):
    def test_full_summary_has_counts_and_coverage(self):
        text = job.format_summary(_report())
        self.assertIn("weekly harvest", text)
        self.assertIn("new=2", text)
        self.assertIn("dup=3", text)
        self.assertIn("474 → 478", text)
        self.assertIn("+4", text)
        self.assertIn("upgraded=2", text)
        self.assertIn("human_rights", text)
        self.assertIn("parliament-dspace", text)
        self.assertIn("Failures", text)

    def test_skipped_report(self):
        text = job.format_summary({"ok": False, "skipped": "already running"})
        self.assertIn("Skipped", text)

    def test_error_report(self):
        text = job.format_summary({"ok": False, "error": "HTTP 500"})
        self.assertIn("failed", text.lower())
        self.assertIn("HTTP 500", text)

    def test_tolerates_missing_sections(self):
        text = job.format_summary({"ok": True})
        self.assertIn("weekly harvest", text)


class LockTest(unittest.TestCase):
    def test_lock_is_exclusive_and_releases(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.lock")
            fd1 = job.acquire_lock(path)
            self.assertIsNotNone(fd1)
            self.assertIsNone(job.acquire_lock(path))  # held -> refused
            job.release_lock(fd1)
            fd2 = job.acquire_lock(path)
            self.assertIsNotNone(fd2)
            job.release_lock(fd2)


class FakeClient:
    def __init__(self, report=None, raise_exc=False):
        self.report = report or _report()
        self.raise_exc = raise_exc
        self.calls = []

    def harvest_cycle(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise RuntimeError("brain down")
        return self.report


class RunTest(unittest.TestCase):
    def _alert_recorder(self):
        sent = []
        return sent, (lambda text, **kw: sent.append(text))

    def test_run_sends_telegram_summary(self):
        with tempfile.TemporaryDirectory() as d:
            client = FakeClient()
            sent, alert = self._alert_recorder()
            result = job.run(limit=4, stub_limit=2, delay=0.5,
                             lock_path=os.path.join(d, "h.lock"),
                             client=client, alert=alert)
            self.assertEqual(client.calls,
                             [{"limit": 4, "stub_limit": 2, "delay": 0.5,
                               "dry_run": False}])
            self.assertEqual(len(sent), 1)
            self.assertIn("new=2", sent[0])
            self.assertIs(result["report"], client.report)

    def test_overlap_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.lock")
            held = job.acquire_lock(path)
            try:
                sent, alert = self._alert_recorder()
                result = job.run(lock_path=path, client=FakeClient(), alert=alert)
                self.assertEqual(result["skipped"], "already running")
                self.assertEqual(sent, [])
            finally:
                job.release_lock(held)

    def test_client_error_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            sent, alert = self._alert_recorder()
            result = job.run(lock_path=os.path.join(d, "h.lock"),
                             client=FakeClient(raise_exc=True), alert=alert)
            self.assertFalse(result["report"]["ok"])
            self.assertIn("brain down", sent[0])

    def test_no_notify_skips_telegram(self):
        with tempfile.TemporaryDirectory() as d:
            sent, alert = self._alert_recorder()
            job.run(lock_path=os.path.join(d, "h.lock"),
                    client=FakeClient(), alert=alert, notify=False)
            self.assertEqual(sent, [])


def _health(**over):
    health = {
        "docs": 1445, "with_content": 1320, "unknown_status": 715,
        "stale_days": 180,
        "temporal_counts": {"CURRENT": 325, "AMENDED": 17, "REPEALED": 1,
                            "PROPOSED": 387, "UNKNOWN": 715},
        "integrity": {"hash_mismatches": 593, "duplicates": 2},
        "stale_authorities": [], "suspect_docs": [{"document_id": 1539}],
    }
    health.update(over)
    return health


class HealthSummaryTest(unittest.TestCase):
    """Phase 6 T3: the weekly Telegram summary carries the knowledge health."""

    def test_format_health_has_key_numbers(self):
        text = job.format_health(_health())
        self.assertIn("Knowledge health", text)
        self.assertIn("UNKNOWN=715", text)
        self.assertIn("hash mismatches=593", text)
        self.assertIn("Suspect docs: 1", text)

    def test_format_health_empty_is_blank(self):
        self.assertEqual(job.format_health(None), "")
        self.assertEqual(job.format_health({}), "")

    def test_summary_appends_health(self):
        text = job.format_summary(_report(), _health())
        self.assertIn("Knowledge health", text)
        self.assertIn("UNKNOWN=715", text)

    def test_summary_without_health_unchanged(self):
        self.assertNotIn("Knowledge health", job.format_summary(_report()))


class FakeHealthClient(FakeClient):
    def __init__(self, health, **kw):
        super().__init__(**kw)
        self._health = health

    def legal_health(self, **kw):
        return self._health


class HealthRunTest(unittest.TestCase):
    def test_run_includes_health(self):
        with tempfile.TemporaryDirectory() as d:
            sent = []
            job.run(lock_path=os.path.join(d, "h.lock"),
                    client=FakeHealthClient(_health()),
                    alert=lambda text, **kw: sent.append(text))
            self.assertIn("Knowledge health", sent[0])

    def test_health_failure_does_not_break_run(self):
        class BoomClient(FakeClient):
            def legal_health(self, **kw):
                raise RuntimeError("health down")

        with tempfile.TemporaryDirectory() as d:
            sent = []
            result = job.run(lock_path=os.path.join(d, "h.lock"),
                             client=BoomClient(),
                             alert=lambda text, **kw: sent.append(text))
            self.assertTrue(result["report"]["ok"])
            self.assertNotIn("Knowledge health", sent[0])


class ChangeAlertRelayTest(unittest.TestCase):
    """Phase 6 T1: the brain's LEGAL CHANGE ALERT is relayed via Telegram."""

    def test_alert_appended_to_summary(self):
        report = _report(legal_changes={
            "alert": "⚖️ *LEGAL CHANGE ALERT*\n1. *LAW:* Foo Bill 2024\n"
                     "   *STATUS:* PROPOSED — NOT YET LAW"})
        text = job.format_summary(report)
        self.assertIn("LEGAL CHANGE ALERT", text)
        self.assertIn("NOT YET LAW", text)

    def test_no_alert_when_absent(self):
        text = job.format_summary(_report())
        self.assertNotIn("LEGAL CHANGE ALERT", text)


if __name__ == "__main__":
    unittest.main()
