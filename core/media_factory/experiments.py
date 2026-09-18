"""Creative experiment engine (§23).

Deterministic A/B assignment and honest readouts: with no outcome data a
readout is UNVERIFIED, never a fabricated lift.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)


def assign_variant(key: str, unit_id: str) -> str:
    """Deterministic 50/50 assignment (pure, unit-tested)."""
    digest = hashlib.sha256(f"{key}:{unit_id}".encode("utf-8")).hexdigest()
    return "a" if int(digest, 16) % 2 == 0 else "b"


def compute_readout(
    variant_a: list[float],
    variant_b: list[float],
) -> dict:
    """Pure readout. No data → UNVERIFIED and no invented effect size."""
    if not variant_a or not variant_b:
        return {
            "status": config.STATUS_UNVERIFIED,
            "reason": "insufficient outcome data",
            "n_a": len(variant_a),
            "n_b": len(variant_b),
            "lift": None,
        }
    mean_a = sum(variant_a) / len(variant_a)
    mean_b = sum(variant_b) / len(variant_b)
    lift = ((mean_b - mean_a) / mean_a) if mean_a else None
    return {
        "status": config.STATUS_PARTIALLY_VERIFIED,
        "mean_a": mean_a,
        "mean_b": mean_b,
        "lift": lift,
        "n_a": len(variant_a),
        "n_b": len(variant_b),
        "note": "observational; not a significance test",
    }


def create(
    key: str,
    *,
    name: Optional[str] = None,
    hypothesis: Optional[str] = None,
    metric: str = "views",
    variant_a: Optional[str] = None,
    variant_b: Optional[str] = None,
) -> dict:
    row = db.insert_returning(
        """
        INSERT INTO experiments
            (key, name, hypothesis, metric, variant_a, variant_b, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET updated_at = now()
        RETURNING *
        """,
        (key, name or key, hypothesis, metric, variant_a, variant_b,
         config.STATUS_UNVERIFIED),
    )
    db.audit("experiment.created", entity_type="experiment", entity_id=row["id"],
             payload={"key": key, "metric": metric})
    return row


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM experiments ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def readout(experiment_id: int) -> dict:
    experiment = db.query_one("SELECT * FROM experiments WHERE id = %s", (experiment_id,))
    if not experiment:
        return {"status": config.STATUS_MISSING, "blocked_reason": "experiment not found"}
    # Outcomes come from analytics rows tagged with the experiment metric, if any.
    rows = db.query(
        "SELECT value FROM analytics WHERE metric = %s",
        (experiment.get("metric") or "views",),
    )
    values = [float(r["value"]) for r in rows if r.get("value") is not None]
    if not values:
        result = {"status": config.STATUS_UNVERIFIED,
                  "experiment_id": experiment_id,
                  "reason": "no outcome data for metric",
                  "metric": experiment.get("metric")}
    else:
        half = len(values) // 2
        result = compute_readout(values[:half], values[half:])
        result["experiment_id"] = experiment_id
    db.execute(
        "UPDATE experiments SET readout = %s, status = %s, updated_at = now() WHERE id = %s",
        (db.jsonb(result), result["status"], experiment_id),
    )
    db.audit("experiment.readout", entity_type="experiment", entity_id=experiment_id,
             payload=result)
    return result
