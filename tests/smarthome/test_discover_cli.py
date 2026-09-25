import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import registry as R
from scripts.smarthome_discover import ingest_candidates
from core.smarthome.discovery import Candidate


def test_ingest_writes_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_default_memory_dir", lambda: tmp_path)
    cands = [
        Candidate(ip="192.168.1.16", mac="50:8a:06:6a:3c:36", provider="tuya",
                  provider_id="bf123", vendor="Tuya Smart", ports=[6668]),
        Candidate(ip="192.168.1.90", provider="homeassistant", ports=[8123]),
    ]
    report = ingest_candidates(cands)
    assert report["created"] == 2
    providers = {d.provider for d in R.list_devices()}
    assert providers == {"tuya", "homeassistant"}


def test_ingest_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_default_memory_dir", lambda: tmp_path)
    cands = [Candidate(ip="192.168.1.16", provider="tuya", provider_id="bf123",
                       vendor="Tuya Smart", ports=[6668])]
    first = ingest_candidates(cands)
    second = ingest_candidates(cands)
    assert first["created"] == 1
    assert second["updated"] == 1 and second["total"] == 1
