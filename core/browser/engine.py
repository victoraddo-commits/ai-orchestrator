"""Playwright engine — the only layer that touches Chromium.

Import of ``playwright`` is deferred so the rest of ``core.browser`` (schemas,
stores, manager, client) imports cleanly on hosts without Chromium (the
orchestrator). Each session opens a *persistent* Chromium context rooted at the
profile's ``user-data-dir``; cookies/localStorage therefore survive restarts,
and ``storage_state`` can be saved/restored explicitly for crash recovery.

Container notes: Chromium in an unprivileged LXC needs ``--no-sandbox`` when
user-namespace sandboxing is unavailable; ``--disable-dev-shm-usage`` guards
small ``/dev/shm``. Both are opt-in via the constructor.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.browser.schema import ElementRef, PageSnapshot, now_iso


@dataclass
class PageHandle:
    context: Any
    page: Any
    console: list[str] = field(default_factory=list)
    secret_selectors: list[str] = field(default_factory=list)
    saved_selectors: list[str] = field(default_factory=list)


class EngineUnavailable(RuntimeError):
    pass


class PlaywrightEngine:
    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 30000,
        no_sandbox: Optional[bool] = None,
        extra_args: Optional[list[str]] = None,
        viewport: Optional[dict] = None,
    ):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.viewport = viewport or {"width": 1280, "height": 900}
        self._playwright = None
        self._chromium = None
        args = list(extra_args or [])
        if no_sandbox is None:
            no_sandbox = os.environ.get("KAI_BROWSER_NO_SANDBOX", "1") == "1"
        if no_sandbox:
            args += ["--no-sandbox", "--disable-setuid-sandbox"]
        args += ["--disable-dev-shm-usage"]
        self._args = args

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self._playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - host without Chromium
            raise EngineUnavailable(f"playwright not installed: {exc}") from exc
        self._playwright = sync_playwright().start()
        self._chromium = self._playwright.chromium

    def stop(self) -> None:
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
            self._chromium = None

    @property
    def available(self) -> bool:
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    # -- context ---------------------------------------------------------------
    def open(
        self,
        user_data_dir: str,
        *,
        storage_state_path: Optional[str] = None,
        restore_storage_state: bool = False,
        headless: Optional[bool] = None,
    ) -> PageHandle:
        self.start()
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        context = self._chromium.launch_persistent_context(
            user_data_dir,
            headless=self.headless if headless is None else headless,
            viewport=self.viewport,
            args=self._args,
        )
        context.set_default_timeout(self.timeout_ms)
        if restore_storage_state and storage_state_path and Path(storage_state_path).exists():
            try:
                state = json.loads(Path(storage_state_path).read_text())
                cookies = state.get("cookies") or []
                if cookies:
                    context.add_cookies(cookies)
            except (OSError, json.JSONDecodeError):
                pass
        page = context.pages[0] if context.pages else context.new_page()
        handle = PageHandle(context=context, page=page)
        page.on("console", lambda msg: handle.console.append(msg.text))
        return handle

    def close(self, handle: PageHandle) -> None:
        try:
            handle.context.close()
        except Exception:
            pass

    def save_storage_state(self, handle: PageHandle, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handle.context.storage_state(path=path)
        return path

    def current_url(self, handle: PageHandle) -> str:
        return handle.page.url

    # -- operations ------------------------------------------------------------
    def navigate(self, handle: PageHandle, url: str, timeout_ms: int = 30000) -> dict:
        resp = handle.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        return {"url": handle.page.url, "status": resp.status if resp else None}

    def fill(self, handle: PageHandle, selector: str, value: str) -> dict:
        handle.page.fill(selector, value)
        return {"selector": selector, "ok": True}

    def click(self, handle: PageHandle, selector: str) -> dict:
        handle.page.click(selector)
        return {"selector": selector, "ok": True}

    def upload_file(self, handle: PageHandle, selector: str, path: str) -> dict:
        handle.page.set_input_files(selector, path)
        return {"selector": selector, "ok": True}

    def evaluate(self, handle: PageHandle, expression: str) -> Any:
        return handle.page.evaluate(expression)

    def wait_for(self, handle: PageHandle, selector: str, state: str = "visible",
                 timeout_ms: int = 15000) -> dict:
        handle.page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return {"selector": selector, "state": state, "ok": True}

    def screenshot(self, handle: PageHandle, path: str, *, full_page: bool = False,
                   mask_selectors: Optional[list[str]] = None) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        masks = list(mask_selectors or []) + list(handle.secret_selectors)
        style_id = "__kai_mask__"
        if masks:
            css = ",".join(masks) + "{filter:blur(12px)!important;color:transparent!important;}"
            script = (
                "() => { const s=document.createElement('style');"
                f"s.id={style_id!r}; s.textContent={css!r}; document.head.appendChild(s); }}"
            )
            try:
                handle.page.evaluate(script)
            except Exception:
                pass
        try:
            handle.page.screenshot(path=path, full_page=full_page)
        finally:
            if masks:
                try:
                    handle.page.evaluate(
                        f"() => {{ const s=document.getElementById({style_id!r}); if(s) s.remove(); }}"
                    )
                except Exception:
                    pass
        return path

    def mark_secret_selector(self, handle: PageHandle, selector: str) -> None:
        if selector and selector not in handle.secret_selectors:
            handle.secret_selectors.append(selector)

    def console_logs(self, handle: PageHandle) -> list[str]:
        return list(handle.console)

    # -- snapshot --------------------------------------------------------------
    _SNAPSHOT_JS = """
() => {
  const sel = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.getAttribute('name')) return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    let path = el.tagName.toLowerCase();
    let parent = el.parentElement;
    if (parent) {
      const idx = Array.from(parent.children).filter(c => c.tagName === el.tagName).indexOf(el) + 1;
      path += ':nth-of-type(' + idx + ')';
    }
    return path;
  };
  const nodes = document.querySelectorAll('a,button,input,select,textarea,[role=button],[role=link],[role=alert]');
  const elements = Array.from(nodes).slice(0, 200).map(el => ({
    role: el.getAttribute('role') || el.tagName.toLowerCase(),
    name: (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').slice(0,120),
    type: el.getAttribute('type') || '',
    selector: sel(el),
  }));
  return {
    url: location.href,
    title: document.title || '',
    text: (document.body ? document.body.innerText : '').slice(0, 20000),
    elements,
    has_password_field: !!document.querySelector('input[type=password]'),
  };
}
"""

    def snapshot(self, handle: PageHandle) -> PageSnapshot:
        raw = handle.page.evaluate(self._SNAPSHOT_JS)
        raw["console"] = self.console_logs(handle)
        raw["elements"] = [ElementRef(**e).model_dump() for e in raw.get("elements", [])]
        return PageSnapshot(**raw)


class EngineError(RuntimeError):
    """Raised for transient engine failures (network/timeout/crash)."""
