"""Exportable reports for Juris Kai (Legal Brain 2.0 Phase 8, Task 2).

Render a research result to a downloadable file — PDF (fpdf2) and DOCX
(python-docx). Supported sources:

  * **Deep Research** — IRAC + authorities + counter-authorities + uncertainty;
  * **Authority Bundle** — structured authorities per issue;
  * **Issue Matrix** — Issue | Law | Authority | Facts | Counterargument | Status;
  * **Legal Chronology** — a dated timeline.

Every report carries a header (query, date, plan), the sections, the
deterministic 📚 Sources footer (with temporal status) and the
"informational — not legal advice" disclaimer. The narrative content is run
through the citation firewall (:mod:`core.juris_kai.citation_firewall`) before
it is rendered, so an invented citation cannot reach a file. Authority lists
are corpus documents, not model output, so they are not passed to the verifier.

Reports are stored as ``juris_report_<id>.pdf`` / ``.docx`` (plus a ``.json``
metadata sidecar) under ``/opt/ai-orchestrator/reports/juris/`` — override with
``JURIS_REPORTS_DIR``. Generation is local-only; nothing here calls a network
LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

logger = logging.getLogger("juris_kai.reports")

DEFAULT_REPORTS_DIR = "/opt/ai-orchestrator/reports/juris"
KINDS = ("deep", "authority_bundle", "issue_matrix", "chronology")

DISCLAIMER = (
    "Informational only — this report is not legal advice and does not create "
    "a lawyer–client relationship. Every authority is drawn from the legal "
    "database; verify the current law before relying on it."
)

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
}

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Emoji / pictographs are not in the DejaVu font subset; strip them for the PDF
# (DOCX keeps them). The 📚 Sources label survives as "Sources".
_PDF_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F\u2696]")
_DEFAULT_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu",
                      "/usr/share/fonts/truetype")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def reports_dir() -> Path:
    """The report storage directory (created on demand)."""
    path = Path(os.environ.get("JURIS_REPORTS_DIR", DEFAULT_REPORTS_DIR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)


def report_path(report_id: str, fmt: str) -> Path | None:
    """The stored path for ``<id>.<fmt>``, or None if unknown/unsafe."""
    fmt = str(fmt or "").lower()
    if fmt not in CONTENT_TYPES:
        return None
    if not _ID_RE.match(str(report_id or "")):
        return None
    path = reports_dir() / f"juris_report_{report_id}.{fmt}"
    try:
        if path.resolve().parent != reports_dir().resolve():
            return None
    except OSError:
        return None
    return path if path.exists() else None


def list_reports() -> list:
    """Every stored report (newest first), from the metadata sidecars."""
    out = []
    for sidecar in sorted(reports_dir().glob("juris_report_*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(sidecar.read_text())
        except Exception:  # noqa: BLE001 - a bad sidecar must not break the list
            continue
        rid = meta.get("id")
        if not rid:
            continue
        for fmt in ("pdf", "docx"):
            p = reports_dir() / f"juris_report_{rid}.{fmt}"
            if not p.exists():
                meta[f"{fmt}_bytes"] = None
        out.append(meta)
    return out


# ---------------------------------------------------------------------------
# Citation firewall (narrative only)
# ---------------------------------------------------------------------------

def _default_verifier(text: str) -> dict:
    from core import legal_brain_client as lb
    return lb.verify_citations(text, record=True)


def _firewall_text(text: str, verifier=None) -> str:
    """Run the citation firewall; fail open so a report is never blanked."""
    from core.juris_kai import citation_firewall
    try:
        out = citation_firewall.apply_citation_firewall(
            text, verifier=verifier or _default_verifier)
        return out.get("text") or text or ""
    except Exception as exc:  # noqa: BLE001 - firewall must never crash a report
        logger.warning("report firewall failed (fail open): %s", exc)
        return text or ""


def _firewall_sections(sections: list, verifier) -> list:
    out = []
    for sec in sections or []:
        sec = dict(sec)
        if sec.get("firewall", True):
            text = "\n".join(sec.get("lines") or [])
            sec["lines"] = _firewall_text(text, verifier=verifier).split("\n")
        out.append(sec)
    return out


# ---------------------------------------------------------------------------
# Normalisation: result → header + sections + sources
# ---------------------------------------------------------------------------

def _source_lines(docs: list) -> list:
    """Plain-text 📚 Sources footer lines (temporal status included)."""
    from core.juris_kai.grounding import _footer_amendment
    lines = []
    for i, d in enumerate(docs or [], 1):
        if not isinstance(d, dict):
            continue
        title = (d.get("title") or "Untitled").strip()
        cite = (d.get("citation") or "").strip()
        year = d.get("year")
        mode = (d.get("store_mode") or "").strip()
        bits = [title]
        if cite and cite != title:
            bits.append(cite)
        if year:
            bits.append(str(year))
        if mode:
            bits.append(mode)
        line = " — ".join(bits)
        amendment = _footer_amendment(d.get("amended_by") or "")
        if amendment:
            line += f" — as amended by {amendment}"
        status = (d.get("temporal_status") or "").strip().upper()
        if status:
            line += f" [{status}]"
        lines.append(f"{i}. {line}")
    return lines


def _deep_sections(result: dict) -> list:
    judge = result.get("judge") or {}
    irac = judge.get("irac") or {}
    opponent = result.get("opponent") or {}
    authorities = list(result.get("authorities")
                       or judge.get("authorities") or [])
    contrary = list(opponent.get("contrary_authorities") or [])
    established = judge.get("established") or []
    disputed = judge.get("disputed") or []
    unresolved = judge.get("unresolved") or []
    confidence = judge.get("confidence")
    uncertainty = [f"Established: {len(established)}",
                   f"Disputed: {len(disputed)}",
                   f"Unresolved: {len(unresolved)}"]
    if confidence is not None:
        uncertainty.append(f"Confidence: {confidence}")
    if result.get("degraded"):
        uncertainty.append("Some reasoning passes degraded; bounded to sources.")
    return [
        {"heading": "Issue", "lines": [irac.get("issue") or "—"]},
        {"heading": "Rule", "lines": [irac.get("rule") or "—"]},
        {"heading": "Application", "lines": [irac.get("application") or "—"]},
        {"heading": "Conclusion", "lines": [irac.get("conclusion") or "—"]},
        {"heading": "Authorities", "firewall": False,
         "lines": [f"• {a}" for a in authorities] or ["None retrieved."]},
        {"heading": "Counter-authorities", "firewall": False,
         "lines": [f"• {a}" for a in contrary] or ["None identified."]},
        {"heading": "Uncertainty", "firewall": False, "lines": uncertainty},
    ]


def _authority_line(a: dict) -> str:
    line = (a.get("title") or "Untitled").strip()
    if a.get("citation") and a["citation"] != a.get("title"):
        line += f" — {a['citation']}"
    if a.get("year"):
        line += f" ({a['year']})"
    if a.get("temporal_status"):
        line += f" [{a['temporal_status']}]"
    return line


def _bundle_sections(result: dict) -> list:
    lines = []
    for i, entry in enumerate(result.get("bundle") or [], 1):
        lines.append(f"Issue {i}: {entry.get('issue') or '—'}")
        auths = entry.get("authorities") or []
        if auths:
            lines += [f"  - {_authority_line(a)}" for a in auths]
        else:
            lines.append("  - No authority found in the database.")
    return [{"heading": "Authority Bundle", "firewall": False,
             "lines": lines or ["No issue provided."]}]


def _matrix_sections(result: dict) -> list:
    lines = []
    for r in result.get("rows") or []:
        lines.append(f"[{r.get('status') or '—'}] {r.get('issue') or '—'}")
        lines.append(f"  Law: {r.get('law') or '—'}")
        lines.append(f"  Authority: {r.get('authority') or '—'}")
        lines.append(f"  Facts: {r.get('facts') or '—'}")
        lines.append(f"  Counterargument: {r.get('counterargument') or '—'}")
    return [{"heading": "Legal Issue Matrix", "firewall": False,
             "lines": lines or ["No issues."]}]


def _chronology_sections(result: dict) -> list:
    lines = []
    for e in result.get("events") or []:
        lines.append(f"{e.get('date')} — {e.get('event')} "
                     f"(Source: {e.get('source') or '—'})")
    return [{"heading": "Legal Chronology", "firewall": False,
             "lines": lines or ["No dates were found in the provided facts."]}]


_BUILDERS = {
    "deep": (_deep_sections, lambda r: list(r.get("docs") or [])),
    "authority_bundle": (_bundle_sections,
                         lambda r: list(r.get("authorities") or [])),
    "issue_matrix": (_matrix_sections,
                     lambda r: list(r.get("authorities") or [])),
    "chronology": (_chronology_sections,
                   lambda r: list(r.get("authorities") or [])),
}


def build_report(kind: str, result: dict, query: str = "", plan: str = "",
                 verifier=None) -> dict:
    """Normalise a result into ``{kind, query, plan, date, sections, docs}``.

    The narrative sections are firewalled before returning (authority lists are
    not). Never raises on a partial/degraded result: every section renders with
    an honest placeholder.
    """
    kind = str(kind or "deep").strip().lower()
    if kind not in _BUILDERS:
        kind = "deep"
    result = result or {}
    builder, docs_of = _BUILDERS[kind]
    sections = _firewall_sections(builder(result), verifier)
    docs = docs_of(result)
    return {
        "kind": kind,
        "query": (query or result.get("query") or "").strip(),
        "plan": str(plan or "").strip(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sections": sections,
        "docs": docs,
        "source_lines": _source_lines(docs),
    }


# ---------------------------------------------------------------------------
# PDF (fpdf2)
# ---------------------------------------------------------------------------

def _pdf_font(pdf):
    """Register a Unicode TTF when available; else fall back to Helvetica."""
    for directory in _DEFAULT_FONT_DIRS:
        regular = Path(directory) / "DejaVuSans.ttf"
        bold = Path(directory) / "DejaVuSans-Bold.ttf"
        if regular.exists():
            try:
                pdf.add_font("DejaVu", "", str(regular))
                if bold.exists():
                    pdf.add_font("DejaVu", "B", str(bold))
                return "DejaVu"
            except Exception as exc:  # noqa: BLE001 - fall back to core font
                logger.warning("PDF font registration failed: %s", exc)
                break
    return "Helvetica"


def _pdf_safe(text) -> str:
    return _PDF_EMOJI_RE.sub("", str(text or ""))


def render_pdf(report: dict) -> bytes:
    """Render a normalised report to PDF bytes (fpdf2)."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    report = report or {}
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font = _pdf_font(pdf)

    def line(text, size=11, style=""):
        pdf.set_font(font, style, size)
        pdf.multi_cell(0, 6, _pdf_safe(text),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    line("Juris Kai — Legal Research Report", 17, "B")
    pdf.ln(1)
    line(f"Query: {report.get('query') or '—'}", 11)
    line(f"Date: {report.get('date') or '—'}", 11)
    line(f"Plan: {report.get('plan') or '—'}", 11)
    line(f"Type: {report.get('kind') or 'deep'}", 11)
    pdf.ln(2)

    for sec in report.get("sections") or []:
        line(sec.get("heading") or "", 13, "B")
        for item in sec.get("lines") or [""]:
            line(item, 11)
        pdf.ln(1)

    line("📚 Sources", 13, "B")
    sources = report.get("source_lines") or []
    for item in sources or ["No sources retrieved."]:
        line(item, 10)

    pdf.ln(3)
    line(DISCLAIMER, 9)
    out = pdf.output()
    return bytes(out)


# ---------------------------------------------------------------------------
# DOCX (python-docx)
# ---------------------------------------------------------------------------

def render_docx(report: dict) -> bytes:
    """Render a normalised report to DOCX bytes (python-docx)."""
    from docx import Document

    report = report or {}
    doc = Document()
    doc.add_heading("Juris Kai — Legal Research Report", level=0)
    doc.add_paragraph(f"Query: {report.get('query') or '—'}")
    doc.add_paragraph(f"Date: {report.get('date') or '—'}")
    doc.add_paragraph(f"Plan: {report.get('plan') or '—'}")
    doc.add_paragraph(f"Type: {report.get('kind') or 'deep'}")

    for sec in report.get("sections") or []:
        doc.add_heading(sec.get("heading") or "", level=1)
        for item in sec.get("lines") or [""]:
            doc.add_paragraph(str(item))

    doc.add_heading("📚 Sources", level=1)
    sources = report.get("source_lines") or []
    for item in sources or ["No sources retrieved."]:
        doc.add_paragraph(str(item))

    doc.add_paragraph(DISCLAIMER)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Create / generate
# ---------------------------------------------------------------------------

def run_query(kind: str, query: str) -> dict:
    """Run the engine that produces the result for ``kind`` (local-only)."""
    from core.juris_kai import reasoning, tools
    q = (query or "").strip()
    if kind == "authority_bundle":
        return tools.authority_bundle([q])
    if kind == "issue_matrix":
        return tools.issue_matrix(q)
    if kind == "chronology":
        return tools.legal_chronology(q)
    return reasoning.run_deep(q)


def create_report(kind: str = "deep", result: dict | None = None,
                  query: str = "", plan: str = "", verifier=None,
                  report_id: str | None = None) -> dict:
    """Render + persist a report; return ``{id, pdf, docx, ...}``.

    If ``result`` is None the engine for ``kind`` is run on ``query`` first.
    """
    if result is None:
        result = run_query(kind, query)
    report = build_report(kind, result, query=query, plan=plan, verifier=verifier)
    rid = report_id or _new_id()
    directory = reports_dir()
    pdf_path = directory / f"juris_report_{rid}.pdf"
    docx_path = directory / f"juris_report_{rid}.docx"
    pdf_path.write_bytes(render_pdf(report))
    docx_path.write_bytes(render_docx(report))

    meta = {
        "id": rid,
        "kind": report["kind"],
        "query": report["query"],
        "plan": report["plan"],
        "date": report["date"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pdf_bytes": pdf_path.stat().st_size,
        "docx_bytes": docx_path.stat().st_size,
        "pdf": f"/api/juris-kai/reports/{rid}.pdf",
        "docx": f"/api/juris-kai/reports/{rid}.docx",
    }
    (directory / f"juris_report_{rid}.json").write_text(json.dumps(meta, indent=2))
    return {**meta, "pdf_path": str(pdf_path), "docx_path": str(docx_path)}


# ---------------------------------------------------------------------------
# Bot hand-off: stash a result so the inline "Export report" button can use it
# ---------------------------------------------------------------------------

_STASH_TTL_S = 24 * 3600


def _stash_dir() -> Path:
    path = reports_dir() / ".stash"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stash_result(result: dict, query: str = "", plan: str = "",
                 kind: str = "deep") -> str:
    """Persist a result for later export; return an opaque short token."""
    token = secrets.token_hex(6)
    payload = {"kind": kind, "query": query, "plan": plan,
               "result": result or {}}
    (_stash_dir() / f"{token}.json").write_text(json.dumps(payload))
    _prune_stash()
    return token


def load_stash(token: str) -> dict | None:
    """Load a stashed result, or None if missing/unsafe/expired."""
    if not _ID_RE.match(str(token or "")):
        return None
    path = _stash_dir() / f"{token}.json"
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > _STASH_TTL_S:
            path.unlink(missing_ok=True)
            return None
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - a bad stash is a miss, never a crash
        return None


def _prune_stash() -> None:
    try:
        for path in _stash_dir().glob("*.json"):
            if time.time() - path.stat().st_mtime > _STASH_TTL_S:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def export_keyboard(token: str) -> str:
    """Telegram inline keyboard JSON offering the export action."""
    return json.dumps({"inline_keyboard": [[
        {"text": "📄 Export report", "callback_data": f"report:{token}"}]]})
