"""Request/response audit logging for the AI Gateway.

Every gateway request (success or failure) is recorded with:
  - trace_id  — unique per request, links request → provider call → response
  - consumer  — the api key that made the request
  - model     — the requested model/provider key
  - provider  — which provider actually served it
  - duration_ms — wall-clock latency
  - tokens    — provider-reported token counts (null when unavailable)
  - cost      — provider-reported cost (null when unavailable)
  - status_code — HTTP status returned to the consumer
  - timestamp

Storage: memory/gateway_audit.json (gitignored).
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STORAGE_PATH = Path(__file__).parent.parent.parent / "memory" / "gateway_audit.json"

# Keep at most this many records in the file to prevent unbounded growth
MAX_RECORDS = 10_000


def _load() -> list[dict]:
    try:
        if STORAGE_PATH.exists():
            data = json.loads(STORAGE_PATH.read_text())
            return data.get("records", [])
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save(records: list[dict]) -> None:
    tmp = STORAGE_PATH.with_suffix(".tmp")
    # Trim oldest if over limit
    if len(records) > MAX_RECORDS:
        records = records[-MAX_RECORDS:]
    tmp.write_text(json.dumps({"records": records, "schema_version": 1}, indent=2))
    tmp.replace(STORAGE_PATH)


def log_request(
    consumer: str,
    model: str,
    provider: str,
    duration_ms: int,
    tokens: Optional[dict] = None,
    cost: Optional[float] = None,
    status_code: int = 200,
    error: Optional[str] = None,
) -> str:
    """Append an audit record.  Returns the trace_id."""
    trace_id = uuid.uuid4().hex[:12]
    record = {
        "trace_id": trace_id,
        "consumer": consumer,
        "model": model,
        "provider": provider,
        "duration_ms": duration_ms,
        "tokens": tokens,
        "cost": cost,
        "status_code": status_code,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    records = _load()
    records.append(record)
    _save(records)
    return trace_id


def get_recent_requests(limit: int = 50) -> list[dict]:
    """Return the most recent audit entries, newest first."""
    records = _load()
    return records[-limit:][::-1]


def get_consumer_usage(consumer_id: str, since: Optional[str] = None) -> dict:
    """Aggregate usage for a specific API consumer.

    Returns:
        {"total_requests": N, "total_cost": float|None, "providers": {...},
         "models": {...}}
    """
    records = _load()
    consumer_records = [
        r for r in records
        if r.get("consumer") == consumer_id
    ]

    if since:
        consumer_records = [
            r for r in consumer_records
            if r.get("timestamp", "") >= since
        ]

    costs = [r.get("cost") for r in consumer_records
             if isinstance(r.get("cost"), (int, float))]
    provider_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}

    for r in consumer_records:
        p = r.get("provider", "unknown")
        m = r.get("model", "unknown")
        provider_counts[p] = provider_counts.get(p, 0) + 1
        model_counts[m] = model_counts.get(m, 0) + 1

    return {
        "total_requests": len(consumer_records),
        "total_cost": sum(costs) if costs else None,
        "providers": provider_counts,
        "models": model_counts,
    }
