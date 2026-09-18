"""Asset registration, lineage and (blocked) generation (§8/§55).

Generation is honestly BLOCKED: CT111 has no image/video models and no
ffmpeg (verified 2026-09-18). ``generate()`` therefore never produces a file;
it records the blocked status and reason. ``register_asset`` will hash a real
file that already exists.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)


def generation_capability() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return {
        "status": config.STATUS_BLOCKED,
        "ffmpeg": bool(ffmpeg),
        "ffprobe": bool(ffprobe),
        "blocked_reason": config.BLOCKED_ASSET_GEN,
    }


def sha256_file(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_asset(
    content_id: Optional[int],
    kind: str,
    uri: Optional[str],
    *,
    meta: Optional[dict] = None,
    parent_asset_id: Optional[int] = None,
    relation: str = "derived_from",
    transform: Optional[str] = None,
) -> dict:
    """Register a real on-disk asset (or an external URI) with hashing/lineage."""
    meta = dict(meta or {})
    digest = None
    size = None
    exists = False
    if uri and "://" not in uri:
        path = Path(uri)
        if path.is_file():
            exists = True
            digest = sha256_file(path)
            size = path.stat().st_size
    meta["exists"] = exists
    status = config.STATUS_VERIFIED if digest else config.STATUS_UNVERIFIED
    row = db.insert_returning(
        """
        INSERT INTO assets (content_id, kind, uri, sha256, bytes, meta, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (content_id, kind, uri, digest, size, db.jsonb(meta), status),
    )
    asset_id = row["id"]
    if parent_asset_id is not None:
        link_lineage(asset_id, parent_asset_id, relation=relation, transform=transform)
    db.audit("asset.registered", entity_type="asset", entity_id=asset_id,
             payload={"content_id": content_id, "kind": kind, "sha256": digest})
    return {"id": asset_id, "status": status, "sha256": digest, "exists": exists}


def link_lineage(
    asset_id: int,
    parent_asset_id: Optional[int],
    *,
    relation: str = "derived_from",
    transform: Optional[str] = None,
) -> Optional[int]:
    row = db.insert_returning(
        """
        INSERT INTO asset_lineage (asset_id, parent_asset_id, relation, transform, status)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (asset_id, parent_asset_id, relation, transform, config.STATUS_VERIFIED),
    )
    return row["id"] if row else None


def generate(
    content_id: Optional[int],
    kind: str,
    *,
    prompt: Optional[str] = None,
) -> dict:
    """Asset generation is BLOCKED — no media models / ffmpeg on CT111.

    Deliberately does not create a placeholder media file.
    """
    cap = generation_capability()
    result = {
        "status": config.STATUS_BLOCKED,
        "content_id": content_id,
        "kind": kind,
        "prompt": prompt,
        "blocked_reason": config.BLOCKED_ASSET_GEN,
        "ffmpeg": cap["ffmpeg"],
        "ffprobe": cap["ffprobe"],
        "asset_id": None,
    }
    db.record_event("asset_generation", config.STATUS_BLOCKED, detail=result)
    db.audit("asset.generate.blocked", entity_type="content", entity_id=content_id,
             payload=result)
    return result


def lineage_for(asset_id: int) -> list[dict]:
    return db.query(
        """
        SELECT l.*, p.uri AS parent_uri
        FROM asset_lineage l
        LEFT JOIN assets p ON p.id = l.parent_asset_id
        WHERE l.asset_id = %s
        ORDER BY l.created_at ASC
        """,
        (asset_id,),
    )


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM assets ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
