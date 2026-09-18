"""Rights / provenance ledger and the publish gate (§9/§43).

Every asset must carry source, creator, jurisdiction, evidence and a rights
status. ``check_publishable`` BLOCKS publishing for any content without
verifiable provenance. "Old" is never assumed to mean public domain.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)

ACCEPTED_RIGHTS_STATUS = {"PUBLIC_DOMAIN", "LICENSED", "OWNED"}
REASON_NO_RECORD = "no rights/provenance record for content"
REASON_NO_EVIDENCE = "rights record has no verifiable evidence"
REASON_UNKNOWN = "rights status not established"


def record_rights(
    *,
    content_id: Optional[int] = None,
    asset_id: Optional[int] = None,
    source: Optional[str] = None,
    title: Optional[str] = None,
    creator: Optional[str] = None,
    work_date: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    license: Optional[str] = None,
    public_domain: bool = False,
    rights_status: str = "UNKNOWN",
    confidence: Optional[float] = None,
    restrictions: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> dict:
    evidence = evidence or {}
    status = (
        config.STATUS_VERIFIED
        if evidence and (public_domain or rights_status in ACCEPTED_RIGHTS_STATUS)
        else config.STATUS_UNVERIFIED
    )
    row = db.insert_returning(
        """
        INSERT INTO rights
            (asset_id, content_id, source, title, creator, work_date, jurisdiction,
             license, public_domain, rights_status, confidence, restrictions,
             evidence, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (asset_id, content_id, source, title, creator, work_date, jurisdiction,
         license, public_domain, rights_status, confidence, restrictions,
         db.jsonb(evidence), status),
    )
    rid = row["id"]
    db.audit("rights.recorded", entity_type="rights", entity_id=rid,
             payload={"content_id": content_id, "asset_id": asset_id,
                      "rights_status": rights_status, "public_domain": public_domain})
    return {"id": rid, "status": status,
            "publishable_hint": status == config.STATUS_VERIFIED}


def record_provenance(
    subject_type: str,
    subject_id: int,
    *,
    source: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> dict:
    evidence = evidence or {}
    status = config.STATUS_VERIFIED if evidence else config.STATUS_UNVERIFIED
    row = db.insert_returning(
        """
        INSERT INTO provenance (subject_type, subject_id, source, evidence, status)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (subject_type, subject_id, source, db.jsonb(evidence), status),
    )
    return {"id": row["id"], "status": status}


def evaluate_publishability(
    rights_rows: list[dict],
    provenance_rows: Optional[list[dict]] = None,
) -> dict:
    """Pure gate logic (unit-tested). Allow only verifiable provenance."""
    provenance_rows = provenance_rows or []
    if not rights_rows and not provenance_rows:
        return {"publishable": False, "status": config.STATUS_BLOCKED,
                "reason": REASON_NO_RECORD}
    for right in rights_rows:
        evidence = right.get("evidence") or {}
        if right.get("public_domain") and evidence:
            return {"publishable": True, "status": config.STATUS_VERIFIED,
                    "reason": "public domain with evidence",
                    "rights_id": right.get("id")}
        if evidence and right.get("rights_status") in ACCEPTED_RIGHTS_STATUS:
            return {"publishable": True, "status": config.STATUS_VERIFIED,
                    "reason": f"rights status {right.get('rights_status')} with evidence",
                    "rights_id": right.get("id")}
    for prov in provenance_rows:
        evidence = prov.get("evidence") or {}
        if evidence:
            return {"publishable": True, "status": config.STATUS_VERIFIED,
                    "reason": "provenance evidence present",
                    "provenance_id": prov.get("id")}
    if rights_rows and not any((r.get("evidence") or {}) for r in rights_rows):
        return {"publishable": False, "status": config.STATUS_BLOCKED,
                "reason": REASON_NO_EVIDENCE}
    return {"publishable": False, "status": config.STATUS_BLOCKED,
            "reason": REASON_UNKNOWN}


def check_publishable(content_id: int) -> dict:
    rights_rows = db.query("SELECT * FROM rights WHERE content_id = %s", (content_id,))
    provenance_rows = db.query(
        "SELECT * FROM provenance WHERE subject_type = 'content' AND subject_id = %s",
        (content_id,),
    )
    verdict = evaluate_publishability(rights_rows, provenance_rows)
    verdict["content_id"] = content_id
    db.record_event("rights_check", verdict["status"], detail=verdict)
    db.audit("rights.check", entity_type="content", entity_id=content_id, payload=verdict)
    return verdict


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM rights ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
