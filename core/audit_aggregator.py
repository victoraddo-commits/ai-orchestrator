import csv
import io
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from core.memory import load
from core.approval import load_requests
from core.incident_manager import load_incidents
from core.decision_engine import load_decisions
from core.build_manager import load_builds

def normalize_timestamp(timestamp_str: str) -> str:
    """Normalize timestamp to ISO format if needed."""
    if not timestamp_str:
        return datetime.now().isoformat()
    
    # If it's already in ISO format, just return it
    try:
        datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return timestamp_str
    except ValueError:
        # Try to convert if it's in a different format
        try:
            dt = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%f%z")
            return dt.isoformat()
        except ValueError:
            # Fallback to current time if we can't parse
            return datetime.now().isoformat()

def map_build_to_audit_entry(build: Dict[str, Any]) -> Dict[str, Any]:
    """Map a build record to an audit entry."""
    return {
        "id": build.get("id"),
        "timestamp": normalize_timestamp(build.get("created_at")),
        "action": "build_triggered",
        "user": build.get("operator", "system"),
        "source_ip": build.get("source_ip", "127.0.0.1"),
        "project": build.get("name"),
        "status": build.get("status"),
        "source_store": "build_history",
        "details": {
            "name": build.get("name"),
            "description": build.get("description"),
            "template": build.get("template"),
            "status": build.get("status")
        }
    }

def map_approval_to_audit_entry(approval: Dict[str, Any]) -> Dict[str, Any]:
    """Map an approval record to an audit entry."""
    action_type = "approval_" + approval.get("approval_type", "unknown").replace("_", "_")
    
    return {
        "id": approval.get("id"),
        "timestamp": normalize_timestamp(approval.get("created_at")),
        "action": action_type,
        "user": approval.get("approved_by") or approval.get("rejected_by", "system"),
        "source_ip": approval.get("source_ip", "127.0.0.1"),
        "project": approval.get("project", ""),
        "status": approval.get("status"),
        "source_store": "approval_queue",
        "details": {
            "approval_type": approval.get("approval_type"),
            "build_id": approval.get("build_id"),
            "title": approval.get("title"),
            "description": approval.get("description"),
            "status": approval.get("status")
        }
    }

def map_decision_to_audit_entry(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Map a decision record to an audit entry."""
    return {
        "id": decision.get("id"),
        "timestamp": normalize_timestamp(decision.get("created_at")),
        "action": "decision_made",
        "user": decision.get("operator", "system"),
        "source_ip": decision.get("source_ip", "127.0.0.1"),
        "project": decision.get("project", ""),
        "status": decision.get("status"),
        "source_store": "decision_history",
        "details": {
            "incident_id": decision.get("incident_id"),
            "problem": decision.get("problem"),
            "recommended_action": decision.get("recommended_action"),
            "risk_level": decision.get("risk_level"),
            "requires_approval": decision.get("requires_approval")
        }
    }

def map_incident_to_audit_entry(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Map an incident record to an audit entry.""" 
    action_type = "incident_" + incident.get("status", "reported").replace("_", "_")
    
    return {
        "id": incident.get("id"),
        "timestamp": normalize_timestamp(incident.get("created_at")),
        "action": action_type,
        "user": incident.get("operator", "system"),
        "source_ip": incident.get("source_ip", "127.0.0.1"),
        "project": incident.get("service", ""),
        "status": incident.get("status"),
        "source_store": "incidents",
        "details": {
            "service": incident.get("service"),
            "issue": incident.get("issue"),
            "severity": incident.get("severity"),
            "occurrences": incident.get("occurrences")
        }
    }

def get_audit_entries(
    user_filter: Optional[str] = None,
    action_filter: Optional[str] = None,
    project_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get all audit entries from various sources and merge them chronologically."""
    
    # Load data from all sources
    builds = []
    try:
        builds = load_builds() or []
    except Exception:
        # Log error if needed, but continue with empty list
        builds = []
    
    approvals = []
    try:
        approvals = load_requests() or []
    except Exception:
        # Log error if needed, but continue with empty list
        approvals = []
    
    decisions = []
    try:
        decisions = load_decisions() or []
    except Exception:
        # Log error if needed, but continue with empty list
        decisions = []
    
    incidents = []
    try:
        incidents = load_incidents() or []
    except Exception:
        # Log error if needed, but continue with empty list
        incidents = []
    
    # Convert all records to audit entries
    audit_entries = []
    
    # Process builds
    for build in builds:
        try:
            entry = map_build_to_audit_entry(build)
            audit_entries.append(entry)
        except Exception:
            # Skip bad entries but continue processing
            continue
        
    # Process approvals
    for approval in approvals:
        try:
            entry = map_approval_to_audit_entry(approval)
            audit_entries.append(entry)
        except Exception:
            # Skip bad entries but continue processing
            continue
        
    # Process decisions
    for decision in decisions:
        try:
            entry = map_decision_to_audit_entry(decision)
            audit_entries.append(entry)
        except Exception:
            # Skip bad entries but continue processing
            continue
        
    # Process incidents
    for incident in incidents:
        try:
            entry = map_incident_to_audit_entry(incident)
            audit_entries.append(entry)
        except Exception:
            # Skip bad entries but continue processing
            continue
        
    # Apply filters
    filtered_entries = []
    for entry in audit_entries:
        # User filter
        if user_filter and user_filter != entry["user"]:
            continue
            
        # Action filter  
        if action_filter and action_filter != entry["action"]:
            continue
            
        # Project filter
        if project_filter and project_filter != entry["project"]:
            continue
            
        # Date range filter
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                entry_dt = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                if entry_dt < start_dt:
                    continue
            except ValueError:
                # Skip invalid dates
                continue
                
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                entry_dt = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                if entry_dt > end_dt:
                    continue
            except ValueError:
                # Skip invalid dates
                continue
                
        filtered_entries.append(entry)
        
    # Sort by timestamp descending (most recent first)
    filtered_entries.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return filtered_entries

def format_audit_entries_as_csv(entries: List[Dict[str, Any]]) -> str:
    """Format audit entries as CSV."""
    if not entries:
        return ""
        
    # Define CSV columns
    fields = ["id", "timestamp", "action", "user", "source_ip", "project", "status", "source_store"]
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    
    for entry in entries:
        row = {
            "id": entry["id"],
            "timestamp": entry["timestamp"],
            "action": entry["action"],
            "user": entry["user"],
            "source_ip": entry["source_ip"],
            "project": entry["project"],
            "status": entry["status"],
            "source_store": entry["source_store"]
        }
        writer.writerow(row)
        
    return output.getvalue()

def format_audit_entries_as_json(entries: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Format audit entries as JSON with metadata."""
    return {
        "entries": entries,
        "metadata": metadata
    }