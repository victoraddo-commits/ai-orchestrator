"""AgentGuard legal boundary (Legal Brain 2.0 Phase 6, Task 2).

Puts the legal module behind AgentGuard's ``check_action`` gate:

* **Tool isolation** — a legal agent may only call retrieval/document/status/
  relations/verification tools. Shell / exec / network-egress tools are denied,
  fail-closed, *before* any tool runs (and even if the underlying guard is
  disabled).
* **Output gate** — the outbound legal answer is passed through the existing
  citation firewall (:mod:`core.juris_kai.citation_firewall`) and the injection
  guard (:mod:`core.legal.injection`), enforced as an AgentGuard policy. This
  wires the modules; it does not re-implement their logic.
* **Corpus-poisoning detection** — a read-only scan of retrieved/corpus text
  with :func:`core.legal.injection.scan`, flagging instruction-like content,
  encoding evasion and suspicious structure.
* **Source integrity** — detects a ``doc_hash`` mismatch (a retrieved document
  whose body changed under a stable id), or consumes the legal-brain's own
  ``/integrity`` report.
* **Audit** — every decision is recorded through the shared
  :mod:`core.audit_logger`; details are sanitized so body text and secrets never
  enter the log.

The module is side-effect-injectable (``guard`` / ``audit_fn`` / ``verifier`` /
``scan_fn`` / ``integrity_fn``) so it is fully unit-testable without network.
"""
from __future__ import annotations

import hashlib
import logging
import re

from core.agentguard.guard import (
    AgentGuard, ActionRequest, ActionType, Decision, GuardResult, RiskLevel,
)

logger = logging.getLogger("agentguard.legal")

# Tools the legal module is permitted to use. Everything else is denied.
LEGAL_TOOL_ALLOWLIST = frozenset({
    "retrieve", "search", "document", "document_fetch", "status",
    "relations", "authority", "verify_citations", "monitor", "stats",
})

# Secondary belt: a tool name carrying one of these markers is never allowed,
# even if it somehow reached the allowlist.
FORBIDDEN_TOOL_MARKERS = ("shell", "exec", "subprocess", "system", "bash",
                          "eval", "curl", "wget", "egress", "network", "http")

# Detail keys that must never be written to the audit log verbatim.
_SENSITIVE_KEY_PARTS = ("content", "text", "answer", "prompt", "body", "raw",
                        "message", "password", "token", "api_key", "secret",
                        "authorization", "credential")

_EVASION_STEPS = frozenset({
    "nfkc", "zero_width_strip", "homoglyph_fold", "leet_fold", "despace",
    "encoding_decode",
})

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _default_scan(text):
    from core.legal import injection
    return injection.scan(text)


def _default_audit(**kwargs):
    from core.audit_logger import log_audit_event
    log_audit_event(**kwargs)


def _default_integrity():
    """Best-effort live integrity checker over the legal-brain client."""
    try:
        from core import legal_brain_client as lb
        return lb.integrity
    except Exception:  # noqa: BLE001 - optional
        return None


def _sanitize(value):
    """Recursively redact body/secrets from an audit details payload."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS):
                clean[key] = "[redacted]"
            else:
                clean[key] = _sanitize(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


def _content_hash(content) -> str:
    """sha256 of whitespace-normalized content (mirrors CT100 document_meta)."""
    normalized = " ".join((content or "").split())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _suspicious_structure(content) -> bool:
    """Cheap structural smell: unusual control-char density or huge padding."""
    text = content or ""
    if not text:
        return False
    if len(text) > 200_000:
        return True
    control = len(_CONTROL_RE.findall(text))
    return control / max(1, len(text)) > 0.02


class LegalGuard:
    """AgentGuard-enforced boundary for the legal module."""

    def __init__(self, guard=None, *, audit_fn=None, verifier=None,
                 scan_fn=None, integrity_fn=None):
        self.guard = guard or AgentGuard({"enabled": True})
        self._audit_fn = audit_fn
        self.verifier = verifier
        self._scan_fn = scan_fn
        self._integrity_fn = integrity_fn

    # -- audit ---------------------------------------------------------------
    def audit(self, event_type, resource, decision, details=None) -> None:
        """Record one legal-boundary decision (sanitized, never raises)."""
        payload = _sanitize(dict(details or {}))
        payload["decision"] = decision
        fn = self._audit_fn or _default_audit
        try:
            fn(event_type=event_type, operator="juris_kai", endpoint=resource,
               method="LEGAL", status_code=200, details=payload)
        except Exception as exc:  # noqa: BLE001 - audit must never break a reply
            logger.warning("legal audit failed: %s", exc)

    # -- tool isolation ------------------------------------------------------
    def check_tool(self, tool, *, agent_id="juris_kai", user_id="operator",
                   details="", metadata=None) -> GuardResult:
        """Allow only legal retrieval tools; deny everything else, fail-closed."""
        name = (tool or "").strip().lower()
        forbidden = (not name or name not in LEGAL_TOOL_ALLOWLIST
                     or any(m in name for m in FORBIDDEN_TOOL_MARKERS))
        if forbidden:
            result = GuardResult(
                decision=Decision.DENY,
                risk_level=RiskLevel.HIGH,
                policy_matched="legal-tool-isolation",
                message=f"Legal tool '{name or 'unknown'}' is not permitted",
            )
            self.audit("legal.tool.denied", f"legal:tool:{name or 'unknown'}",
                       Decision.DENY.value, {"tool": name, "details": details})
            return result

        request = ActionRequest(
            agent_id=agent_id, user_id=user_id, action_type=ActionType.READ,
            resource=f"legal:tool:{name}",
            details=details or f"legal tool {name}",
            metadata=metadata or {},
        )
        result = self.guard.check_action(request)
        if result.decision != Decision.ALLOW:
            # Fail-closed for the legal path: approval/deny never runs the tool.
            result = GuardResult(
                decision=Decision.DENY,
                risk_level=result.risk_level,
                policy_matched=(result.policy_matched
                                or "legal-fail-closed"),
                message=result.message,
            )
        self.audit("legal.tool.allowed" if result.decision == Decision.ALLOW
                   else "legal.tool.denied", request.resource,
                   result.decision.value, {"tool": name})
        return result

    # -- output gate ---------------------------------------------------------
    def guard_output(self, answer, *, source="juris_kai", protected=None,
                     apply_injection=True, apply_firewall=True,
                     verifier=None) -> dict:
        """Enforce the legal output policy on an outbound answer.

        Runs the existing injection guard (redact/block leaked instructions)
        and citation firewall (strip unverifiable citations) as AgentGuard
        policies. Safe answers are returned unchanged; a poisoned answer is
        replaced by the safe fallback. Never raises.
        """
        text = answer or ""
        result = {"text": text, "changed": False, "denied": False,
                  "injection": None, "citations": None, "error": None}

        gate = self.guard.check_action(ActionRequest(
            agent_id="juris_kai", user_id="operator", action_type=ActionType.READ,
            resource="legal:output", details="enforce legal output policy"))
        if gate.decision != Decision.ALLOW:
            from core.legal.injection import SAFE_FALLBACK
            result.update(text=SAFE_FALLBACK, denied=True, changed=True)
            self.audit("legal.output.denied", "legal:output",
                       gate.decision.value)
            return result

        try:
            if apply_injection:
                from core.legal import injection
                out = injection.guard_output(text, source=source,
                                             protected=protected)
                result["injection"] = {"tripped": bool(out.get("tripped")),
                                       "markers": out.get("markers", [])}
                if out.get("tripped"):
                    text = out.get("text", text)
                    result["changed"] = True

            if apply_firewall:
                from core.juris_kai import citation_firewall
                fw = citation_firewall.apply_citation_firewall(
                    text, verifier=verifier if verifier is not None
                    else self.verifier)
                result["citations"] = {"changed": bool(fw.get("changed")),
                                       "error": fw.get("error")}
                text = fw.get("text", text)
                if fw.get("changed"):
                    result["changed"] = True
        except Exception as exc:  # noqa: BLE001 - never break a legal reply
            logger.warning("legal output gate failed (fail-closed): %s", exc)
            from core.legal.injection import SAFE_FALLBACK
            return {"text": SAFE_FALLBACK, "changed": True, "denied": True,
                    "injection": result["injection"],
                    "citations": result["citations"], "error": str(exc)}

        result["text"] = text
        self.audit("legal.output.enforced", "legal:output", "allow", {
            "injection_tripped": bool(result["injection"]
                                      and result["injection"]["tripped"]),
            "citations_changed": bool(result["citations"]
                                      and result["citations"]["changed"]),
        })
        return result

    # -- corpus-poisoning detection -----------------------------------------
    def scan_corpus(self, docs, *, scan_fn=None) -> dict:
        """Read-only poisoning report over a set of documents."""
        scan = scan_fn or self._scan_fn or _default_scan
        scanned = 0
        poisoned = []
        for doc in docs or []:
            if not isinstance(doc, dict):
                continue
            scanned += 1
            content = (doc.get("content") or doc.get("chunk_content") or "")
            try:
                verdict = scan(content) or {}
            except Exception as exc:  # noqa: BLE001 - scan is advisory
                logger.warning("poisoning scan failed: %s", exc)
                continue
            reasons = []
            if verdict.get("suspected"):
                reasons.append("instruction_like")
            evasions = sorted(set(verdict.get("normalizations") or [])
                              & _EVASION_STEPS)
            if evasions:
                reasons.append("obfuscation:" + ",".join(evasions))
            if _suspicious_structure(content):
                reasons.append("suspicious_structure")
            if reasons:
                poisoned.append({
                    "document_id": doc.get("id") or doc.get("document_id"),
                    "title": doc.get("title") or "",
                    "markers": verdict.get("markers") or [],
                    "reasons": reasons,
                    "length": len(content),
                })
        return {"scanned": scanned, "poisoned": poisoned,
                "count": len(poisoned)}

    # -- source integrity ----------------------------------------------------
    def detect_hash_mismatches(self, records, *, integrity_fn=None,
                               hash_fn=None) -> dict:
        """Flag retrieved docs whose body no longer matches their ``doc_hash``.

        With ``integrity_fn`` (e.g. the legal-brain ``/integrity`` checker) the
        brain's verdict is used; otherwise the body is re-hashed locally and
        compared with the supplied ``doc_hash``.
        """
        check = integrity_fn or self._integrity_fn
        hasher = hash_fn or _content_hash
        mismatches = []
        records = list(records or [])
        for rec in records:
            if not isinstance(rec, dict):
                continue
            doc_id = rec.get("id") or rec.get("document_id")
            title = rec.get("title") or ""
            if check is not None:
                try:
                    report = check(doc_id) or {}
                except Exception as exc:  # noqa: BLE001 - degraded, not fatal
                    mismatches.append({"document_id": doc_id, "title": title,
                                       "reason": "integrity_error",
                                       "error": str(exc)})
                    continue
                if report.get("all_versions_intact") is False:
                    mismatches.append({"document_id": doc_id, "title": title,
                                       "reason": "integrity_report",
                                       "report": report})
                continue
            expected = rec.get("doc_hash") or rec.get("expected_hash")
            if not expected:
                continue
            actual = hasher(rec.get("content") or rec.get("chunk_content") or "")
            if actual and actual != expected:
                mismatches.append({
                    "document_id": doc_id, "title": title,
                    "reason": "doc_hash_mismatch",
                    "expected_hash": expected, "computed_hash": actual,
                })
        return {"checked": len(records), "mismatches": mismatches,
                "count": len(mismatches)}


# A process-wide default boundary for callers that do not inject one.
_default_guard = None


def get_legal_guard(**overrides) -> LegalGuard:
    """Return a shared :class:`LegalGuard` (or a fresh one with overrides)."""
    global _default_guard
    if overrides:
        return LegalGuard(**overrides)
    if _default_guard is None:
        _default_guard = LegalGuard()
    return _default_guard
