from datetime import datetime

from core.memory import load, save, update
from core.second_brain.stores.project.adapters.learning_adapter import sync_lesson_to_second_brain as _sb_sync


SUCCESS_STATUSES = {"COMPLETED"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ROLLED_BACK"}

# Threshold values lifted out of evaluate_template's recommendation ladder so
# the trusted/observe/avoid pattern is reusable for lessons, not just
# templates -- per 13F's "no new classification scheme" requirement.
RECOMMENDATION_TRUSTED_MIN = 80
RECOMMENDATION_OBSERVE_MIN = 50

# Strongest-to-weakest, used to cap an aggregate at the best claim any single
# lesson underneath it actually makes (see _cap_to_best_lesson_claim).
RECOMMENDATION_STRENGTH = ("avoid", "observe", "trusted")

# Categories tracked by the 13F lesson store. Plain strings (not an Enum) so
# they round-trip cleanly through JSON and are easy to filter on.
LESSON_CATEGORIES = (
    "preferred_architecture",
    "common_failure",
    "successful_solution",
    "avoided_approach",
)

LESSONS_FILE = "learning_lessons.json"


def _recommend_from_rate(success_rate, total):
    """Apply the existing trusted/observe/avoid pattern given a percentage.

    The thresholds match evaluate_template's ladder exactly -- anything
    above (or equal to) the trusted minimum is "trusted", anything above
    (or equal to) the observe minimum is "observe", everything else is
    "avoid", and a zero-attempt subject is "insufficient_history".
    """
    if total == 0:
        return "insufficient_history"
    if success_rate >= RECOMMENDATION_TRUSTED_MIN:
        return "trusted"
    if success_rate >= RECOMMENDATION_OBSERVE_MIN:
        return "observe"
    return "avoid"


def _derive_rollback_root_cause(build):
    """Dynamically extract a rollback root-cause string from a build.

    No fixed taxonomy -- sources are tried in priority order and the first
    one with usable detail wins. Designed for the case where
    ``rollback_deployment`` has just transitioned a build to ROLLED_BACK and
    the operator (or the next build cycle) wants to know *why* beyond a bare
    status flag.

    Precedence (highest to lowest):
      1. ``deployment.rollback.reason``  -- explicit reason captured by the
         remediation's rollback handler
      2. The "no rollback strategy registered" message when a build's
         remediation action has no rollback handler
      3. ``deployment.rollback.result.error``  -- strategy-execution error
      4. ``deployment.rollback.result.rolled_back_to``  -- confirmation of
         what the rollback restored
      5. ``build.failure_reason``  -- the build's pre-rollback failure
    """
    deployment = build.get("deployment") or {}

    rollback = deployment.get("rollback") or {}

    if rollback.get("reason"):
        return rollback["reason"]

    if rollback.get("attempted") and not rollback.get("available"):
        action = deployment.get("action") or "deployment"
        return f"No rollback strategy registered for {action!r}"

    result = rollback.get("result") or {}
    if isinstance(result, dict):
        if result.get("error"):
            return f"Rollback error: {result['error']}"
        if result.get("rolled_back_to"):
            return f"Rolled back to {result['rolled_back_to']}"

    failure_reason = build.get("failure_reason")
    if failure_reason:
        return f"Build failed: {failure_reason}"

    return "Rollback requested without a recorded cause"


def get_build_history():
    return load("build_history.json") or []


def record_build_outcome(build):
    history = get_build_history()

    security_report = build.get("security_report") or {}

    status = build.get("status")
    entry = {
        "build_id": build.get("id"),
        "name": build.get("name"),
        "template": build.get("template"),
        "status": status,
        "failure_reason": build.get("failure_reason"),
        "security_findings": security_report.get("total_findings"),
        "highest_severity": security_report.get("highest_severity"),
        "commits": len((build.get("generation_result") or {}).get("commits") or []),
        "timestamp": datetime.now().isoformat(),
    }

    if status == "ROLLED_BACK":
        # Record *why* the rollback happened, not just that it did. Derived
        # dynamically from whatever data the build has -- no fixed taxonomy.
        entry["rollback_root_cause"] = _derive_rollback_root_cause(build)

    history.append(entry)
    save("build_history.json", history)

    return entry


def get_template_success_rate(template):
    history = get_build_history()

    attempts = [entry for entry in history if entry.get("template") == template]

    if not attempts:
        return {"success_rate": 0, "attempts": 0}

    successes = len([e for e in attempts if e.get("status") in SUCCESS_STATUSES])

    return {
        "success_rate": round(successes / len(attempts) * 100, 2),
        "attempts": len(attempts),
    }


def evaluate_template(template):
    stats = get_template_success_rate(template)

    if stats["attempts"] == 0:
        stats["recommendation"] = "insufficient_history"
    else:
        stats["recommendation"] = _recommend_from_rate(stats["success_rate"], stats["attempts"])

    return stats


def summarize_templates():
    history = get_build_history()

    templates = {entry["template"] for entry in history if entry.get("template")}

    return {template: evaluate_template(template) for template in templates}


# --- 13F: lesson store (preferred architectures, common failures,
#             successful solutions, avoided approaches) ------------------

def get_lessons(category=None):
    lessons = load(LESSONS_FILE)

    if not isinstance(lessons, list):
        lessons = []

    if category is None:
        return lessons

    return [lesson for lesson in lessons if lesson.get("category") == category]


def record_lesson(category, subject, source, evidence=None, recommendation=None):
    if category not in LESSON_CATEGORIES:
        raise ValueError(f"Unknown lesson category: {category!r}")

    entry = {
        "category": category,
        "subject": subject,
        "source": source,
        "evidence": evidence or {},
        "recommendation": recommendation,
        "timestamp": datetime.now().isoformat(),
    }

    # 13T: the whole read-append-write goes through core.memory.update()'s
    # single flock critical section, not a load() then save() pair. Since 13R
    # dispatches builds through a ThreadPoolExecutor, two builds can record a
    # lesson at the same time -- with load-then-save both read the same list
    # and the second write silently drops the first one's lesson (measured at
    # 18 of 20 lost under a 20-thread append). Exactly the race 324aecf fixed
    # for the provider-rotation index, and the same fix.
    def mutate(lessons):
        lessons = lessons if isinstance(lessons, list) else []
        lessons.append(entry)
        return lessons

    update(LESSONS_FILE, mutate)

    # Mirror to Second Brain (additive — failures are silent)
    try:
        _sb_sync(
            action=subject,
            classification=recommendation or "observe",
            context={"category": category, "source": source, "evidence": evidence or {}},
        )
    except Exception:
        pass

    return entry


def _lesson_is_positive(lesson):
    """Whether a single lesson entry is a positive signal for its subject.

    Per-category interpretation (no new classification scheme; the existing
    trusted/observe/avoid ladder is what summarize_lessons applies):
      * preferred_architecture  -> positive iff the lesson's recommendation
        is one of trusted/observe (i.e. the build using this template was
        considered successful enough to recommend).
      * successful_solution     -> positive iff the lesson's recommendation
        is one of trusted/observe.
      * common_failure          -> each lesson IS a failure, so it is a
        negative signal by definition.
      * avoided_approach        -> each lesson IS a rejection (recorded as
        ``recommendation="avoid"``), so it is a negative signal by
        definition.
    """
    category = lesson.get("category")
    recommendation = lesson.get("recommendation") or ""

    if category in ("preferred_architecture", "successful_solution"):
        return recommendation in ("trusted", "observe")

    if category in ("common_failure", "avoided_approach"):
        return False

    return False


def _cap_to_best_lesson_claim(aggregate, lesson_recommendations):
    """Never report a stronger recommendation than any single lesson claims.

    13T: the positive-evidence ratio alone escalates a lone "observe" lesson
    to "trusted" -- 1 positive out of 1 is 100%. That silently overrides a
    deliberate judgement: 13T records minimax-m2.7's coding-agent result as
    "observe" precisely because 3 attempts is below
    core.ai.provider_evidence.MIN_SAMPLE_SIZE, and a summary that reads back
    "trusted" defeats the guard.

    One-directional by design -- this only ever lowers the aggregate, so a
    majority of failures still drags a subject down to "avoid" regardless of
    how confident its individual positive lessons were.
    """
    ranked = [r for r in lesson_recommendations if r in RECOMMENDATION_STRENGTH]

    if not ranked or aggregate not in RECOMMENDATION_STRENGTH:
        return aggregate

    best = max(ranked, key=RECOMMENDATION_STRENGTH.index)

    return min(aggregate, best, key=RECOMMENDATION_STRENGTH.index)


def summarize_lessons(category=None):
    """Aggregate lessons by subject and apply the trusted/observe/avoid pattern.

    For each subject within the requested category (or across all categories
    if ``category`` is None), compute the positive-evidence ratio and feed
    it through the same threshold table ``evaluate_template`` already uses,
    then cap it at the best claim any single lesson makes (13T, see
    ``_cap_to_best_lesson_claim``). The result is the per-subject
    recommendation, exactly the same shape callers already know from
    ``summarize_templates``.
    """
    lessons = get_lessons(category)

    by_subject = {}
    for lesson in lessons:
        subject = lesson.get("subject")
        if not subject:
            continue
        bucket = by_subject.setdefault(
            subject,
            {"category": lesson.get("category"), "attempts": 0, "positive": 0, "claims": []},
        )
        bucket["attempts"] += 1
        bucket["claims"].append(lesson.get("recommendation"))
        if _lesson_is_positive(lesson):
            bucket["positive"] += 1

    summary = {}
    for subject, bucket in by_subject.items():
        total = bucket["attempts"]
        positives = bucket["positive"]
        rate = round(positives / total * 100, 2) if total else 0
        summary[subject] = {
            "category": bucket["category"],
            "attempts": total,
            "positive": positives,
            "success_rate": rate,
            "recommendation": _cap_to_best_lesson_claim(
                _recommend_from_rate(rate, total), bucket["claims"]
            ),
        }

    return summary


def ingest_rejected_proposals():
    """Read 13C's Improvement Proposal store and record rejected proposals
    as ``avoided_approach`` lessons.

    The transition note (if any) recorded when the proposal was rejected
    becomes the lesson's evidence so future cycles can see not just *that*
    an approach was avoided but *why*.
    """
    try:
        from core.kai.planner import load_proposals
    except ImportError:
        return {"ingested": 0, "skipped": "proposal_store_unavailable"}

    proposals = load_proposals()
    ingested = 0
    total_rejected = 0

    for proposal in proposals:
        if proposal.get("status") != "rejected":
            continue

        total_rejected += 1

        # 13C's lifecycle.transition() appends {"status": ..., "note": ...}
        # entries to proposal["history"]; the most recent rejection note is
        # the last entry whose status is "rejected".
        rejection_note = None
        for entry in reversed(proposal.get("history") or []):
            if entry.get("status") == "rejected":
                rejection_note = entry.get("note")
                break

        record_lesson(
            category="avoided_approach",
            subject=proposal.get("title") or proposal.get("id"),
            source="improvement_proposal_store",
            evidence={
                "proposal_id": proposal.get("id"),
                "description": proposal.get("description"),
                "suggested_action": proposal.get("suggested_action"),
                "rationale": proposal.get("rationale"),
                "rejection_note": rejection_note,
            },
            recommendation="avoid",
        )
        ingested += 1

    return {"ingested": ingested, "total_rejected": total_rejected}
