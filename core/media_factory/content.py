"""Idea → story → script generation on the local model fabric (§8/§9/§10).

Uses the local Ollama OpenAI-compatible endpoint (``qwen3-coder:kai``). If the
model is unreachable the stage reports BLOCKED with the reason — content text
is never synthesized to look as if the model produced it.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)

_USER_AGENT = "KAI-MediaFactory/1.0"


class ModelUnavailable(RuntimeError):
    pass


def model_available(*, base_url: Optional[str] = None, timeout: float = 5.0) -> tuple[bool, str]:
    """Probe the local model endpoint. Returns (available, detail)."""
    base = (base_url or config.LLM_BASE_URL).rstrip("/")
    request = urllib.request.Request(
        f"{base}/api/tags", headers={"User-Agent": _USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        names = {m.get("name") for m in payload.get("models", [])}
        if config.LLM_MODEL in names:
            return True, f"{config.LLM_MODEL} present on {base}"
        return False, f"{config.LLM_MODEL} not in /api/tags on {base}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def chat(
    messages: list[dict],
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.7,
    timeout: Optional[float] = None,
) -> dict:
    """Call the local model. Raises ModelUnavailable on any failure."""
    base = (base_url or config.LLM_BASE_URL).rstrip("/")
    body = json.dumps(
        {
            "model": model or config.LLM_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout or config.LLM_TIMEOUT
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc
    choices = payload.get("choices") or []
    if not choices:
        raise ModelUnavailable("model returned no choices")
    text = (choices[0].get("message") or {}).get("content") or ""
    return {"text": text.strip(), "model": payload.get("model", config.LLM_MODEL)}


# ── Prompt builders (pure, unit-testable) ──────────────────────────────────
_SYSTEM = (
    "You are the story team of an autonomous media factory. Produce original, "
    "policy-safe content. Never impersonate real people, never reproduce "
    "copyrighted text, and clearly label synthetic media. Be concise."
)


def build_idea_prompt(trend_title: str, factory_key: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Factory: {factory_key}.\n"
                f"Trend signal: {trend_title!r}.\n"
                "Propose ONE original content idea (2 sentences): hook + angle."
            ),
        },
    ]


def build_story_prompt(idea_text: str, factory_key: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Factory: {factory_key}.\nIdea: {idea_text}\n"
                "Write a short story beat outline (beginning, turn, cliffhanger), "
                "max 120 words."
            ),
        },
    ]


def build_script_prompt(story_text: str, factory_key: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Factory: {factory_key}.\nStory: {story_text}\n"
                "Write a 30-second vertical-video script with narration lines only."
            ),
        },
    ]


def _generate(prompt: list[dict]) -> dict:
    """Return {status, text, model, blocked_reason}. BLOCKED when unavailable."""
    try:
        result = chat(prompt)
        return {
            "status": config.STATUS_PARTIALLY_VERIFIED,
            "text": result["text"],
            "model": result["model"],
            "blocked_reason": None,
        }
    except ModelUnavailable as exc:
        return {
            "status": config.STATUS_BLOCKED,
            "text": None,
            "model": config.LLM_MODEL,
            "blocked_reason": f"local model unavailable: {exc}",
        }


def create_from_opportunity(
    opportunity: dict,
    *,
    content_kind: str = "short",
    account_id: Optional[int] = None,
) -> dict:
    """Persist a content item and attempt idea → story → script generation."""
    factory_key = opportunity.get("factory_key") or "public_domain_cartoon"
    title = (opportunity.get("angle") or "untitled opportunity")[:200]
    content_row = db.insert_returning(
        """
        INSERT INTO content
            (opportunity_id, factory_key, account_id, title, kind, state, status)
        VALUES (%s, %s, %s, %s, %s, 'IDEA', %s)
        RETURNING id
        """,
        (
            opportunity.get("id"),
            factory_key,
            account_id,
            title,
            content_kind,
            config.STATUS_PARTIALLY_VERIFIED,
        ),
    )
    content_id = content_row["id"]
    db.audit("content.created", entity_type="content", entity_id=content_id,
             payload={"opportunity_id": opportunity.get("id"), "factory_key": factory_key})

    idea = _generate(build_idea_prompt(title, factory_key))
    if idea["status"] != config.STATUS_BLOCKED and idea["text"]:
        db.execute(
            "INSERT INTO ideas (content_id, title, premise, status) VALUES (%s, %s, %s, %s)",
            (content_id, title, idea["text"], idea["status"]),
        )
        story = _generate(build_story_prompt(idea["text"], factory_key))
    else:
        story = {"status": config.STATUS_BLOCKED, "text": None,
                 "blocked_reason": idea["blocked_reason"], "model": idea["model"]}

    if story["status"] != config.STATUS_BLOCKED and story["text"]:
        db.execute(
            "INSERT INTO content_dna (content_id, format, dna, status) VALUES (%s, %s, %s, %s)",
            (content_id, "story_outline", db.jsonb({"story": story["text"]}), story["status"]),
        )
        script = _generate(build_script_prompt(story["text"], factory_key))
    else:
        script = {"status": config.STATUS_BLOCKED, "text": None,
                  "blocked_reason": story.get("blocked_reason"), "model": story.get("model")}

    if script["status"] != config.STATUS_BLOCKED and script["text"]:
        db.execute(
            """
            INSERT INTO scripts (content_id, version, body, model, model_status, status)
            VALUES (%s, 1, %s, %s, %s, %s)
            """,
            (content_id, script["text"], script["model"], script["status"], script["status"]),
        )
        db.execute(
            "UPDATE content SET state = 'SCRIPTING', status = %s, updated_at = now() WHERE id = %s",
            (config.STATUS_PARTIALLY_VERIFIED, content_id),
        )

    overall = (
        config.STATUS_PARTIALLY_VERIFIED
        if script["status"] != config.STATUS_BLOCKED
        else config.STATUS_BLOCKED
    )
    result = {
        "status": overall,
        "content_id": content_id,
        "factory_key": factory_key,
        "idea_status": idea["status"],
        "story_status": story["status"],
        "script_status": script["status"],
        "blocked_reason": script.get("blocked_reason"),
        "model": script.get("model"),
    }
    if overall == config.STATUS_BLOCKED:
        db.execute(
            "UPDATE content SET status = %s, updated_at = now() WHERE id = %s",
            (config.STATUS_BLOCKED, content_id),
        )
        db.record_event("content_generation", config.STATUS_BLOCKED, detail=result)
        db.audit("content.generation.blocked", entity_type="content",
                 entity_id=content_id, payload=result)
    else:
        db.record_event("content_generation", config.STATUS_PARTIALLY_VERIFIED, detail=result)
        db.audit("content.generation", entity_type="content", entity_id=content_id,
                 payload=result)
    return result


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM content ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def get(content_id: int) -> Optional[dict]:
    return db.query_one("SELECT * FROM content WHERE id = %s", (content_id,))
