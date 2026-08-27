"""Core File Fabric: identity, hashing, dedup, storage, classification.

Security posture (§8/§32/§57):
- every file treated as untrusted until scanned
- SHA-256 content identity; transport events separate from content
- duplicates detected by hash → new event links to existing File ID
- size limits + path-traversal-safe storage names
- verdicts recorded; rejected files quarantined, never processed
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

_MEMORY_DIR = Path("/project/ai-orchestrator/memory/filefabric")
STORAGE_DIR = Path("/project/uploads/filefabric")
REGISTRY_PATH = _MEMORY_DIR / "files.json"
EVENTS_PATH = _MEMORY_DIR / "file_events.jsonl"

MAX_FILE_BYTES = 50 * 1024 * 1024   # §25: configurable limit
QUARANTINE_DIR = STORAGE_DIR / ".quarantine"

_MIME_SKIP = re.compile(r"\.(png|jpg|jpeg|gif|webp|mp4|mp3|ogg|oga|webm)$", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs():
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> dict:
    try:
        with open(REGISTRY_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {"schema_version": 1, "files": {}}


def _save_registry(reg: dict) -> None:
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, default=str))
    os.replace(tmp, REGISTRY_PATH)


def _event(file_id: str, event: str, detail: dict | None = None) -> None:
    _ensure_dirs()
    with open(EVENTS_PATH, "a") as fh:
        fh.write(json.dumps({"ts": _now(), "file_id": file_id, "event": event,
                             "detail": detail or {}}, default=str) + "\n")


def classify(filename: str, mime: str, head: bytes) -> dict:
    """Content-aware classification (§12). Explainable, rule-based v1."""
    name = filename.lower()
    mime = (mime or "").lower()
    if name.endswith((".pdf",)) or mime == "application/pdf":
        kind = "document"
        hint = "PDF document"
    elif name.endswith((".doc", ".docx", ".odt")) or "word" in mime:
        kind = "document"; hint = "word-processor document"
    elif name.endswith((".xls", ".xlsx", ".csv")) or "spreadsheet" in mime or "excel" in mime:
        kind = "data"; hint = "spreadsheet/CSV data"
    elif name.endswith((".ppt", ".pptx")):
        kind = "document"; hint = "presentation"
    elif name.endswith((".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")):
        kind = "archive"; hint = "compressed archive"
    elif name.endswith((".apk", ".exe", ".bin", ".deb", ".sh", ".bat")) or \
         (head[:2] == b"MZ"):
        kind = "executable"; hint = "executable/installer/binary — sandbox required"
    elif mime.startswith("image/"):
        kind = "image"; hint = "image (OCR/vision candidates)"
    elif mime.startswith("audio/") or name.endswith((".oga", ".ogg", ".mp3", ".m4a")):
        kind = "audio"; hint = "audio (transcription candidate)"
    elif mime.startswith("video/"):
        kind = "video"; hint = "video"
    elif name.endswith((".py", ".js", ".ts", ".kt", ".java", ".go", ".rs", ".sh", ".yaml", ".yml", ".json", ".xml", ".toml")) or name.endswith("gradlew"):
        kind = "code"; hint = "source/config code"
    else:
        kind = "unknown"; hint = "unrecognized — sandbox + content sniff"
    return {"kind": kind, "hint": hint}


def security_scan(data: bytes, filename: str, classification: dict) -> dict:
    """§8 security pipeline v1: size, path traversal, embedded executables,
    archive-bomb size guard. Malware AV engine integration is a TODO hook."""
    verdict = {"safe": True, "flags": []}
    if len(data) > MAX_FILE_BYTES:
        verdict["safe"] = False
        verdict["flags"].append(f"size {len(data)} exceeds limit {MAX_FILE_BYTES}")
    if re.search(r"(\.\./|/etc/|/proc/|\x00)", filename):
        verdict["safe"] = False
        verdict["flags"].append("path traversal pattern in filename")
    if classification["kind"] == "archive" and len(data) > 20 * 1024 * 1024:
        verdict["flags"].append("large archive — extraction must be sandboxed")
    if classification["kind"] == "executable":
        verdict["flags"].append("executable content — quarantine until approved")
        verdict["safe"] = False
    if data[:2] == b"MZ" and classification["kind"] not in ("executable",):
        verdict["safe"] = False
        verdict["flags"].append("embedded Windows executable in non-exe file")
    return verdict


def ingest(data: bytes, filename: str, mime: str, sender: str,
           source: str = "telegram", chat_id: str = "", message_id: str = "") -> dict:
    """Full intake: identity → hash → dedup → scan → store → register.
    Returns the file record (or quarantine record for unsafe files)."""
    _ensure_dirs()
    if not data:
        return {"ok": False, "error": "empty file"}
    file_id = f"KAI-FILE-{secrets.token_hex(4).upper()}"
    sha = hashlib.sha256(data).hexdigest()
    classification = classify(filename, mime, data[:512])
    scan = security_scan(data, filename, classification)

    reg = _load_registry()
    # dedup by hash (§7): reuse existing stored file
    existing = next((f for f in reg["files"].values()
                     if f.get("sha256") == sha and f.get("status") != "rejected"), None)

    safe_name = re.sub(r"[^A-Za-z0-9 ._\-]", "_", filename)[:100] or "unnamed"
    record = {
        "file_id": file_id,
        "original_name": filename,
        "safe_name": safe_name,
        "mime": mime,
        "size": len(data),
        "sha256": sha,
        "sender": sender,
        "source": source,
        "chat_id": chat_id,
        "message_id": message_id,
        "created_at": _now(),
        "classification": classification,
        "security": scan,
        "status": "received",
        "retention": "normal",
        "tags": [],
    }

    if not scan["safe"]:
        qdir = QUARANTINE_DIR / file_id
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / safe_name).write_bytes(data)
        record["status"] = "rejected"
        record["storage"] = str(qdir / safe_name)
        reg["files"][file_id] = record
        _save_registry(reg)
        _event(file_id, "REJECTED", {"flags": scan["flags"]})
        return {"ok": False, "rejected": True, "record": record}

    if existing:
        # duplicate: link event to existing file, no second physical copy
        existing.setdefault("duplicates", []).append({
            "file_id": file_id, "sender": sender, "chat_id": chat_id,
            "message_id": message_id, "ts": _now()})
        record["status"] = "duplicate"
        record["duplicate_of"] = existing["file_id"]
        record["storage"] = existing.get("storage")
        reg["files"][file_id] = record
        _save_registry(reg)
        _event(existing["file_id"], "DUPLICATE_RECEIVED", {"new_event_id": file_id})
        return {"ok": True, "duplicate_of": existing["file_id"], "record": record}

    # fresh store
    fdir = STORAGE_DIR / file_id
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / safe_name).write_bytes(data)
    record["storage"] = str(fdir / safe_name)
    record["status"] = "stored"
    reg["files"][file_id] = record
    _save_registry(reg)
    _event(file_id, "STORED", {"size": len(data), "kind": classification["kind"]})
    return {"ok": True, "record": record, "file_id": file_id}


def get_file(file_id: str) -> dict | None:
    return _load_registry()["files"].get(file_id)


def read_file(file_id: str) -> bytes | None:
    f = get_file(file_id)
    if not f or f.get("status") == "rejected":
        return None
    try:
        return Path(f["storage"]).read_bytes()
    except Exception:
        return None


def recent(limit: int = 20) -> list:
    reg = _load_registry()
    rows = sorted(reg["files"].values(), key=lambda f: f.get("created_at", ""), reverse=True)
    return rows[:limit]
