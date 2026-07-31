"""Command handlers for the Law Tutor bot. Each builds a prompt (system
prompt + recent context + command-specific instruction + the student's
message) and calls core.ai.ai_router.delegate() with the law_* task_type
that best fits the task -- see LAW_TUTOR_ROLE_PROVIDERS in ai_router.py for
the reasoning behind each assignment.

Deliberately thin: no build/approval/deploy imports anywhere in this module
or anything it imports, by design -- this bot only ever reads and replies
with text, never executes anything.
"""

from core.ai.ai_router import delegate, AllProvidersFailed
from core.law_tutor.prompt import LAW_TUTOR_SYSTEM_PROMPT
from core.law_tutor.session import (
    append_exchange, mark_needs_practice, recent_context_text, progress_summary_text,
)


def _ask(task_type, instruction, chat_id, command, topic=None):
    context = recent_context_text(chat_id)
    prompt = (
        f"{LAW_TUTOR_SYSTEM_PROMPT}\n\n"
        f"RECENT CONVERSATION WITH THIS STUDENT:\n{context}\n\n"
        f"{instruction}"
    )
    try:
        result = delegate(prompt, task_type=task_type, capability="text_task")
        reply = result.get("response", "").strip() or "(No response generated.)"
    except AllProvidersFailed:
        reply = "Sorry -- every AI provider is unavailable right now. Please try again shortly."

    append_exchange(chat_id, command, instruction, reply, topic=topic)
    return reply


def cmd_learn(chat_id, topic_text):
    if not topic_text:
        return "Usage: /learn <topic> -- e.g. /learn negligence"
    instruction = (
        f"The student wants to learn about: {topic_text}\n"
        "Teach this topic following your teaching style: Socratic questioning where it "
        "helps, plain-English meaning, legal definition, elements required, examples, "
        "exceptions, and common mistakes. End by asking if she'd like you to test her "
        "understanding."
    )
    return _ask("law_teaching", instruction, chat_id, "learn", topic=topic_text)


def cmd_case(chat_id, case_text):
    if not case_text:
        return "Usage: /case <case name or citation> -- e.g. /case Donoghue v Stevenson"
    instruction = (
        f"Analyze this case: {case_text}\n"
        "Use the Case Brief Format exactly: Case / Court / Date / Jurisdiction, then Facts, "
        "Issue, Rule, Analysis, Decision, Precedent, and Exam Application. If you are not "
        "confident of the specific citation details (court, date, exact holding), say so "
        "plainly ('I need to verify this authority') rather than inventing them, and ask "
        "which jurisdiction/course this is for if that would change the analysis."
    )
    return _ask("law_case_analysis", instruction, chat_id, "case", topic=case_text)


def cmd_research(chat_id, question_text):
    if not question_text:
        return "Usage: /research <question> -- e.g. /research how has the standard for duty of care evolved"
    instruction = (
        f"Research this legal question: {question_text}\n"
        "Do not just summarize -- analyze competing interpretations, historical "
        "development, judicial trends, and conflicting authorities. Identify any gaps in "
        "the reasoning you present and suggest where a stronger argument could be made. "
        "Cite sources (case/statute/commentary, with authority type) wherever you make a "
        "legal claim, and say 'I need to verify this authority' rather than inventing a "
        "citation you're not confident of."
    )
    return _ask("law_case_analysis", instruction, chat_id, "research", topic=question_text)


def cmd_argument(chat_id, issue_text):
    if not issue_text:
        return "Usage: /argument <legal issue> -- e.g. /argument should X be liable for Y"
    instruction = (
        f"Develop a full legal argument on: {issue_text}\n"
        "Structure it exactly as: Issue (the legal question), Applicable Law (statutes and "
        "precedents that apply), Arguments For (the strongest case for one side), Arguments "
        "Against (the strongest case for the other side), Evaluation (compare both), and "
        "Conclusion (the most legally supported outcome)."
    )
    return _ask("law_case_analysis", instruction, chat_id, "argument", topic=issue_text)


def cmd_quiz(chat_id, topic_text):
    subject = topic_text or "a mix of topics we've recently covered"
    instruction = (
        f"Quiz the student on: {subject}\n"
        "Ask 3-5 short questions one at a time would be ideal, but since this is a single "
        "reply, give the first question only and tell her you're waiting for her answer "
        "before continuing -- don't dump all the answers at once."
    )
    return _ask("law_exam", instruction, chat_id, "quiz", topic=topic_text)


def cmd_flashcards(chat_id, topic_text):
    if not topic_text:
        return "Usage: /flashcards <topic> -- e.g. /flashcards offer and acceptance"
    instruction = (
        f"Create 8-10 flashcards for: {topic_text}\n"
        "Format each as 'Q: ...' then 'A: ...' on the next line, concise enough to "
        "actually memorize, covering definitions, elements, and key case names where relevant."
    )
    return _ask("law_flashcards", instruction, chat_id, "flashcards", topic=topic_text)


def cmd_exam(chat_id, topic_text):
    if not topic_text:
        return "Usage: /exam <topic> -- e.g. /exam duty of care"
    instruction = (
        f"Create one realistic mock exam question on: {topic_text}\n"
        "Make it essay-style with a fact pattern, appropriate for IRAC analysis. Do not "
        "answer it yet -- just present the question, and mention she can reply with her "
        "attempt or use /irac to work through it structured."
    )
    return _ask("law_exam", instruction, chat_id, "exam", topic=topic_text)


def cmd_irac(chat_id, fact_pattern):
    if not fact_pattern:
        return "Usage: /irac <fact pattern or question> -- paste the scenario you want structured."
    instruction = (
        f"Help structure an IRAC answer for this: {fact_pattern}\n"
        "Walk through Issue, Rule, Application, Conclusion. Where the rule or outcome is "
        "genuinely unsettled or jurisdiction-dependent, say so rather than asserting false "
        "certainty."
    )
    return _ask("law_exam", instruction, chat_id, "irac", topic=fact_pattern)


def cmd_studyplan(chat_id, extra_text):
    progress = progress_summary_text(chat_id)
    instruction = (
        f"Help build a revision/study plan.\n{('Additional context from the student: ' + extra_text) if extra_text else ''}\n\n"
        f"Her tracked progress so far:\n{progress}\n\n"
        "Propose a realistic study schedule, prioritizing topics flagged as needing more "
        "practice, and ask what her exam date and available study hours per week are if "
        "that isn't already clear from context."
    )
    return _ask("law_teaching", instruction, chat_id, "studyplan")


def cmd_debate(chat_id, topic_text):
    if not topic_text:
        return "Usage: /debate <topic> -- e.g. /debate should strict liability apply to X"
    instruction = (
        f"Argue both sides of this legal issue: {topic_text}\n"
        "Present the strongest genuine argument for each side, note which side current "
        "law/precedent tends to favor if that's clear, and end by asking her which side "
        "she finds more persuasive and why."
    )
    return _ask("law_case_analysis", instruction, chat_id, "debate", topic=topic_text)


def cmd_progress(chat_id):
    return progress_summary_text(chat_id)


def cmd_needs_practice(chat_id, topic_text):
    if not topic_text:
        return "Usage: /needspractice <topic> -- flags a topic so future study plans prioritize it."
    mark_needs_practice(chat_id, topic_text)
    return f"Noted -- \"{topic_text}\" flagged for more practice."
