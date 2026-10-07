"""In-memory engine double for fast orchestrator-side tests (no Chromium).

Implements the same surface as :class:`core.browser.engine.PlaywrightEngine`, so
the profile/session/takeover/security logic can be exercised on CT111 without
Playwright.
"""

from __future__ import annotations

import json
import os

from core.browser.schema import ElementRef, PageSnapshot


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.title = ""
        self.text = ""
        self.password = False
        self.elements: list[dict] = []


class FakeHandle:
    def __init__(self, user_data_dir: str):
        self.user_data_dir = user_data_dir
        self.page = FakePage()
        self.console: list[str] = []
        self.secret_selectors: list[str] = []
        self.cookies: dict = {}


class FakeEngine:
    available = True

    def __init__(self):
        self.handles: list[FakeHandle] = []

    @property
    def last(self) -> FakeHandle:
        return self.handles[-1]

    # -- lifecycle -------------------------------------------------------------
    def open(self, user_data_dir, *, storage_state_path=None, restore_storage_state=False,
             headless=None):
        handle = FakeHandle(user_data_dir)
        self.handles.append(handle)
        if restore_storage_state and storage_state_path and os.path.exists(storage_state_path):
            try:
                with open(storage_state_path) as fh:
                    handle.cookies = json.load(fh).get("cookies", {})
            except Exception:
                pass
        return handle

    def close(self, handle):
        pass

    def save_storage_state(self, handle, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"cookies": handle.cookies}, fh)
        return path

    def mark_secret_selector(self, handle, selector):
        if selector not in handle.secret_selectors:
            handle.secret_selectors.append(selector)

    def current_url(self, handle):
        return handle.page.url

    def console_logs(self, handle):
        return list(handle.console)

    # -- operations ------------------------------------------------------------
    def navigate(self, handle, url, timeout_ms=30000):
        final = url
        if url.rstrip("/").endswith("/redirect-evil"):
            final = "http://evil.example.test/login"
        handle.page.url = final
        if "login" in final:
            handle.page.title = "Sign in"
            handle.page.text = "Sign in  password  forgot password"
            handle.page.password = True
        elif "dashboard" in final:
            handle.page.title = "Dashboard"
            handle.page.text = "Welcome back  Log out  My account"
            handle.page.password = False
        else:
            handle.page.title = "Page"
            handle.page.text = ""
        return {"url": final, "status": 200}

    def fill(self, handle, selector, value):
        if "password" in selector:
            handle.page.password = False
        return {"selector": selector, "ok": True}

    def click(self, handle, selector):
        return {"selector": selector, "ok": True}

    def upload_file(self, handle, selector, path):
        return {"selector": selector, "ok": True}

    def evaluate(self, handle, expression):
        return "ok"

    def wait_for(self, handle, selector, state="visible", timeout_ms=15000):
        return {"selector": selector, "state": state, "ok": True}

    def screenshot(self, handle, path, *, full_page=False, mask_selectors=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"PNG")
        return path

    def snapshot(self, handle):
        return PageSnapshot(
            url=handle.page.url, title=handle.page.title, text=handle.page.text,
            elements=[ElementRef(**e) for e in handle.page.elements],
            has_password_field=handle.page.password, console=list(handle.console))
