import csv
import io
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import HTTPException, Request
from core.memory import load
from core.approval import load_requests
from core.incident_manager import load_incidents
from core.decision_engine import load_decisions
from core.build_manager import load_builds


def extract_client_ip(request: Request) -> str:
    header = request.headers.get("x-forwarded-for")
    if header:
        return header.split(",")[0].strip()
    header = request.headers.get("x-real-ip")
    if header:
        return header.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def normalize_timestamp(ts: Any) -> str:
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    s = str(ts)
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").isoformat()
    except ValueError:
        pass
    return datetime.now(timezone.utc).isoformat()


def _safe_status(status: Any) -> str:
    return str(status).lower() if status else "unknown"


def map_build_to_audit_entry(build: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": build.get("id"),
        "timestamp": normalize_timestamp(build.get("created")),
        "action": "build_" + _safe_status(build.get("status")),
        "user": build.get("operator", "system"),
        "source_ip": build.get("source_ip", "127.0.0.1"),
        "project": build.get("name") or "",
        "status": build.get("status"),
        "source_store": "build_history",
        "details": {
            "name": build.get("name"),
            "description": build.get("description"),
            "template": build.get("template"),
            "trace_id": build.get("trace_id"),
        },
    }


def _approval_audit_entries(approval: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    history: list = approval.get("history") or []
    for h in history:
        status = h.get("status", "unknown")
        action = "approval_" + status.replace("_", "_")
        entries.append({
            "id": approval.get("id"),
            "timestamp": normalize_timestamp(h.get("timestamp")),
            "action": action,
            "user": approval.get("approved_by") or "system",
            "source_ip": approval.get("source_ip", "127.0.0.1"),
            "project": approval.get("phase_id", ""),
            "status": status,
            "source_store": "approval_queue",
            "details": {
                "approval_type": approval.get("approval_type"),
                "build_id": approval.get("build_id"),
                "title": approval.get("title"),
                "description": approval.get("description"),
            },
        })
    return entries


def map_decision_to_audit_entry(decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": decision.get("id"),
        "timestamp": normalize_timestamp(decision.get("created")),
        "action": "decision_" + _safe_status(decision.get("status")),
        "user": decision.get("operator", "system"),
        "source_ip": decision.get("source_ip", "127.0.0.1"),
        "project": decision.get("incident_id", ""),
        "status": decision.get("status"),
        "source_store": "decision_history",
        "details": {
            "incident_id": decision.get("incident_id"),
            "problem": decision.get("problem"),
            "recommended_action": decision.get("recommended_action"),
            "risk_level": decision.get("risk_level"),
            "requires_approval": decision.get("requires_approval"),
        },
    }


def map_incident_to_audit_entry(incident: Dict[str, Any]) -> Dict[str, Any]:
    severity = incident.get("severity", "info")
    action = "incident_" + str(severity).lower()
    return {
        "id": incident.get("id"),
        "timestamp": normalize_timestamp(incident.get("timestamp")),
        "action": action,
        "user": incident.get("operator", "system"),
        "source_ip": incident.get("source_ip", "127.0.0.1"),
        "project": incident.get("service", ""),
        "status": severity,
        "source_store": "incidents",
        "details": {
            "service": incident.get("service"),
            "issue": incident.get("issue"),
            "severity": severity,
            "occurrences": incident.get("occurrences"),
        },
    }


def _load_source(loader, label: str) -> List[Dict[str, Any]]:
    try:
        data = loader()
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def get_audit_entries(
    user: Optional[str] = None,
    action: Optional[str] = None,
    project: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    builds = _load_source(load_builds, "builds")
    approvals = _load_source(load_requests, "approvals")
    decisions = _load_source(load_decisions, "decisions")
    incidents = _load_source(load_incidents, "incidents")

    audit_entries: List[Dict[str, Any]] = []

    for build in builds:
        try:
            audit_entries.append(map_build_to_audit_entry(build))
        except Exception:
            continue

    for approval in approvals:
        try:
            audit_entries.extend(_approval_audit_entries(approval))
        except Exception:
            continue

    for decision in decisions:
        try:
            audit_entries.append(map_decision_to_audit_entry(decision))
        except Exception:
            continue

    for incident in incidents:
        try:
            audit_entries.append(map_incident_to_audit_entry(incident))
        except Exception:
            continue

    filtered: List[Dict[str, Any]] = []
    for entry in audit_entries:
        if user and str(user).lower() != str(entry["user"]).lower():
            continue
        if action and str(action).lower() != str(entry["action"]).lower():
            continue
        if project and str(project).lower() != str(entry["project"]).lower():
            continue
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                entry_dt = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                if entry_dt < start_dt:
                    continue
            except (ValueError, TypeError):
                continue
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                entry_dt = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                if entry_dt > end_dt:
                    continue
            except (ValueError, TypeError):
                continue
        filtered.append(entry)

    filtered.sort(key=lambda x: x["timestamp"], reverse=True)
    return filtered


def format_audit_entries_as_csv(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return ""
    fields = ["id", "timestamp", "action", "user", "source_ip", "project", "status", "source_store"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for entry in entries:
        writer.writerow({k: entry.get(k, "") for k in fields})
    return output.getvalue()


def format_audit_entries_as_json(
    entries: List[Dict[str, Any]], metadata: Dict[str, Any]
) -> Dict[str, Any]:
    return {"entries": entries, "metadata": metadata}
