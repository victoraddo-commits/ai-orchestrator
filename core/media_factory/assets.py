"""Asset registration, lineage and generation (§8/§55).

Image generation is REAL: ``generate_image`` resolves the OpenAI key from
kai-vault (path ``ai-orchestrator/providers/openai``), calls
``POST /v1/images/generations`` and persists the returned bytes under
``MEDIA_ROOT/assets/<sha256>.<ext>`` with a JSON sidecar. Video generation
stays honestly BLOCKED — ``generate()`` never fabricates media.

Nothing here logs or returns key material.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from core.media_factory import config, db

logger = logging.getLogger(__name__)

# OpenAI image bytes are PNG by default; sniff rather than trust the header.
_IMAGE_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


# ── Capability probes ──────────────────────────────────────────────────────
def resolve_openai_key() -> Optional[str]:
    """Provider key from kai-vault (env fallback), or None. Never logged."""
    try:
        from core.ai.kai_vault_client import fetch_for_provider

        key = fetch_for_provider(config.OPENAI_VAULT_PROVIDER)
        if key:
            return key.strip()
    except Exception as exc:  # noqa: BLE001 - vault is best-effort
        logger.warning("openai vault lookup failed (%s)", type(exc).__name__)
    env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return env_key or None


def image_asset_count() -> int:
    """Number of registered, hashed image assets (0 on any DB error)."""
    try:
        row = db.query_one(
            "SELECT count(*) AS n FROM assets WHERE kind = %s AND sha256 IS NOT NULL",
            ("image",),
        )
        return int(row["n"]) if row else 0
    except Exception as exc:  # noqa: BLE001 - capability probe must not raise
        logger.warning("image asset count failed (%s)", type(exc).__name__)
        return 0


def image_capability() -> dict:
    """Honest image-generation capability: VERIFIED only after a real image."""
    model = config.image_model()
    if not resolve_openai_key():
        return {
            "status": config.STATUS_BLOCKED,
            "provider": "openai",
            "model": model,
            "detail": "no OpenAI key in kai-vault (ai-orchestrator/providers/openai)",
            "blocked_reason": "no openai key",
            "images_produced": 0,
        }
    produced = image_asset_count()
    if produced > 0:
        return {
            "status": config.STATUS_VERIFIED,
            "provider": "openai",
            "model": model,
            "detail": f"{produced} image asset(s) generated via openai/{model}",
            "blocked_reason": config.BLOCKED_ASSET_VIDEO,
            "images_produced": produced,
        }
    return {
        "status": config.STATUS_PARTIALLY_VERIFIED,
        "provider": "openai",
        "model": model,
        "detail": "OpenAI image provider configured; no image generated yet",
        "blocked_reason": config.BLOCKED_ASSET_VIDEO,
        "images_produced": 0,
    }


def generation_capability() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    image = image_capability()
    return {
        "status": image["status"],
        "ffmpeg": bool(ffmpeg),
        "ffprobe": bool(ffprobe),
        "blocked_reason": config.BLOCKED_ASSET_GEN,
        "image": image,
    }


# ── Hashing / persistence ──────────────────────────────────────────────────
def sha256_file(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sniff_image_mime(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _assets_dir() -> Path:
    path = Path(config.MEDIA_ROOT) / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_image_bytes(
    data: bytes,
    *,
    prompt: str,
    model: str,
    size: str,
    source: str = "b64_json",
) -> dict:
    """Persist raw image bytes as ``<sha256>.<ext>`` plus a JSON sidecar.

    Blank/undecodable payloads are rejected — no placeholder media is written.
    """
    if not data:
        raise ValueError("empty image bytes")
    mime = _sniff_image_mime(data)
    if mime is None:
        raise ValueError("bytes are not a recognizable image")
    ext = _IMAGE_EXT_BY_MIME.get(mime, "bin")
    digest = hashlib.sha256(data).hexdigest()
    target = _assets_dir() / f"{digest}.{ext}"
    if not target.is_file() or target.stat().st_size != len(data):
        _atomic_write(target, data)
    sidecar = _assets_dir() / f"{digest}.json"
    _atomic_write(sidecar, json.dumps({
        "sha256": digest,
        "bytes": len(data),
        "mime": mime,
        "provider": "openai",
        "model": model,
        "size": size,
        "prompt": prompt,
        "path": str(target),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2).encode("utf-8"))
    return {
        "path": str(target),
        "uri": str(target),
        "sha256": digest,
        "bytes": len(data),
        "mime": mime,
        "sidecar": str(sidecar),
    }


# ── Registration / lineage (unchanged public surface) ──────────────────────
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


def _register_image_row(content_id: Optional[int], info: dict, *,
                        prompt: str, model: str, size: str) -> Optional[dict]:
    meta = {
        "provider": "openai",
        "model": model,
        "prompt": prompt,
        "size": size,
        "mime": info["mime"],
        "sidecar": info["sidecar"],
    }
    row = db.insert_returning(
        """
        INSERT INTO assets (content_id, kind, uri, sha256, bytes, meta, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (content_id, "image", info["uri"], info["sha256"], info["bytes"],
         db.jsonb(meta), config.STATUS_VERIFIED),
    )
    if not row:
        return None
    asset_id = row["id"]
    if content_id is not None:
        parent_id = None
        try:
            parent = db.query_one(
                "SELECT id FROM assets WHERE content_id = %s AND id <> %s "
                "ORDER BY created_at DESC LIMIT 1",
                (content_id, asset_id),
            )
            parent_id = parent["id"] if parent else None
        except Exception:  # noqa: BLE001 - lineage is additive
            parent_id = None
        link_lineage(
            asset_id,
            parent_id,
            relation="derived_from" if parent_id is not None else "generated_for",
            transform=f"openai:{model}" if parent_id is not None else f"content:{content_id}",
        )
    return row


# ── Video / generic generation stays blocked ───────────────────────────────
def generate(
    content_id: Optional[int],
    kind: str,
    *,
    prompt: Optional[str] = None,
) -> dict:
    """Video (and non-image) asset generation is BLOCKED.

    Deliberately does not create a placeholder media file. Image generation is
    available through :func:`generate_image`.
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


# ── Real image generation (OpenAI) ─────────────────────────────────────────
def _provider_error(resp) -> str:
    """Short, key-free provider error string."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or body)[:400]
        if err:
            return str(err)[:400]
    return (getattr(resp, "text", "") or "")[:400]


def _extract_images(payload: dict) -> list[tuple[bytes, str]]:
    """Decode the ``data`` array into (bytes, source) pairs (b64_json or url)."""
    items = payload.get("data")
    if not isinstance(items, list):
        return []
    out: list[tuple[bytes, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if b64:
            try:
                out.append((base64.b64decode(b64), "b64_json"))
            except Exception:  # noqa: BLE001
                logger.warning("openai image: undecodable b64_json item skipped")
            continue
        url = item.get("url")
        if url:
            resp = requests.get(url, timeout=config.image_timeout())
            resp.raise_for_status()
            out.append((resp.content, "url"))
    return out


def _fail_generation(content_id: Optional[int], prompt: str, model: str,
                     size: str, reason: str) -> dict:
    result = {
        "status": config.STATUS_FAILED,
        "provider": "openai",
        "model": model,
        "size": size,
        "prompt": prompt,
        "reason": reason,
        "assets": [],
        "asset_id": None,
    }
    db.record_event("asset_generation", config.STATUS_FAILED, detail=result)
    db.audit("asset.generate.failed", entity_type="content", entity_id=content_id,
             payload={"provider": "openai", "model": model, "reason": reason})
    return result


def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    n: int = 1,
    content_id: Optional[int] = None,
) -> dict:
    """Generate real image(s) via OpenAI and register them as assets.

    Returns a result dict whose ``status`` is one of VERIFIED / PARTIALLY_VERIFIED
    / BLOCKED / FAILED. No key => BLOCKED (never fabricated); provider or
    registration failure => FAILED, and no asset row is written for the failure.
    """
    prompt = (prompt or "").strip()
    model = (model or config.image_model()).strip()
    if not prompt:
        return _fail_generation(content_id, prompt, model, size, "empty prompt")

    key = resolve_openai_key()
    if not key:
        result = {
            "status": config.STATUS_BLOCKED,
            "provider": "openai",
            "model": model,
            "size": size,
            "prompt": prompt,
            "blocked_reason": "no openai key",
            "assets": [],
            "asset_id": None,
        }
        db.record_event("asset_generation", config.STATUS_BLOCKED, detail=result)
        db.audit("asset.generate.blocked", entity_type="content", entity_id=content_id,
                 payload={"provider": "openai", "model": model,
                          "blocked_reason": "no openai key"})
        return result

    try:
        count = max(1, int(n))
    except (TypeError, ValueError):
        count = 1

    url = f"{config.OPENAI_API_BASE.rstrip('/')}/images/generations"
    body = {"model": model, "prompt": prompt, "size": size, "n": count}
    started = time.time()
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=config.image_timeout(),
        )
    except requests.RequestException as exc:
        return _fail_generation(content_id, prompt, model, size,
                                f"provider request failed: {type(exc).__name__}")
    if resp.status_code != 200:
        return _fail_generation(content_id, prompt, model, size,
                                f"provider HTTP {resp.status_code}: {_provider_error(resp)}")
    try:
        payload = resp.json()
    except ValueError:
        return _fail_generation(content_id, prompt, model, size,
                                "provider returned non-JSON body")
    try:
        images = _extract_images(payload)
    except requests.RequestException as exc:
        return _fail_generation(content_id, prompt, model, size,
                                f"image download failed: {type(exc).__name__}")
    if not images:
        return _fail_generation(content_id, prompt, model, size,
                                "provider returned no image data")

    files: list[dict] = []
    rows: list[dict] = []
    errors: list[str] = []
    for raw, source in images:
        try:
            info = save_image_bytes(raw, prompt=prompt, model=model,
                                    size=size, source=source)
        except Exception as exc:  # noqa: BLE001 - report honestly, keep others
            errors.append(f"save failed: {type(exc).__name__}: {exc}")
            continue
        files.append(info)
        try:
            row = _register_image_row(content_id, info, prompt=prompt,
                                      model=model, size=size)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"register failed: {type(exc).__name__}")
            row = None
        if row:
            rows.append(row)

    if not rows:
        return _fail_generation(content_id, prompt, model, size,
                                "image saved but asset registration failed: "
                                + "; ".join(errors))

    status = config.STATUS_VERIFIED if not errors else config.STATUS_PARTIALLY_VERIFIED
    result = {
        "status": status,
        "provider": "openai",
        "model": model,
        "size": size,
        "prompt": prompt,
        "count": len(rows),
        "assets": rows,
        "files": files,
        "asset_id": rows[0].get("id"),
        "reason": "; ".join(errors) if errors else None,
        "elapsed_s": round(time.time() - started, 3),
    }
    db.record_event("asset_generation", status, detail={
        "provider": "openai", "model": model, "size": size,
        "count": len(rows), "sha256": [r.get("sha256") for r in rows],
    })
    db.audit("asset.generated", entity_type="asset", entity_id=result["asset_id"],
             payload={"provider": "openai", "model": model, "size": size,
                      "content_id": content_id, "count": len(rows),
                      "sha256": [r.get("sha256") for r in rows]})
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
