"""BrowserOperator — orchestration over profiles, sessions, takeovers and engine.

The manager is host-agnostic: it depends on an injected ``engine`` (Playwright on
CT110, a fake in fast CT111 tests), an EventSink, an optional provider-policy
``gate_check`` and an optional ``guard`` (AgentGuard). Every action is validated
(operations), policy/URL-checked before credentials, and emitted on the bus and
to the audit log. Human takeover pauses a mission and resumes it only when the
page state actually changes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.browser import detectors
from core.browser import security
from core.browser.operations import validate_operation
from core.browser.profiles import ProfileStore
from core.browser.schema import (
    AuthState,
    SessionState,
    SessionStatus,
    TakeoverRecord,
    TakeoverStatus,
    now_iso,
)
from core.browser.sinks import EventSink, default_sink
from core.browser.sessions import SessionStore
from core.browser.storage import new_id
from core.browser.takeover import TakeoverStore


class BrowserOperatorError(RuntimeError):
    pass


class SessionNotFound(BrowserOperatorError):
    pass


class TooManySessions(BrowserOperatorError):
    pass


class ApprovalRequired(BrowserOperatorError):
    def __init__(self, approval_request_id: Optional[str], reason: str):
        super().__init__(reason)
        self.approval_request_id = approval_request_id


_SOURCE = "browser_operator"
_DEFAULT_RESUME_EXPIRY_S = 900


class BrowserOperator:
    def __init__(
        self,
        root: Path,
        *,
        engine: Any = None,
        sink: Optional[EventSink] = None,
        gate_check: Optional[Callable[[str], Any]] = None,
        guard: Any = None,
        provider_resolver: Optional[Callable[[str], Any]] = None,
        novnc_url: Optional[str] = None,
        max_concurrent: int = 2,
        headless: bool = True,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = engine
        self.sink = sink or default_sink(self.root)
        self.gate_check = gate_check
        self.guard = guard
        self.provider_resolver = provider_resolver
        self.novnc_url = novnc_url or f"http://{_host_ip()}:6080/vnc.html?autoconnect=1&resize=scale"
        self.headless = headless

        self.profiles = ProfileStore(self.root)
        self.sessions = SessionStore(self.root)
        self.takeovers = TakeoverStore(self.root)

        self._handles: dict[str, Any] = {}
        self._secrets: dict[str, list[str]] = {}
        self._provider_domains: dict[str, str] = {}
        self._lock = threading.RLock()
        self._sem = threading.BoundedSemaphore(max_concurrent)

    # -- provider helpers ------------------------------------------------------
    def set_provider_domain(self, provider_id: str, official_domain: Optional[str]) -> None:
        with self._lock:
            if official_domain:
                self._provider_domains[provider_id] = official_domain.lower()

    def official_domain(self, provider_id: str) -> Optional[str]:
        if provider_id in self._provider_domains:
            return self._provider_domains[provider_id]
        if self.provider_resolver is not None:
            try:
                descriptor = self.provider_resolver(provider_id)
                return (getattr(descriptor, "official_domain", None) or "").lower() or None
            except Exception:
                return None
        return None

    # -- session lifecycle -----------------------------------------------------
    def open_session(
        self,
        identity_id: str,
        provider_id: str,
        *,
        mission_id: Optional[str] = None,
        headless: Optional[bool] = None,
        restore_storage_state: bool = False,
    ) -> SessionState:
        if self.gate_check is not None:
            self.gate_check(provider_id)  # blocking ToS/legality precondition
        if not self._sem.acquire(blocking=False):
            raise TooManySessions(f"max concurrent browser sessions reached")
        session_id = new_id("bsess")
        try:
            profile = self.profiles.get_or_create(identity_id, provider_id)
            self._provider_domains.setdefault(provider_id, self.official_domain(provider_id) or "")
            handle = self._open_handle(profile, headless, restore_storage_state)
            self._handles[session_id] = handle
            self._secrets[session_id] = []
            snapshot = self.engine.snapshot(handle)
            state = SessionState(
                session_id=session_id,
                mission_id=mission_id,
                profile_id=profile.profile_id,
                identity_id=identity_id,
                provider_id=provider_id,
                status=SessionStatus.active,
                auth_state=AuthState(detectors.detect_auth_state(snapshot)),
                current_url=snapshot.url,
                storage_state_path=profile.storage_state_path,
                headless=self.headless if headless is None else headless,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            self.sessions.save(state)
            self.profiles.touch(profile.profile_id)
            self._emit("browser.session.started", {
                "session_id": session_id, "mission_id": mission_id,
                "identity_id": identity_id, "provider_id": provider_id,
                "profile_id": profile.profile_id,
            })
            self._audit("browser.session.start", {
                "session_id": session_id, "mission_id": mission_id,
                "provider_id": provider_id, "profile_id": profile.profile_id,
            })
            return state
        except Exception:
            self._sem.release()
            raise

    def _open_handle(self, profile, headless, restore_storage_state):
        return self.engine.open(
            profile.user_data_dir,
            storage_state_path=profile.storage_state_path,
            restore_storage_state=restore_storage_state,
            headless=headless,
        )

    def recover_session(self, session_id: str) -> SessionState:
        state = self._require(session_id)
        if session_id not in self._handles:
            profile = self.profiles.get(state.profile_id)
            if profile is None:
                raise BrowserOperatorError(f"profile gone for session {session_id}")
            self._handles[session_id] = self._open_handle(profile, state.headless, True)
        state.status = SessionStatus.active
        state.updated_at = now_iso()
        self.sessions.save(state)
        self._emit("browser.session.resumed", {"session_id": session_id, "recovered": True})
        self._audit("browser.session.recover", {"session_id": session_id})
        return state

    def end_session(self, session_id: str, *, save_state: bool = True) -> SessionState:
        state = self._require(session_id)
        handle = self._handles.pop(session_id, None)
        if handle is not None:
            try:
                if save_state and state.storage_state_path:
                    self.engine.save_storage_state(handle, state.storage_state_path)
            except Exception:
                pass
            self.engine.close(handle)
            self._sem.release()
        self._secrets.pop(session_id, None)
        state.status = SessionStatus.ended
        state.updated_at = now_iso()
        self.sessions.save(state)
        self._emit("browser.session.ended", {"session_id": session_id})
        self._audit("browser.session.end", {"session_id": session_id})
        return state

    # -- operations ------------------------------------------------------------
    def perform(self, session_id: str, op: str, params: Optional[dict] = None) -> dict:
        state = self._require(session_id)
        if state.status == SessionStatus.ended:
            raise BrowserOperatorError(f"session {session_id} already ended")
        handle = self._handles.get(session_id)
        if handle is None:
            self.recover_session(session_id)
            handle = self._handles[session_id]
        norm = validate_operation(op, params)

        guard = self._guard_gate(op, norm, state)
        if guard is not None and getattr(guard, "approval_required", False):
            self._audit("browser.action.guarded", {
                "session_id": session_id, "op": op,
                "risk": _risk(getattr(guard, "risk_level", "")),
                "decision": "require_approval",
            })
            raise ApprovalRequired(getattr(guard, "approval_request_id", None),
                                   f"operation {op!r} requires approval")

        try:
            result = self._dispatch(handle, op, norm, state)
            try:
                state.current_url = self.engine.current_url(handle)
            except Exception:
                pass
        except Exception as exc:
            self._audit("browser.action.error", {
                "session_id": session_id, "op": op, "error": f"{type(exc).__name__}: {exc}"})
            if state.status != SessionStatus.paused:
                state.status = SessionStatus.error
                self.sessions.save(state)
            raise
        state.last_action = op
        state.updated_at = now_iso()
        self.sessions.save(state)
        self._audit("browser.action", {
            "session_id": session_id, "op": op, "url": state.current_url,
            "risk": _risk(getattr(guard, "risk_level", "")),
        })
        return result

    def _dispatch(self, handle, op: str, norm: dict, state: SessionState) -> dict:
        if op == "navigate":
            result = self._with_retries(
                lambda: self.engine.navigate(handle, norm["url"], norm["timeout_ms"]), session_id=state.session_id)
            final_url = result.get("url") or norm["url"]
            if norm.get("enforce_domain"):
                security.assert_navigation_stays_on_site(final_url, self.official_domain(state.provider_id))
            state.current_url = final_url
            return {"op": op, "url": final_url, "status": result.get("status")}
        if op == "inspect":
            snap = self.engine.snapshot(handle)
            snap = self._sanitize_snapshot(state.session_id, snap)
            state.current_url = snap.url
            state.auth_state = AuthState(detectors.detect_auth_state(snap))
            return {"op": op, "snapshot": snap.model_dump(),
                    "detectors": detectors.run_detectors(snap),
                    "untrusted": True}
        if op == "fill":
            if norm.get("credential"):
                self._preflight_credential(state)
            self.engine.fill(handle, norm["selector"], norm["value"])
            if norm.get("credential"):
                self._secrets.setdefault(state.session_id, []).append(norm["value"])
                self.engine.mark_secret_selector(handle, norm["selector"])
            # Never log the value; only length is retained.
            self._audit("browser.fill", {
                "session_id": state.session_id, "selector": norm["selector"],
                "credential": norm.get("credential", False), "value_len": len(norm["value"])})
            return {"op": op, "selector": norm["selector"], "ok": True,
                    "credential": norm.get("credential", False)}
        if op == "click":
            return {"op": op, **self.engine.click(handle, norm["selector"])}
        if op == "screenshot":
            path = str(self.root / "screenshots" / f"{state.session_id}-{int(time.time())}.png")
            self.engine.screenshot(handle, path, full_page=norm["full_page"],
                                   mask_selectors=norm["mask_selectors"])
            return {"op": op, "screenshot_path": path}
        if op == "upload_file":
            return {"op": op, **self.engine.upload_file(handle, norm["selector"], norm["path"])}
        if op == "evaluate":
            return {"op": op, "result": self.engine.evaluate(handle, norm["expression"])}
        if op == "wait_for":
            return {"op": op, **self.engine.wait_for(handle, norm["selector"], norm["state"], norm["timeout_ms"])}
        raise BrowserOperatorError(f"unhandled op {op}")

    def _preflight_credential(self, state: SessionState) -> None:
        """Block credential entry unless host+policy are provider-valid."""
        current = state.current_url or ""
        security.assert_credential_target(current, self.official_domain(state.provider_id))
        if self.gate_check is not None:
            self.gate_check(state.provider_id)

    def _sanitize_snapshot(self, session_id: str, snap):
        secrets = self._secrets.get(session_id, [])
        text, truncated = security.sanitize_untrusted_text(snap.text)
        snap.text = security.redact_text(text, secrets)
        snap.console = [security.redact_text(c, secrets) for c in snap.console]
        snap.truncated = truncated or snap.truncated
        snap.untrusted = True
        return snap

    # -- human takeover --------------------------------------------------------
    def pause_for_human(
        self,
        session_id: str,
        *,
        reason: str,
        action_required: str,
        instructions: str = "",
        resume_condition: Optional[dict] = None,
        expires_in: int = _DEFAULT_RESUME_EXPIRY_S,
    ) -> TakeoverRecord:
        state = self._require(session_id)
        handle = self._handles.get(session_id)
        if handle is None:
            self.recover_session(session_id)
            handle = self._handles[session_id]
        snap = self._sanitize_snapshot(session_id, self.engine.snapshot(handle))
        takeover_id = new_id("tover")
        shot_path = str(self.root / "takeovers" / f"{takeover_id}.png")
        try:
            self.engine.screenshot(handle, shot_path, full_page=True)
        except Exception:
            shot_path = None
        from datetime import datetime, timezone, timedelta

        record = TakeoverRecord(
            takeover_id=takeover_id,
            session_id=session_id,
            mission_id=state.mission_id,
            provider_id=state.provider_id,
            identity_id=state.identity_id,
            action_required=action_required,
            reason=reason,
            current_state={
                "url": snap.url, "title": snap.title,
                "auth_state": detectors.detect_auth_state(snap),
                "detectors": detectors.run_detectors(snap),
            },
            instructions=instructions,
            no_vnc_url=self.novnc_url,
            screenshot_path=shot_path,
            resume_condition=resume_condition or {},
            status=TakeoverStatus.pending,
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
            created_at=now_iso(),
        )
        self.takeovers.create(record)
        state.status = SessionStatus.paused
        state.takeover_id = takeover_id
        state.current_url = snap.url
        state.updated_at = now_iso()
        self.sessions.save(state)
        self._emit("browser.session.paused", {
            "session_id": session_id, "mission_id": state.mission_id,
            "takeover_id": takeover_id, "action_required": action_required,
            "reason": reason})
        self._emit("human.action.required", {
            "session_id": session_id, "mission_id": state.mission_id,
            "takeover_id": takeover_id, "action_required": action_required,
            "no_vnc_url": self.novnc_url, "instructions": instructions})
        self._audit("browser.takeover.pause", {
            "session_id": session_id, "takeover_id": takeover_id,
            "action_required": action_required, "reason": reason})
        return record

    def resume_if_completed(self, session_id: str) -> dict:
        state = self._require(session_id)
        record = None
        if state.takeover_id:
            record = self.takeovers.get(state.takeover_id)
        if record is None:
            record = self.takeovers.active_for_session(session_id)
        if record is None:
            return {"resumed": False, "reason": "no_active_takeover"}

        if self.takeovers.is_expired(record):
            self.takeovers.expire(record.takeover_id)
            self._emit("human.action.expired", {
                "session_id": session_id, "takeover_id": record.takeover_id})
            self._audit("browser.takeover.expired", {
                "session_id": session_id, "takeover_id": record.takeover_id})
            return {"resumed": False, "reason": "takeover_expired", "takeover_id": record.takeover_id}

        handle = self._handles.get(session_id)
        if handle is None:
            self.recover_session(session_id)
            handle = self._handles[session_id]
        snap = self._sanitize_snapshot(session_id, self.engine.snapshot(handle))
        met = _condition_met(record.resume_condition, snap)
        if not met:
            return {"resumed": False, "reason": "condition_not_met",
                    "takeover_id": record.takeover_id,
                    "current": {"url": snap.url,
                                "auth_state": detectors.detect_auth_state(snap)}}

        self.takeovers.complete(record.takeover_id)
        state.status = SessionStatus.active
        state.takeover_id = None
        state.current_url = snap.url
        state.auth_state = AuthState(detectors.detect_auth_state(snap))
        state.updated_at = now_iso()
        self.sessions.save(state)
        self._emit("browser.session.resumed", {
            "session_id": session_id, "mission_id": state.mission_id,
            "takeover_id": record.takeover_id})
        self._emit("human.action.completed", {
            "session_id": session_id, "mission_id": state.mission_id,
            "takeover_id": record.takeover_id})
        self._audit("browser.takeover.complete", {
            "session_id": session_id, "takeover_id": record.takeover_id,
            "mission_id": state.mission_id})
        return {"resumed": True, "takeover_id": record.takeover_id,
                "session_id": session_id, "session_status": state.status.value}

    # -- queries / helpers -----------------------------------------------------
    def get_session(self, session_id: str) -> Optional[SessionState]:
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[SessionState]:
        return self.sessions.list()

    def list_takeovers(self, status: Optional[str] = None) -> list[TakeoverRecord]:
        return self.takeovers.list(status=status)

    def health(self) -> dict:
        return {
            "root": str(self.root),
            "profiles": len(self.profiles.list()),
            "sessions": len(self.sessions.list()),
            "open_handles": len(self._handles),
            "pending_takeovers": len(self.takeovers.list(status=TakeoverStatus.pending.value)),
            "engine_available": bool(getattr(self.engine, "available", False)) if self.engine else False,
            "novnc_url": self.novnc_url,
        }

    def _require(self, session_id: str) -> SessionState:
        state = self.sessions.get(session_id)
        if state is None:
            raise SessionNotFound(f"session not found: {session_id}")
        return state

    def _guard_gate(self, op: str, norm: dict, state: SessionState):
        if self.guard is None:
            return None
        try:
            from core.agentguard.guard import ActionRequest, ActionType
        except Exception:
            return None
        if op == "fill" and norm.get("credential"):
            action, risk_resource = ActionType.CREDENTIAL, f"browser://{state.provider_id}/credential"
        elif op in ("navigate", "click"):
            action, risk_resource = ActionType.NETWORK, norm.get("url") or norm.get("selector", "")
        else:
            action, risk_resource = ActionType.READ, f"browser://{state.session_id}/{op}"
        request = ActionRequest(
            agent_id="kai_browser", user_id=state.identity_id or "kai",
            action_type=action, resource=risk_resource, details=f"{op} on {state.provider_id}")
        return self.guard.check_action(request)

    def _with_retries(self, fn, *, session_id: str, retries: int = 2):
        delay = 0.2
        last: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                return fn()
            except Exception as exc:  # transient network/timeout/crash
                last = exc
                self._emit("browser.error", {
                    "session_id": session_id, "attempt": attempt + 1,
                    "error": type(exc).__name__})
                if attempt < retries:
                    time.sleep(delay)
                    delay *= 2
        raise last  # type: ignore[misc]

    def _emit(self, topic: str, payload: dict) -> None:
        payload = {**payload, "source": _SOURCE}
        self.sink.emit(topic, payload)

    def _audit(self, event_type: str, details: dict) -> None:
        self.sink.audit(event_type, details)


def _risk(value: Any) -> str:
    return getattr(value, "value", value or "none")


def _condition_met(condition: dict, snap) -> bool:
    if not condition:
        return False
    ctype = condition.get("type")
    if ctype == "auth_state":
        return detectors.detect_auth_state(snap) == condition.get("equals")
    if ctype == "url_contains":
        return condition.get("value", "") in (snap.url or "")
    if ctype == "url_not_contains":
        return condition.get("value", "") not in (snap.url or "")
    if ctype == "detector":
        name = condition.get("name", "")
        fn = detectors.DETECTORS.get(name)
        if fn is None:
            return False
        result = fn(snap)
        detected = result.get("detected") if isinstance(result, dict) else result
        return bool(detected) == bool(condition.get("equals", True))
    if ctype == "element_present":
        selector = condition.get("selector", "")
        return any(el.selector == selector for el in snap.elements)
    return False


def _host_ip() -> str:
    import os
    import socket

    if os.environ.get("KAI_BROWSER_HOST_IP"):
        return os.environ["KAI_BROWSER_HOST_IP"]
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"
