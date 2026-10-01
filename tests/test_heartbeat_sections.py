"""Tests for core.workers.heartbeat_sections (TASK HB)."""

import json
import time

import pytest

from core.workers import heartbeat_sections as hb


class TestServiceSection:
    def test_all_healthy(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_json", lambda url, timeout=hb.PROBE_TIMEOUT, tls_lan=False: (
            {"worker": {"inbox": 7}} if "8770" in url else ({"status": "ok"} if "8095" in url else (_ for _ in ()).throw(AssertionError(url)))
        ))
        monkeypatch.setattr(hb, "_vault_alive", lambda: True)
        status, parts = hb._service_section()
        assert status == "ok"
        assert any("sms ok" in p for p in parts)
        assert any("akush ok" in p for p in parts)
        assert any("vault ok" in p for p in parts)

    def test_sms_down_is_bad(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_json", lambda url, timeout=hb.PROBE_TIMEOUT, tls_lan=False: (
            (_ for _ in ()).throw(ConnectionError()) if "8770" in url else {"status": "ok"}
        ))
        monkeypatch.setattr(hb, "_vault_alive", lambda: True)
        status, parts = hb._service_section()
        assert status == "bad"
        assert any("unreachable" in p for p in parts)

    def test_vault_down_degrades(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_json", lambda url, timeout=hb.PROBE_TIMEOUT, tls_lan=False: (
            {"worker": {"inbox": 0}} if "8770" in url else {"status": "ok"}
        ))
        monkeypatch.setattr(hb, "_vault_alive", lambda: False)
        status, _ = hb._service_section()
        assert status == "degraded"


class TestBridgeSection:
    def test_clean_bridge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hb, "MEMORY_DIR", tmp_path)
        (tmp_path / "money_sms_bridge_state.json").write_text(
            json.dumps({"counters": {"forwarded": 21, "dead_letter_count": 0}, "last_processed_id": "sms-abc"})
        )
        status, lines = hb._bridge_section()
        assert status == "ok"
        assert "forwarded 21" in lines[0] and "dead-letter 0" in lines[0]

    def test_dead_letter_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hb, "MEMORY_DIR", tmp_path)
        (tmp_path / "money_sms_bridge_state.json").write_text(
            json.dumps({"counters": {"forwarded": 21, "dead_letter_count": 3}})
        )
        status, _ = hb._bridge_section()
        assert status == "degraded"

    def test_missing_state_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hb, "MEMORY_DIR", tmp_path)
        status, lines = hb._bridge_section()
        assert status == "unknown"
        assert "unknown" in lines[0]


class TestBrainSection:
    def test_tunnel_up_with_model(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_json", lambda url, timeout=hb.PROBE_TIMEOUT, tls_lan=False: {"models": [{"name": "qwen3-coder:kai"}]})
        status, lines = hb._brain_section()
        assert status == "ok"
        assert "present" in lines[0]

    def test_tunnel_down_is_bad(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_json", lambda url, timeout=hb.PROBE_TIMEOUT, tls_lan=False: (_ for _ in ()).throw(ConnectionError()))
        status, _ = hb._brain_section()
        assert status == "bad"


class TestIncidentSection:
    def _journal(self, tmp_path, rows):
        j = tmp_path / "kai_event_journal.jsonl"
        j.write_text("\n".join(json.dumps(r) for r in rows))
        return tmp_path

    def test_counts_since(self, tmp_path, monkeypatch):
        now = time.time()
        monkeypatch.setattr(hb, "MEMORY_DIR", self._journal(tmp_path, [
            {"topic": "money.sms.bridge_failed", "ts": now - 10},
            {"topic": "money.sms.bridge_failed", "ts": now - 5},
            {"topic": "money.bill.detected", "ts": now - 3},
            {"topic": "money.sms.bridge_failed", "ts": now - 999999},
        ]))
        status, lines = hb._incident_section(since=now - 60)
        assert status == "degraded"
        assert "bridge_failed x2" in lines[0]

    def test_no_incidents(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hb, "MEMORY_DIR", self._journal(tmp_path, [{"topic": "money.bill.detected", "ts": time.time()}]))
        status, lines = hb._incident_section(since=time.time() - 60)
        assert status == "ok"
        assert "no incidents" in lines[0]


class TestFormat:
    def test_never_crashes_and_bounded(self, monkeypatch):
        def boom(url, timeout=hb.PROBE_TIMEOUT, tls_lan=False):
            raise RuntimeError(url)
        monkeypatch.setattr(hb, "_fetch_json", boom)
        monkeypatch.setattr(hb, "_vault_alive", lambda: True)
        out = hb.format_sections(since=0.0, bot_client=None, max_lines=10)
        assert len(out.splitlines()) <= 10
        assert ("❓" in out) or ("🔴" in out) or ("⚠️" in out)  # unknown or degraded, never fabricated-ok

    def test_no_secret_looking_output(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_json", lambda url, timeout=hb.PROBE_TIMEOUT, tls_lan=False: (
            {"worker": {"inbox": 1}} if "8770" in url else {"status": "ok"}
        ))
        monkeypatch.setattr(hb, "_vault_alive", lambda: True)
        out = hb.format_sections(since=0.0, bot_client=None)
        for token in ("Bearer", "sk-", "password=", "token="):
            assert token not in out

    def test_collect_returns_all_sections(self):
        sections = hb.collect_sections(since=0.0, bot_client=None)
        titles = [t for t, _, _ in sections]
        assert titles == ["Services", "Money", "SMS bridge", "Infrastructure", "Brain", "Incidents"]
