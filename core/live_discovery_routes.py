"""KAI Capability Registry routes (live-estate discovery + graph).

Read-only exposure of core/live_discovery + live_graph so the Command
Center can show first-class ecosystem capabilities (directive §21).
"""
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from core.live_discovery import scan_live_estate
from core.live_graph import build_graph
from core.live_dupes import find_dupes, find_orphans

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kai/capabilities", tags=["kai", "capabilities"])


@router.get("")
def list_capabilities():
    caps = scan_live_estate()
    return {"ok": True, "data": caps, "meta": {"total": len(caps)}}


@router.get("/graph")
def capability_graph():
    g = build_graph()
    return {
        "ok": True,
        "data": {
            "nodes": g["nodes"],
            "edges": [list(e) for e in g["edges"]],
            "in_degree": g["in_degree"],
        },
        "meta": {"nodes": len(g["nodes"]), "edges": len(g["edges"])},
    }


@router.get("/health")
def registry_health():
    caps = scan_live_estate()
    g = build_graph()
    return {
        "ok": True,
        "data": {
            "capabilities": len(caps),
            "graph_nodes": len(g["nodes"]),
            "graph_edges": len(g["edges"]),
            "duplicate_clusters": len(find_dupes()),
            "orphans": len(find_orphans()),
        },
    }


@router.get("/ui")
def capabilities_html():
    """Minimal read-only HTML table of /kai/capabilities data (plain HTML,
    inline CSS, no external JS)."""
    caps = scan_live_estate()
    rows = ""
    for c in caps:
        cid = c.get("capability_id", "")
        mod = c.get("module", "")
        purpose = c.get("purpose", "") or ""
        status = c.get("status", "")
        rows += (
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_html_escape(cid), _html_escape(mod),
               _html_escape(purpose), _html_escape(status))
        )
    html = (
        "<!DOCTYPE html><html><head><title>KAI Capabilities</title>"
        "<style>body{font-family:sans-serif;margin:16px}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #999;padding:4px 8px;text-align:left}"
        "th{background:#eee}</style></head><body>"
        "<h1>KAI Capabilities</h1>"
        "<table><thead><tr><th>capability_id</th><th>module</th>"
        "<th>purpose</th><th>status</th></tr></thead><tbody>"
        + rows + "</tbody></table></body></html>"
    )
    return HTMLResponse(content=html)


def _html_escape(v):
    return (str(v).replace("&", "&").replace("<", "<")
            .replace(">", ">"))
