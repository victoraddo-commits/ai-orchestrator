"""Asset registration, lineage and generation (§8/§55).

Image generation is REAL: ``generate_image`` prefers Gemini (key at
kai-vault path ``ai-orchestrator/providers/gemini``, ``:generateContent``
inline base64) and falls back to OpenAI (``ai-orchestrator/providers/openai``,
``/v1/images/generations``). Returned bytes are persisted under
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


def resolve_gemini_key() -> Optional[str]:
    """Gemini provider key from kai-vault (env fallback), or None.

    Uses the same machine-plane mechanism as :func:`resolve_openai_key`; the
    vault path is ``ai-orchestrator/providers/gemini``. Never logged.
    """
    try:
        from core.ai.kai_vault_client import fetch_for_provider

        key = fetch_for_provider(config.GEMINI_VAULT_PROVIDER)
        if key:
            return key.strip()
    except Exception as exc:  # noqa: BLE001 - vault is best-effort
        logger.warning("gemini vault lookup failed (%s)", type(exc).__name__)
    env_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()
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
    """Honest image-generation capability: VERIFIED only after a real image.

    Gemini is the primary provider; OpenAI is the fallback. ``provider`` names
    the preferred configured provider, while ``providers`` reports exactly
    which keys resolve so /status can explain what is actually wired up.
    """
    has_gemini = bool(resolve_gemini_key())
    has_openai = bool(resolve_openai_key())
    providers = {"gemini": has_gemini, "openai": has_openai}
    if has_gemini:
        provider, model = "gemini", config.gemini_image_model()
    else:
        provider, model = "openai", config.image_model()
    produced = image_asset_count()
    if produced > 0:
        return {
            "status": config.STATUS_VERIFIED,
            "provider": provider,
            "model": model,
            "providers": providers,
            "detail": f"{produced} image asset(s) generated via {provider}/{model}",
            "blocked_reason": config.BLOCKED_ASSET_VIDEO,
            "images_produced": produced,
        }
    if not (has_gemini or has_openai):
        return {
            "status": config.STATUS_BLOCKED,
            "provider": provider,
            "model": model,
            "providers": providers,
            "detail": "no image provider key configured (gemini/openai)",
            "blocked_reason": "no image provider key",
            "images_produced": 0,
        }
    configured = ", ".join(name for name, ok in providers.items() if ok)
    return {
        "status": config.STATUS_PARTIALLY_VERIFIED,
        "provider": provider,
        "model": model,
        "providers": providers,
        "detail": f"image provider configured ({configured}); no image generated yet",
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
    provider: str = "openai",
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
        "provider": provider,
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
                        prompt: str, model: str, size: str,
                        provider: str = "openai") -> Optional[dict]:
    meta = {
        "provider": provider,
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
            transform=f"{provider}:{model}" if parent_id is not None else f"content:{content_id}",
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


# ── Real image generation (Gemini primary, OpenAI fallback) ────────────────
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


def _extract_gemini_images(payload: dict) -> list[tuple[bytes, str]]:
    """First inline image part(s) from a Gemini ``generateContent`` response.

    Walks ``candidates[].content.parts[].inlineData`` (snake_case tolerated),
    base64-decodes each and returns ``(bytes, mime)`` pairs.
    """
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return []
    out: list[tuple[bytes, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            data = inline.get("data")
            if not data:
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            try:
                out.append((base64.b64decode(data), str(mime)))
            except Exception:  # noqa: BLE001
                logger.warning("gemini image: undecodable inlineData skipped")
    return out


def _extract_images(payload: dict) -> list[tuple[bytes, str]]:
    """Decode the OpenAI ``data`` array into (bytes, source) pairs."""
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
                     size: str, reason: str, *,
                     provider: str = "openai") -> dict:
    result = {
        "status": config.STATUS_FAILED,
        "provider": provider,
        "model": model,
        "size": size,
        "prompt": prompt,
        "reason": reason,
        "assets": [],
        "asset_id": None,
    }
    db.record_event("asset_generation", config.STATUS_FAILED, detail=result)
    db.audit("asset.generate.failed", entity_type="content", entity_id=content_id,
             payload={"provider": provider, "model": model, "reason": reason})
    return result


def _blocked_generation(content_id: Optional[int], prompt: str, model: str,
                        size: str, *, provider: str, reason: str) -> dict:
    result = {
        "status": config.STATUS_BLOCKED,
        "provider": provider,
        "model": model,
        "size": size,
        "prompt": prompt,
        "blocked_reason": reason,
        "assets": [],
        "asset_id": None,
    }
    db.record_event("asset_generation", config.STATUS_BLOCKED, detail=result)
    db.audit("asset.generate.blocked", entity_type="content", entity_id=content_id,
             payload={"provider": provider, "model": model,
                      "blocked_reason": reason})
    return result


def _persist_generated_images(
    images: list[tuple[bytes, str]],
    *,
    provider: str,
    prompt: str,
    model: str,
    size: str,
    content_id: Optional[int],
    started: float,
) -> dict:
    """Save + register provider images; shared by every provider path."""
    files: list[dict] = []
    rows: list[dict] = []
    errors: list[str] = []
    for raw, source in images:
        try:
            info = save_image_bytes(raw, prompt=prompt, model=model,
                                    size=size, source=source, provider=provider)
        except Exception as exc:  # noqa: BLE001 - report honestly, keep others
            errors.append(f"save failed: {type(exc).__name__}: {exc}")
            continue
        files.append(info)
        try:
            row = _register_image_row(content_id, info, prompt=prompt,
                                      model=model, size=size, provider=provider)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"register failed: {type(exc).__name__}")
            row = None
        if row:
            rows.append(row)

    if not rows:
        return _fail_generation(content_id, prompt, model, size,
                                "image saved but asset registration failed: "
                                + "; ".join(errors), provider=provider)

    status = config.STATUS_VERIFIED if not errors else config.STATUS_PARTIALLY_VERIFIED
    result = {
        "status": status,
        "provider": provider,
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
        "provider": provider, "model": model, "size": size,
        "count": len(rows), "sha256": [r.get("sha256") for r in rows],
    })
    db.audit("asset.generated", entity_type="asset", entity_id=result["asset_id"],
             payload={"provider": provider, "model": model, "size": size,
                      "content_id": content_id, "count": len(rows),
                      "sha256": [r.get("sha256") for r in rows]})
    return result


def generate_image_gemini(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    n: int = 1,
    content_id: Optional[int] = None,
) -> dict:
    """Generate image(s) via Gemini and register them as assets.

    Calls ``POST {GEMINI_API_BASE}/models/{model}:generateContent?key=...`` and
    persists the first inline image part. Returns VERIFIED / PARTIALLY_VERIFIED /
    BLOCKED (no key) / FAILED (provider or registration error); never fabricates.
    """
    prompt = (prompt or "").strip()
    model = (model or config.gemini_image_model()).strip()
    if not prompt:
        return _fail_generation(content_id, prompt, model, size, "empty prompt",
                                provider="gemini")

    key = resolve_gemini_key()
    if not key:
        return _blocked_generation(content_id, prompt, model, size,
                                   provider="gemini", reason="no gemini key")

    url = f"{config.GEMINI_API_BASE.rstrip('/')}/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    started = time.time()
    try:
        resp = requests.post(url, params={"key": key}, json=body,
                             timeout=config.gemini_image_timeout())
    except requests.RequestException as exc:
        return _fail_generation(content_id, prompt, model, size,
                                f"provider request failed: {type(exc).__name__}",
                                provider="gemini")
    if resp.status_code != 200:
        return _fail_generation(content_id, prompt, model, size,
                                f"provider HTTP {resp.status_code}: {_provider_error(resp)}",
                                provider="gemini")
    try:
        payload = resp.json()
    except ValueError:
        return _fail_generation(content_id, prompt, model, size,
                                "provider returned non-JSON body", provider="gemini")

    images = _extract_gemini_images(payload)
    if not images:
        return _fail_generation(content_id, prompt, model, size,
                                "provider returned no image data", provider="gemini")
    return _persist_generated_images(images, provider="gemini", prompt=prompt,
                                     model=model, size=size,
                                     content_id=content_id, started=started)


def generate_image_openai(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    n: int = 1,
    content_id: Optional[int] = None,
) -> dict:
    """Generate real image(s) via OpenAI and register them as assets (fallback)."""
    prompt = (prompt or "").strip()
    model = (model or config.image_model()).strip()
    if not prompt:
        return _fail_generation(content_id, prompt, model, size, "empty prompt",
                                provider="openai")

    key = resolve_openai_key()
    if not key:
        return _blocked_generation(content_id, prompt, model, size,
                                   provider="openai", reason="no openai key")

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
                                f"provider request failed: {type(exc).__name__}",
                                provider="openai")
    if resp.status_code != 200:
        return _fail_generation(content_id, prompt, model, size,
                                f"provider HTTP {resp.status_code}: {_provider_error(resp)}",
                                provider="openai")
    try:
        payload = resp.json()
    except ValueError:
        return _fail_generation(content_id, prompt, model, size,
                                "provider returned non-JSON body", provider="openai")
    try:
        images = _extract_images(payload)
    except requests.RequestException as exc:
        return _fail_generation(content_id, prompt, model, size,
                                f"image download failed: {type(exc).__name__}",
                                provider="openai")
    if not images:
        return _fail_generation(content_id, prompt, model, size,
                                "provider returned no image data", provider="openai")
    return _persist_generated_images(images, provider="openai", prompt=prompt,
                                     model=model, size=size,
                                     content_id=content_id, started=started)


def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    n: int = 1,
    content_id: Optional[int] = None,
) -> dict:
    """Generate real image(s), preferring Gemini then falling back to OpenAI.

    Returns VERIFIED / PARTIALLY_VERIFIED on success. If no provider key
    resolves the result is BLOCKED; if every configured provider fails the
    result is FAILED. No placeholder media is ever written.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return _fail_generation(content_id, prompt, model or "", size,
                                "empty prompt", provider="none")

    attempts: list[dict] = []
    if resolve_gemini_key():
        result = generate_image_gemini(prompt, model=model, size=size, n=n,
                                       content_id=content_id)
        if result["status"] in (config.STATUS_VERIFIED,
                                config.STATUS_PARTIALLY_VERIFIED):
            return result
        attempts.append(result)

    if resolve_openai_key():
        result = generate_image_openai(prompt, model=model, size=size, n=n,
                                       content_id=content_id)
        if result["status"] in (config.STATUS_VERIFIED,
                                config.STATUS_PARTIALLY_VERIFIED):
            return result
        attempts.append(result)

    if not attempts:
        return _blocked_generation(
            content_id, prompt, model or config.gemini_image_model(), size,
            provider="gemini+openai",
            reason="no image provider key (gemini/openai)")

    if len(attempts) == 1:
        return attempts[0]

    return _fail_generation(
        content_id, prompt, model or "", size,
        "all providers failed: " + " | ".join(
            f"{a.get('provider')}: {a.get('reason')}" for a in attempts),
        provider="gemini+openai")


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
