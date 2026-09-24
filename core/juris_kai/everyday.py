"""Everyday-Law menu + rendering for Juris Kai (Phase 7, Task 4).

The grounded explainer itself is produced on the Legal Brain (CT 100,
``core.legal.plain``) and fetched through :mod:`core.legal_brain_client`. This
module only owns the Telegram surface: the topic menu, the label→topic mapping,
and the text rendering. The outbound text is gated by the caller through the
AgentGuard output gate + citation firewall, exactly like every other legal
answer.

The label map mirrors the Legal Brain topic keys so the menu works offline; a
topic the brain does not know returns an honest notice, never a guess.
"""
from __future__ import annotations

import json

#: Topic key -> Telegram button label (mirrors core.legal.plain.TOPICS order).
TOPIC_LABELS = [
    ("traffic-tint", "🚗 Traffic & vehicle tint"),
    ("tenancy-rent", "🏠 Tenancy & rent"),
    ("employment-rights", "💼 Employment rights"),
    ("consumer-rights", "🛒 Consumer rights"),
    ("marriage-divorce", "💍 Marriage & divorce"),
    ("police-stops", "🚓 Police stops & liberty"),
    ("individual-taxes", "💰 Individual taxes"),
]

LABEL_TO_KEY = {label: key for key, label in TOPIC_LABELS}
KEY_TO_LABEL = dict(TOPIC_LABELS)

DEFAULT_DISCLAIMER = ("Informational only — this is a plain-language summary of "
                      "published Ghanaian law, not legal advice.")

INTRO = (
    "📖 *Everyday Law*\n\n"
    "Plain-language summaries built only from published Ghanaian law — each one "
    "names its controlling instrument and its currency. Choose a topic, or use "
    "`/everyday <topic>`.\n\n"
    "_Informational only — not legal advice._"
)


def _keyboard(rows) -> str:
    return json.dumps({
        "keyboard": [[{"text": btn} for btn in row] for row in rows],
        "resize_keyboard": True,
    })


def everyday_menu() -> str:
    """Reply keyboard listing every Everyday-Law topic."""
    rows = [[label] for _key, label in TOPIC_LABELS]
    rows.append(["🔙 Back to Menu"])
    return _keyboard(rows)


def resolve_topic(value: str) -> str | None:
    """Resolve a topic key, label or partial label to a topic key.

    Accepts the exact key (``tenancy-rent``), the exact button label, or a
    case-insensitive substring of either. Returns ``None`` when nothing matches
    (the caller must then answer honestly, never guess a topic).
    """
    v = (value or "").strip()
    if not v:
        return None
    low = v.lower()
    if low in KEY_TO_LABEL:
        return low
    for key, label in TOPIC_LABELS:
        if low == label.lower() or low in label.lower() or low in key:
            return key
    return None


def render_explainer(data: dict) -> str:
    """Render a brain explainer payload as Telegram text (Markdown)."""
    data = data or {}
    if data.get("error") and not data.get("grounded"):
        return ("📖 *Everyday Law*\n\nI couldn't reach the legal database just "
                "now. Please try again shortly.\n\n"
                f"_{data.get('error')}_")
    label = data.get("label") or KEY_TO_LABEL.get(data.get("topic"), "Everyday Law")
    if not data.get("grounded"):
        notice = data.get("notice") or ("No authoritative Ghanaian source for "
                                        "this topic is in the database yet.")
        return (f"📖 *Everyday Law — {label}*\n\n{notice}\n\n"
                f"_{data.get('disclaimer') or DEFAULT_DISCLAIMER}_")

    lines = [f"📖 *Everyday Law — {label}*", "", "*What the law says:*",
             data.get("explainer", "")]
    inst = data.get("instrument") or {}
    if inst.get("title"):
        cite = inst.get("citation") or ""
        cite = f" ({cite})" if cite and cite != inst["title"] else ""
        lines += ["", f"*Controlling instrument:* {inst['title']}{cite}",
                  f"*Currency:* {data.get('currency', 'UNKNOWN')}"]
    sources = data.get("sources") or []
    if sources:
        lines.append("")
        lines.append("*Sources*")
        for i, s in enumerate(sources, 1):
            title = (s.get("title") or "Untitled").strip()
            cite = (s.get("citation") or "").strip()
            bit = f"{i}. {title}"
            if cite and cite != title:
                bit += f" — {cite}"
            status = (s.get("temporal_status") or "").strip().upper()
            if status:
                bit += f" [{status}]"
            lines.append(bit)
    lines += ["", f"_{data.get('disclaimer') or DEFAULT_DISCLAIMER}_"]
    return "\n".join(lines)
