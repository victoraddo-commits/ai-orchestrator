"""Research Session Logger (KAI 2.0 phase 18D).

Captures Claude Code / research sessions in a lightweight ledger so the
Command Center and later analytics can answer "what did we look at, when,
and with what outcome" without parsing every transcript on disk.

Records live in ``memory/research_sessions.json`` with schema:

    {
      "schema_version": 1,
      "records": [
        {
          "session_id": "<12 hex chars>",
          "operator": "<user id or handle>",
          "purpose": "<short human string>",
          "started_at": "<iso8601 UTC>",
          "ended_at": "<iso8601 UTC | null>",
          "transcript_ref": "<path | null>",
          "artifacts": ["<path>", ...],
          "outcome": "completed" | "abandoned" | "failed" | null,
          "tags": [...]
        }
      ]
    }
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.memory import load, save


_STORE = "research_sessions.json"
_SCHEMA_VERSION = 1
_VALID_OUTCOMES = {"completed", "abandoned", "failed"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    data = load(_STORE)
    if not isinstance(data, dict) or "records" not in data:
        data = {"schema_version": _SCHEMA_VERSION, "records": []}
    if not isinstance(data.get("records"), list):
        data["records"] = []
    return data


def _save(data: dict[str, Any]) -> None:
    data.setdefault("schema_version", _SCHEMA_VERSION)
    save(_STORE, data)


def log_session(
    purpose: str,
    operator: str,
    transcript_ref: str | None = None,
    artifacts: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Append a new research session record and return it.

    ``started_at`` is set to now; ``ended_at`` / ``outcome`` are None until
    :func:`finalize_session` is called.
    """
    record = {
        "session_id": uuid.uuid4().hex[:12],
        "operator": operator,
        "purpose": purpose,
        "started_at": _now_iso(),
        "ended_at": None,
        "transcript_ref": transcript_ref,
        "artifacts": list(artifacts or []),
        "outcome": None,
        "tags": list(tags or []),
    }
    data = _load()
    data["records"].append(record)
    _save(data)
    return record


def finalize_session(
    session_id: str,
    outcome: str,
    artifacts: list[str] | None = None,
) -> dict[str, Any] | None:
    """Set ``ended_at`` + ``outcome`` on the session; optionally extend
    artifacts. Idempotent — re-finalizing overwrites outcome/ended_at.

    Returns the updated record or ``None`` if not found.
    """
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(
            f"invalid outcome {outcome!r}; expected one of {sorted(_VALID_OUTCOMES)}"
        )

    data = _load()
    for record in data["records"]:
        if record.get("session_id") == session_id:
            record["outcome"] = outcome
            record["ended_at"] = _now_iso()
            if artifacts:
                merged = list(record.get("artifacts") or [])
                for a in artifacts:
                    if a not in merged:
                        merged.append(a)
                record["artifacts"] = merged
            _save(data)
            return record
    return None


def list_sessions(
    limit: int = 50,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Return sessions in reverse chronological order (newest first).

    ``since_iso`` filters to sessions whose ``started_at >= since_iso``.
    """
    data = _load()
    records = list(data["records"])
    if since_iso is not None:
        records = [r for r in records if (r.get("started_at") or "") >= since_iso]
    records.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    if limit is not None and limit >= 0:
        records = records[:limit]
    return records


def get_session(session_id: str) -> dict[str, Any] | None:
    """Return the session with matching id, or ``None`` if not found."""
    data = _load()
    for record in data["records"]:
        if record.get("session_id") == session_id:
            return record
    return None
