from datetime import datetime

from core.memory import load, save


SUCCESS_STATUSES = {"COMPLETED"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ROLLED_BACK"}


def get_build_history():
    return load("build_history.json") or []


def record_build_outcome(build):
    history = get_build_history()

    security_report = build.get("security_report") or {}

    entry = {
        "build_id": build.get("id"),
        "name": build.get("name"),
        "template": build.get("template"),
        "status": build.get("status"),
        "failure_reason": build.get("failure_reason"),
        "security_findings": security_report.get("total_findings"),
        "highest_severity": security_report.get("highest_severity"),
        "commits": len((build.get("generation_result") or {}).get("commits") or []),
        "timestamp": datetime.now().isoformat(),
    }

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
    elif stats["success_rate"] >= 80:
        stats["recommendation"] = "trusted"
    elif stats["success_rate"] >= 50:
        stats["recommendation"] = "observe"
    else:
        stats["recommendation"] = "avoid"

    return stats


def summarize_templates():
    history = get_build_history()

    templates = {entry["template"] for entry in history if entry.get("template")}

    return {template: evaluate_template(template) for template in templates}
