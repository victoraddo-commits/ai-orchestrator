from __future__ import annotations

import html

from .models import Record

_CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#0d1117;color:#e6edf3}
header{padding:16px 24px;background:#161b22;border-bottom:1px solid #30363d}
h1{margin:0;font-size:18px}
main{max-width:1000px;margin:0 auto;padding:16px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:8px;border-bottom:1px solid #21262d;text-align:left}
.up{color:#3fb950}.down{color:#f85149}.unknown{color:#8b949e}
a{color:#58a6ff;text-decoration:none}
"""


def render_index(records: list[Record]) -> str:
    rows = []
    for r in records:
        url = r.internal_url or r.target_url
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.display_name or r.name)}</td>"
            f"<td>{html.escape(r.category)}</td>"
            f"<td class=\"{html.escape(r.health_status)}\">{html.escape(r.health_status)}</td>"
            f"<td>{html.escape(r.host or '')}:{r.port}</td>"
            f"<td><a href=\"{html.escape(url)}\">{html.escape(url)}</a></td>"
            "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Kai Service Directory</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<header><h1>Kai Service Directory</h1></header><main>"
        "<table><thead><tr><th>Service</th><th>Category</th><th>Status</th>"
        "<th>Host</th><th>URL</th></tr></thead><tbody>"
        + "".join(rows) +
        "</tbody></table></main></body></html>"
    )
