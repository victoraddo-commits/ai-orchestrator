from core.memory import load, save, update
from core.lifecycle import new_object, transition
from datetime import datetime, timedelta


# A resolved/closed incident is reused (and reopened) instead of a brand-new
# record being minted when the same (service, issue) recurs within this
# window. Root-caused 2026-09-20: the Telegram watchdog embedded a variable
# duration in the issue text, so every failed reminder cycle created a new
# incident (1,025 of them) -- but even with a stable issue, an incident that
# gets auto-resolved between failures would otherwise be re-minted forever.
RECURRENCE_WINDOW_SECONDS = 6 * 60 * 60

# Bounded retention: resolved/closed incidents older than this many days are
# moved (not deleted) to INCIDENT_ARCHIVE_FILE by prune_incidents().
INCIDENT_ARCHIVE_FILE = "incidents_archive.json"

# Legacy, non-dedupable issue prefix minted by the pre-2026-09-20 watchdog.
# One-shot cleanup resolves these in bulk; the fix means no new ones appear.
STALE_REMINDER_ISSUE_PREFIX = "Stale-approval/failure reminder could not be sent"

# Severities that describe a *fact*, not a problem. "New node discovered: X"
# / "peer came online" are observations; capturing them is useful for the
# audit trail, but leaving them open made the incident feed show 4 "open"
# network items that needed no action. An info incident is therefore recorded
# and immediately auto-resolved (never silently dropped).
AUTO_RESOLVE_SEVERITIES = ("info",)

# Issue fragment for the informational discovery/online events that were
# minted before auto-resolve existed (2026-09-20 backlog cleanup).
INFO_NETWORK_EVENT_PREFIXES = (
    "New node discovered:",
    "Tailscale peer",
)


def _is_informational_network_event(issue):
    issue = str(issue or "")
    return any(prefix in issue for prefix in INFO_NETWORK_EVENT_PREFIXES)


def _parse_timestamp(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _naive(value):
    if value is not None and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _incident_timestamp(incident):
    return _naive(_parse_timestamp(incident.get("updated") or incident.get("created")))


def _incident_created_at(incident):
    """Cooldown anchor: when the problem first appeared, not last touched."""
    return _naive(_parse_timestamp(incident.get("created")))


# Network alert types
NETWORK_PEER_OFFLINE = "NETWORK_PEER_OFFLINE"
NETWORK_PEER_ONLINE = "NETWORK_PEER_ONLINE"
NETWORK_ROUTE_ADVERTISED_BUT_NOT_ACCEPTED = "NETWORK_ROUTE_ADVERTISED_BUT_NOT_ACCEPTED"
NETWORK_SUBNET_UNREACHABLE = "NETWORK_SUBNET_UNREACHABLE"
NETWORK_ROUTE_ACCEPTED = "NETWORK_ROUTE_ACCEPTED"
NETWORK_NODE_DISCOVERED = "NETWORK_NODE_DISCOVERED"


ALLOWED_TRANSITIONS = {
    "open": ["investigating"],
    "investigating": ["approved", "closed"],
    "approved": ["executing", "closed"],
    "executing": ["verifying", "failed"],
    "verifying": ["resolved", "failed"],
    "resolved": ["closed"],
    "failed": ["investigating", "closed"],
    "closed": []
}


def load_incidents():

    incidents = load("incidents.json")

    if not isinstance(incidents, list):
        incidents = []

    return incidents


def save_incidents(incidents):

    save("incidents.json", incidents)


def find_open_duplicate(incidents, service, issue):

    for incident in reversed(incidents):

        if (
            incident.get("service") == service
            and incident.get("issue") == issue
            and incident.get("status") not in ("closed", "resolved")
        ):

            return incident

    return None


def find_recent_duplicate(incidents, service, issue, window_seconds, now=None):
    """Most recent incident for (service, issue) touched within the window,
    regardless of status. Lets a recurrence collapse onto an incident that was
    auto-resolved in between instead of minting a new record every cycle."""

    now = now or datetime.now()

    newest = None
    newest_at = None

    for incident in incidents:

        if incident.get("service") != service or incident.get("issue") != issue:
            continue

        touched_at = _incident_created_at(incident)

        if touched_at is None:
            continue

        if newest_at is None or touched_at > newest_at:
            newest = incident
            newest_at = touched_at

    if newest is None or newest_at is None:
        return None

    if (now - newest_at).total_seconds() > window_seconds:
        return None

    return newest


def _record_recurrence(incident, severity, detail, now_iso, reopened=False):

    incident["occurrences"] = incident.get("occurrences", 1) + 1
    incident["severity"] = severity
    incident["updated"] = now_iso

    if detail is not None:
        incident["detail"] = detail

    note = "recurrence"

    if reopened:
        incident["status"] = "open"
        incident.pop("resolved_at", None)
        incident.pop("resolution", None)
        note = "recurrence (reopened within cooldown)"

    incident.setdefault("history", []).append(
        {"status": incident.get("status", "open"), "timestamp": now_iso, "note": note}
    )

    return incident


def _mark_resolved(incident, note, now_iso):
    incident["status"] = "resolved"
    incident["updated"] = now_iso
    incident["resolved_at"] = now_iso
    incident["resolution"] = note
    incident.setdefault("history", []).append(
        {"status": "resolved", "timestamp": now_iso, "note": note}
    )
    return incident


def create_incident(service, issue, severity="info", detail=None, cooldown_seconds=0,
                    now=None):
    """Create or update an incident.

    The dedup key is (service, issue) -- callers MUST keep the issue string
    stable and pass any variable context (durations, raw errors, reminder
    bodies) via `detail`, otherwise every recurrence mints a new incident.

    With cooldown_seconds > 0, a resolved/closed incident for the same key
    touched within the window is reused and reopened rather than duplicated.

    ``severity="info"`` records an informational *observation* (node
    discovered, peer online) and auto-resolves it on the way in, so it never
    sits in the open feed needing an action it does not have.
    """

    now = now or datetime.now()
    now_iso = now.isoformat()

    incidents = load_incidents()

    existing = find_open_duplicate(incidents, service, issue)

    # Auto-resolved info incidents are, by definition, never open -- so
    # without a recurrence window every "New node discovered: X" scan would
    # mint a fresh resolved record. Reuse/update the recent one instead.
    if existing is None and severity in AUTO_RESOLVE_SEVERITIES:
        existing = find_recent_duplicate(
            incidents, service, issue, RECURRENCE_WINDOW_SECONDS, now=now
        )

    if existing is None and cooldown_seconds:
        existing = find_recent_duplicate(
            incidents, service, issue, cooldown_seconds, now=now
        )

    if existing:

        reopened = existing.get("status") in ("closed", "resolved")

        auto_resolve = severity in AUTO_RESOLVE_SEVERITIES

        _record_recurrence(
            existing, severity, detail, now_iso,
            reopened=reopened and not auto_resolve,
        )

        if auto_resolve:
            _mark_resolved(
                existing,
                "Auto-resolved: informational event (observed, no action needed)",
                now_iso,
            )
            if existing["history"]:
                existing["history"][-1]["note"] = "recurrence"

        save_incidents(incidents)

        return existing

    incident = new_object(
        "open",
        service=service,
        issue=issue,
        severity=severity,
        occurrences=1
    )

    # Honor the injected clock so cooldown math is deterministic/testable.
    incident["created"] = now_iso
    incident["updated"] = now_iso
    if incident.get("history"):
        incident["history"][-1]["timestamp"] = now_iso

    if detail is not None:
        incident["detail"] = detail

    incidents.append(incident)

    if severity in AUTO_RESOLVE_SEVERITIES:
        _mark_resolved(
            incident,
            "Auto-resolved: informational event (observed, no action needed)",
            now_iso,
        )

    save_incidents(incidents)

    return incident


def prune_incidents(resolved_older_than_days=30, now=None):
    """Bounded, non-destructive retention for incidents.json.

    - Resolved/closed incidents older than the cutoff are moved to
      incidents_archive.json (never deleted).
    - Duplicate OPEN incidents sharing a (service, issue) key are collapsed
      into the most recently touched one; the extras are archived and their
      occurrence counts folded into the keeper.

    Returns a summary dict. The archive file is the safety net, so this is
    safe to run repeatedly.
    """

    now = _naive(now or datetime.now())
    cutoff = now - timedelta(days=resolved_older_than_days)

    incidents = load_incidents()

    archive = load(INCIDENT_ARCHIVE_FILE)
    if not isinstance(archive, list):
        archive = []

    kept = []
    archived_resolved = 0

    for incident in incidents:

        if (
            incident.get("status") in ("resolved", "closed")
            and _incident_timestamp(incident) is not None
            and _incident_timestamp(incident) < cutoff
        ):
            archive.append(incident)
            archived_resolved += 1
        else:
            kept.append(incident)

    closed_now = [i for i in kept if i.get("status") in ("resolved", "closed")]
    open_now = [i for i in kept if i.get("status") not in ("resolved", "closed")]

    groups = {}

    for incident in open_now:
        groups.setdefault((incident.get("service"), incident.get("issue")), []).append(incident)

    collapsed_duplicates = 0
    final_open = []

    for group in groups.values():

        if len(group) == 1:
            final_open.append(group[0])
            continue

        group.sort(key=lambda i: _incident_timestamp(i) or datetime.min, reverse=True)
        keeper = group[0]
        keeper["occurrences"] = sum(i.get("occurrences", 1) or 1 for i in group)

        for extra in group[1:]:
            archive.append(extra)
            collapsed_duplicates += 1

        final_open.append(keeper)

    save_incidents(closed_now + final_open)
    save(INCIDENT_ARCHIVE_FILE, archive)

    return {
        "archived_resolved": archived_resolved,
        "collapsed_duplicates": collapsed_duplicates,
        "remaining": len(closed_now) + len(final_open),
        "archive_size": len(archive),
    }


def resolve_incidents(service, issue, note, now=None):
    """Resolve the open incident(s) for an exact (service, issue) key.

    Used to close out false-positive/obsolete incidents once the underlying
    cause is fixed and evidenced. Returns the number resolved. Runs under the
    memory lock so it is safe alongside the live scheduler.
    """

    now_iso = (now or datetime.now()).isoformat()
    result = {"count": 0}

    def mutate(incidents):

        if not isinstance(incidents, list):
            return incidents

        for incident in incidents:

            if (
                incident.get("status") not in ("resolved", "closed")
                and incident.get("service") == service
                and incident.get("issue") == issue
            ):
                _mark_resolved(incident, note, now_iso)
                result["count"] += 1

        return incidents

    update("incidents.json", mutate)

    return result["count"]


def resolve_informational_network_incidents(note, now=None):
    """One-shot backlog cleanup: resolve open, informational network
    discovery/online incidents minted before auto-resolve existed.

    Matches only service=="network", severity=="info", and the known
    discovery/online issue shapes -- a genuinely-failing network incident
    (offline peer, unreachable subnet) is left untouched. Returns the count.
    """

    now_iso = (now or datetime.now()).isoformat()
    result = {"count": 0}

    def mutate(incidents):

        if not isinstance(incidents, list):
            return incidents

        for incident in incidents:

            if (
                incident.get("status") not in ("resolved", "closed")
                and incident.get("service") == "network"
                and incident.get("severity") == "info"
                and _is_informational_network_event(incident.get("issue"))
            ):
                _mark_resolved(incident, note, now_iso)
                result["count"] += 1

        return incidents

    update("incidents.json", mutate)

    return result["count"]


def resolve_stale_reminder_incidents(note, now=None):
    """One-shot migration: resolve every open legacy reminder-failure incident.

    Matches only the pre-fix issue prefix so the new, dedupable stable issue
    (which should stay open while delivery is genuinely broken) is untouched.
    Returns the number resolved. Runs under the memory_manager lock so it is
    safe alongside the live scheduler.
    """

    now_iso = (now or datetime.now()).isoformat()
    result = {"count": 0}

    def mutate(incidents):

        if not isinstance(incidents, list):
            return incidents

        for incident in incidents:

            if (
                incident.get("status") not in ("resolved", "closed")
                and incident.get("service") == "telegram"
                and str(incident.get("issue", "")).startswith(STALE_REMINDER_ISSUE_PREFIX)
            ):
                _mark_resolved(incident, note, now_iso)
                result["count"] += 1

        return incidents

    update("incidents.json", mutate)

    return result["count"]


def transition_incident(incident_id, new_status, note=None):

    incidents = load_incidents()

    for incident in incidents:

        if str(incident.get("id")) == str(incident_id):

            transition(incident, new_status, ALLOWED_TRANSITIONS, note=note)

            save_incidents(incidents)

            return {"status": "success", "incident": incident}

    return {"status": "not_found"}


def mark_investigating(incident_id, note=None):
    return transition_incident(incident_id, "investigating", note=note)


def mark_approved(incident_id, note=None):
    return transition_incident(incident_id, "approved", note=note)


def mark_executing(incident_id, note=None):
    return transition_incident(incident_id, "executing", note=note)


def mark_verifying(incident_id, note=None):
    return transition_incident(incident_id, "verifying", note=note)


def mark_resolved(incident_id, note=None):
    return transition_incident(incident_id, "resolved", note=note)


def mark_failed(incident_id, note=None):
    return transition_incident(incident_id, "failed", note=note)


def mark_closed(incident_id, note=None):
    return transition_incident(incident_id, "closed", note=note)


def get_active_incidents():

    return [
        i for i in load_incidents()
        if i.get("status") not in ("closed", "resolved")
    ]


if __name__ == "__main__":

    print(
        create_incident(
            "pulse",
            "Container unhealthy",
            "warning"
        )
    )
