"""Strict prompt templates for the three reasoning passes (Phase 5 T2).

One template per pass — Advocate, Opponent, Judge — all sharing the same
grounding contract as :mod:`core.juris_kai.prompt`: the model may use ONLY the
supplied retrieved sources, must quote them, and must say plainly when the
sources do not support a point. Source text is neutralized and fenced exactly
as in ``build_grounded_prompt`` so untrusted corpus text cannot break out of
the quoted block.
"""
from __future__ import annotations

from core.juris_kai.legal_context import MAX_CHUNK_LENGTH
from core.juris_kai.prompt import _GROUNDED_SCOPE

try:
    from core.legal.injection import fence_user_content, neutralize
except Exception:  # noqa: BLE001 - optional guard, never block prompting
    def neutralize(text: str) -> str:
        return text or ""

    def fence_user_content(text: str) -> str:
        return text or ""


ADVOCATE = "advocate"
OPPONENT = "opponent"
JUDGE = "judge"

# Passage budgets live in ``prompt.TASK_MAX_TOKENS`` (the single canonical
# table) so ``streaming.generate`` bounds every pass.
PASS_TASK_TYPE = {
    ADVOCATE: "juris_advocate",
    OPPONENT: "juris_opponent",
    JUDGE: "juris_judge",
}

# Deep "fast" mode uses the lower-budget variants of the same passes.
PASS_TASK_TYPE_FAST = {
    ADVOCATE: "juris_advocate_fast",
    OPPONENT: "juris_opponent_fast",
    JUDGE: "juris_judge_fast",
}


def pass_task_type(pass_name: str, fast: bool = False) -> str:
    """Return the generation task type (and thus token budget) for a pass."""
    table = PASS_TASK_TYPE_FAST if fast else PASS_TASK_TYPE
    return table[pass_name]


def _sources_block(docs: list) -> str:
    """Fenced, neutralized list of retrieved sources (never citable otherwise)."""
    lines = []
    for i, d in enumerate(docs or [], 1):
        title = (d.get("title") or "Untitled").strip()
        citation = (d.get("citation") or "").strip()
        label = f"SOURCE {i}: {title}"
        if citation and citation != title:
            label += f" ({citation})"
        chunk = (d.get("chunk_content") or "")[:MAX_CHUNK_LENGTH]
        chunk = chunk.replace("<<<USER_CONTENT>>>", "[fence marker removed]")
        chunk = chunk.replace("<<<END_USER_CONTENT>>>", "[fence marker removed]")
        lines.append(f"{label}\n{fence_user_content(neutralize(chunk))}")
    return "\n\n".join(lines)


_STRICT = (
    "Use ONLY the supplied sources. Do not mention any statute, case, article, "
    "or fact that is not in them. Quote the source text briefly to support "
    "every point. If the sources do not support a point, say so plainly rather "
    "than guessing."
)


def build_advocate_prompt(query: str, docs: list) -> str:
    """Advocate: the strongest argument the supplied sources can support."""
    return (
        f"{_GROUNDED_SCOPE}\n\n"
        f"You are the ADVOCATE. Build the strongest possible argument that "
        f"answers the question below.\n\n"
        f"SOURCES:\n{_sources_block(docs)}\n\n"
        f"QUESTION (Ghana law): {query}\n\n"
        f"{_STRICT}\n"
        "State each point as a clear proposition, each backed by a quoted "
        "source. Write the argument now; do not repeat these instructions."
    )


def build_opponent_prompt(query: str, docs: list, argument: str) -> str:
    """Opponent: counter-arguments, contrary text, exceptions, and gaps."""
    return (
        f"{_GROUNDED_SCOPE}\n\n"
        f"You are the OPPONENT. Attack the argument below as a careful opposing "
        f"counsel would.\n\n"
        f"SOURCES:\n{_sources_block(docs)}\n\n"
        f"ADVOCATE ARGUMENT (context only — NOT a citable source):\n"
        f"{neutralize((argument or '').strip())}\n\n"
        f"QUESTION (Ghana law): {query}\n\n"
        "Find any contrary authority, exception, limitation, proviso, or "
        "repealed/amended provision in the sources "
        "(\"except\", \"shall not apply\", \"notwithstanding\", \"repealed\", "
        "\"amended\"). Identify gaps where the sources are silent. "
        f"{_STRICT}\n"
        "If the supplied sources contain no contrary authority, say exactly: "
        "\"No contrary authority in the supplied sources.\" "
        "Write the counter-argument now; do not repeat these instructions."
    )


def build_judge_prompt(query: str, advocate: str, opponent: str,
                       docs: list) -> str:
    """Judge: decide only what the sources establish, in IRAC form."""
    return (
        f"{_GROUNDED_SCOPE}\n\n"
        f"You are the JUDGE. Decide the question only on the supplied sources. "
        f"Never over-claim.\n\n"
        f"SOURCES:\n{_sources_block(docs)}\n\n"
        f"ADVOCATE (context only — NOT a citable source):\n"
        f"{neutralize((advocate or '').strip())}\n\n"
        f"OPPONENT (context only — NOT a citable source):\n"
        f"{neutralize((opponent or '').strip())}\n\n"
        f"QUESTION (Ghana law): {query}\n\n"
        "Mark what the sources ESTABLISH, what is DISPUTED (the authorities "
        "conflict), and what remains UNRESOLVED (the sources are silent or "
        "weak). Cite only the supplied sources, quoting them. "
        f"{_STRICT}\n"
        "Answer using exactly these four headed sections:\n"
        "ISSUE: <the legal question>\n"
        "RULE: <the rule drawn from the sources>\n"
        "APPLICATION: <apply the rule, noting disagreement>\n"
        "CONCLUSION: <what is established, disputed, and unresolved>"
    )
