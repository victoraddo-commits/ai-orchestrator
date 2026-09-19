"""Legal group management + group document audit.

Wraps :class:`core.juris_kai.accounts.AccountManager` group tables with a small
facade used by the Command Center API and the Telegram bot:

  * create / rename / archive a group;
  * join by invite code, leave;
  * add / remove / re-role members;
  * run the **group document audit** — a deterministic verification pass over
    the documents uploaded by the group's members, cross-checked against the
    Legal Brain corpus, persisted as a report.

Security: NO imports of ``core.build_manager``, ``core.approval`` or
``core.deployment_manager``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("juris_kai.groups")

VALID_ROLES = ("owner", "admin", "member")


def _mgr():
    from core.juris_kai.accounts import get_account_manager
    return get_account_manager()


# ── group lifecycle ───────────────────────────────────────────────────────

def create_group(name: str, account_id: str, *, kind: str = "user",
                 description: str = "", actor: str = "") -> Dict[str, Any]:
    return _mgr().create_group(name, created_by=account_id,
                               created_by_kind=kind, description=description,
                               actor=actor or account_id)


def find_group(group_id_or_code: str) -> Optional[Dict[str, Any]]:
    return _mgr().get_group(group_id_or_code)


def join_group(invite_code: str, account_id: str, *,
               actor: str = "") -> Dict[str, Any]:
    group = _mgr().get_group(invite_code)
    if not group:
        return {"success": False, "error": "invalid invite code"}
    if group.get("status") != "active":
        return {"success": False, "error": "group is archived"}
    result = _mgr().add_member(group["group_id"], account_id, role="member",
                               actor=actor or account_id)
    result["group"] = group
    return result


def leave_group(group_id: str, account_id: str, *,
                actor: str = "") -> Dict[str, Any]:
    return _mgr().remove_member(group_id, account_id, actor=actor or account_id)


def groups_for_account(account_id: str) -> List[Dict[str, Any]]:
    return _mgr().list_groups(account_id=account_id)


def member_account_ids(group_id: str) -> List[str]:
    return [m["account_id"] for m in _mgr().list_members(group_id)]


# ── group document audit ──────────────────────────────────────────────────

def _default_searcher(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Search the Legal Brain corpus; empty on any failure (never raises)."""
    try:
        from core import legal_brain_client as lb
        return lb.search(query, limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("group audit: legal brain search failed (%s)",
                       type(exc).__name__)
        return []


def _member_documents(account_ids: List[str]) -> List[Dict[str, Any]]:
    """Collect document analyses uploaded by any of the group's members."""
    if not account_ids:
        return []
    mgr = _mgr()
    placeholders = ", ".join(["?"] * len(account_ids))
    rows = mgr.db.execute(
        f"""SELECT analysis_id, account_id, document_name, page_count,
                   cost_ghs, status, created_at
            FROM juris_document_analyses
            WHERE account_id IN ({placeholders})
            ORDER BY created_at DESC""",
        list(account_ids),
    ).fetchall()
    return [dict(r) for r in rows]


def _verdict(doc: Dict[str, Any], matches: List[Dict[str, Any]]) -> str:
    """verified when a corpus hit shares meaningful title tokens; else flagged."""
    name = (doc.get("document_name") or "").strip().lower()
    if not name:
        return "flagged"
    tokens = {t for t in name.replace("-", " ").split() if len(t) > 3}
    for hit in matches:
        title = str(hit.get("title") or "").lower()
        if not title:
            continue
        if name in title or title in name:
            return "verified"
        hit_tokens = {t for t in title.replace("-", " ").split() if len(t) > 3}
        if tokens and len(tokens & hit_tokens) >= max(1, len(tokens) // 2):
            return "verified"
    return "flagged"


def audit_group_documents(
    group_id: str,
    *,
    requested_by: str = "",
    searcher: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Run and store a document audit for a group.

    For every document uploaded by the group's members: search the Legal Brain
    corpus for a matching source and classify it ``verified`` (a corpus source
    matches) or ``flagged`` (no authoritative source found). The report is
    persisted to ``legal_group_reports`` and returned.
    """
    mgr = _mgr()
    group = mgr.get_group(group_id)
    if not group:
        return {"success": False, "error": "group not found"}
    gid = group["group_id"]
    members = mgr.list_members(gid)
    account_ids = [m["account_id"] for m in members]
    documents = _member_documents(account_ids)
    search = searcher or _default_searcher

    findings: List[Dict[str, Any]] = []
    verified = 0
    flagged = 0
    for doc in documents:
        matches = search(doc.get("document_name") or "", 3) or []
        verdict = _verdict(doc, matches)
        if verdict == "verified":
            verified += 1
        else:
            flagged += 1
        findings.append({
            "analysis_id": doc.get("analysis_id"),
            "account_id": doc.get("account_id"),
            "document_name": doc.get("document_name"),
            "page_count": doc.get("page_count"),
            "status": doc.get("status"),
            "verdict": verdict,
            "sources": [h.get("title") for h in matches][:3],
        })

    summary = (
        f"{len(documents)} document(s) across {len(account_ids)} member(s): "
        f"{verified} verified against the Legal Brain corpus, "
        f"{flagged} flagged for lack of an authoritative source."
    )
    report = mgr.save_group_report(
        gid, requested_by=requested_by, documents=documents,
        findings={"group": group.get("name"), "group_id": gid,
                  "members": len(account_ids), "items": findings},
        summary=summary, verified_count=verified, flagged_count=flagged,
    )
    mgr.log_group_audit(gid, requested_by, "document_audit",
                        {"report_id": report["report_id"],
                         "documents": len(documents),
                         "verified": verified, "flagged": flagged})
    return {
        "success": True,
        "group": group,
        "report": report,
        "documents": documents,
        "findings": findings,
        "member_count": len(account_ids),
        "verified_count": verified,
        "flagged_count": flagged,
    }
