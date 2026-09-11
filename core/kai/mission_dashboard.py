"""
Mission dashboard — summary, list, and detail views.
"""

from core.kai.mission_store import load_missions, get_mission


def get_mission_summary():
    """Return a summary of all missions.

    Returns:
        {"total": int, "by_status": {"proposed": N, "approved": N, "running": N, ...}}
    """
    missions = load_missions()
    by_status = {}
    for m in missions:
        s = m.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return {"total": len(missions), "by_status": by_status}


def get_mission_list(status=None, limit=50):
    """Return lightweight mission dicts, optionally filtered by status.

    Args:
        status: if provided, only missions with this status are returned
        limit: maximum number of missions to return (default 50)

    Returns:
        list of dicts with: id, objective, status, progress_pct, current_phase,
        risk, priority, updated, created, drift_score
    """
    missions = load_missions(status=status)
    lightweight = []
    for m in missions:
        lightweight.append({
            "id": m.get("id"),
            "objective": m.get("objective"),
            "status": m.get("status"),
            "progress_pct": m.get("progress_pct", 0),
            "current_phase": m.get("current_phase"),
            "risk": m.get("risk"),
            "priority": m.get("priority", 5),
            "updated": m.get("updated"),
            "created": m.get("created"),
            "drift_score": m.get("drift_score", 0),
        })
    return lightweight[:limit]


def get_mission_detail(mission_id):
    """Return the full mission dict for a given mission_id.

    Raises:
        ValueError: if the mission is not found
    """
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")
    return mission
