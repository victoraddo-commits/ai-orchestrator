"""Prompt engineering for Juris Kai Legal Assistant — Ghana Law Only

This module builds Ghana-scoped legal prompts. Every prompt enforces:
  1. Jurisdiction = Republic of Ghana ONLY
  2. Consult the bot's local knowledge base / database as primary source
  3. Never discuss, reference, or compare to other countries' laws
"""

try:
    from core.legal.injection import fence_user_content, neutralize
except Exception:  # noqa: BLE001 - optional guard, never block prompting
    def neutralize(text: str) -> str:
        return text or ""

    def fence_user_content(text: str) -> str:
        return text or ""


_JURISDICTION_GATE = (
    "IMPORTANT: You are Juris Kai, a Ghanaian legal assistant. "
    "You ONLY answer questions about Ghana law (Republic of Ghana). "
    "If the user's question is about any other country's laws, legal system, "
    "or jurisdiction, respond ONLY with: "
    '"I only handle Ghana legal matters. Please ask a question about Ghana law." '
    "Do NOT compare Ghana law to other countries. Do NOT reference foreign cases, "
    "statutes, or legal principles — not even as examples or context. "
    "Every citation, case name, statute, and legal principle you mention MUST be Ghanaian. "
)

_DATABASE_FIRST = (
    "Before answering, check the bot's local knowledge base and database for relevant "
    "Ghana legal documents, cases, statutes, and precedents. "
    "Cite specific Ghanaian sources from the database whenever possible. "
    "If the database has no relevant information, state that clearly and provide "
    "only what you know with certainty about Ghana law — do NOT fabricate or guess. "
)

_PREAMBLE = _JURISDICTION_GATE + "\n" + _DATABASE_FIRST

# Per-task generation ceilings (tokens). These bound both the streaming path
# (Ollama ``num_predict``) and are the canonical budget tables the Command
# Center displays. Teaching/flashcards stay short; research gets more room.
TASK_MAX_TOKENS = {
    "legal_teaching": 700,
    "legal_case_analysis": 800,
    "legal_research": 1100,
    "legal_argument": 900,
    "legal_flashcards": 450,
    "juris_legal_teaching": 700,
    "juris_case_analysis": 800,
    "juris_research": 1100,
    "juris_argument_construction": 900,
    "juris_flashcards": 450,
    "juris_chat": 700,
}
DEFAULT_MAX_TOKENS = 800


def budget_for(task_type: str) -> int:
    """Return the ``num_predict``/max-token budget for a task type."""
    return TASK_MAX_TOKENS.get(task_type or "", DEFAULT_MAX_TOKENS)


def max_tokens_for(task_type: str) -> int:
    """Alias used by non-streamed local providers."""
    return budget_for(task_type)


def build_prompt(task_type: str, content: str, context: str = "") -> str:
    """Build Ghana-scoped legal prompts based on task type.

    ``context`` is optional bounded follow-up context (last few turns) so
    anaphoric follow-ups ("and the penalty?") are answered with the prior
    question/answer in view. It is inserted directly after the jurisdiction
    preamble and is expected to be char-capped by the caller.
    """
    ctx_block = f"\n\n{context.strip()}" if (context or "").strip() else ""

    if task_type == "legal_teaching":
        return (
            f"{_PREAMBLE}{ctx_block}\n\n"
            f"TASK: Explain the Ghanaian legal concept: '{content}'.\n"
            "Provide key examples and relevant Ghana legal principles. "
            "Reference specific Ghanaian statutes, cases, and constitutional provisions. "
            "Keep your response concise (300-500 words) and structured for a Ghana law student."
        )

    elif task_type == "legal_case_analysis":
        return (
            f"{_PREAMBLE}{ctx_block}\n\n"
            f"TASK: Analyze this Ghana legal case: '{content}'.\n"
            "Discuss the key Ghana legal principles, the Ghanaian court's reasoning, "
            "and the impact on Ghanaian legal doctrine. "
            "Only reference Ghanaian courts, judges, and precedents. "
            "Keep your response concise (300-500 words)."
        )

    elif task_type == "legal_research":
        return (
            f"{_PREAMBLE}{ctx_block}\n\n"
            f"TASK: Research this Ghana law topic: '{content}'.\n"
            "Provide an overview of the current legal position under Ghanaian law. "
            "Cite specific Ghanaian statutes (Acts of Parliament, LIs, CIs), "
            "key Ghanaian cases (Supreme Court, Court of Appeal, High Court), "
            "and relevant provisions of the 1992 Constitution of Ghana. "
            "Consult the bot's knowledge base for primary sources first. "
            "Keep your response concise (300-500 words)."
        )

    elif task_type == "legal_argument":
        return (
            f"{_PREAMBLE}{ctx_block}\n\n"
            f"TASK: Construct a legal argument under Ghana law for: '{content}'.\n"
            "Use Ghanaian legal principles, Ghanaian precedents, and Ghanaian statutes. "
            "Consider counterarguments based on Ghanaian jurisprudence. "
            "The argument must be valid in a Ghanaian court. "
            "Keep your response concise (300-500 words)."
        )

    elif task_type == "legal_flashcards":
        return (
            f"{_PREAMBLE}{ctx_block}\n\n"
            f"TASK: Generate flashcards for this Ghana law topic: '{content}'.\n"
            "Each flashcard must reference a specific Ghanaian legal concept, "
            "statute, case, or constitutional provision. "
            "Generate 5 flashcards. Format as bullet points. "
            "Every card must be exclusively about Ghana law."
        )

    else:
        return (
            f"{_PREAMBLE}{ctx_block}\n\n"
            f"TASK: Answer this Ghana law question: '{content}'.\n"
            "Provide a concise answer grounded in Ghanaian statutes, "
            "cases, and the 1992 Constitution. Keep under 500 words."
        )


def build_grounded_prompt(task_type: str, content: str, verdict: str,
                          docs: list[dict]) -> str:
    """Prompt that enforces the grounding tier.

    GROUNDED/PARTIAL: the model may cite ONLY the provided sources and must
    quote them briefly. PARTIAL additionally requires any point the sources do
    not directly support to be marked general/unverified. UNGROUNDED: the bot
    does not answer legal substance at all (the caller returns a refusal
    without calling the model); this string is a guard if it is ever called.

    Source text is untrusted: each chunk is neutralized (instruction-like
    spans stripped) and fenced, so neither a ``\"\"\"`` nor a forged fence
    sentinel inside a chunk can break out of the quoted source block.
    ``task_type`` is reserved for caller symmetry with ``build_prompt`` /
    ``budget_for`` (callers pass the type they budget with); it does not alter
    this prompt today.
    """
    if verdict == "UNGROUNDED":
        return (
            "Do NOT answer this legal question. No authoritative Ghana legal "
            "source was retrieved from the database. Reply only that you could "
            "not find it and suggest rephrasing or a covered topic."
        )

    from core.juris_kai.legal_context import MAX_CHUNK_LENGTH

    src_lines = []
    for i, d in enumerate(docs, 1):
        title = (d.get("title") or "Untitled").strip()
        citation = (d.get("citation") or "").strip()
        label = f"SOURCE {i}: {title}"
        if citation and citation != title:
            label += f" ({citation})"
        chunk = (d.get("chunk_content") or "")[:MAX_CHUNK_LENGTH]
        chunk = chunk.replace("<<<USER_CONTENT>>>", "[fence marker removed]")
        chunk = chunk.replace("<<<END_USER_CONTENT>>>", "[fence marker removed]")
        body = fence_user_content(neutralize(chunk))
        src_lines.append(f"{label}\n{body}")
    sources = "\n\n".join(src_lines)

    if verdict == "PARTIAL":
        strict = (
            "Cite ONLY the sources below. Do not mention any statute, case, or "
            "article that is not in them. The sources only partially cover this "
            "question: mark any point that the sources do not directly support "
            "as general or unverified, and say so plainly if they do not answer "
            "it. Quote the source text briefly to support each point."
        )
    else:
        strict = (
            "Cite ONLY the sources below. Do not mention any statute, case, or "
            "article that is not in them. If the sources do not answer the "
            "question, say so plainly. Quote the source text briefly to support "
            "each point."
        )

    return (
        f"{_JURISDICTION_GATE}\n\n{sources}\n\n"
        f"TASK: Answer this Ghana law question using ONLY the sources above: "
        f"'{content}'.\n{strict}"
    )


# Same security pattern as law_tutor - no imports of operational modules
# Only pure text processing and prompt construction
