#!/usr/bin/env python3
"""Command Center endpoint contract check.

Guards against the recurring class of CC defect where a panel calls an endpoint
that does not exist (a 404 that renders as "Not Found" or raw code). It:

  1. extracts every endpoint the Command Center HTML calls (quoted, backtick and
     ``loadJsonTab`` arrays, including ``${...}`` interpolation);
  2. asserts each resolves to a route in the API's OpenAPI document;
  3. (optionally) smoke-calls every concrete GET endpoint and fails on 404/405,
     5xx or a connection error. Writes are existence-checked only — never
     executed; dynamic/prefix paths and auth-gated 401/403 are tolerated.

Exit code is non-zero on any drift so it can gate CI.

Usage::

    python scripts/cc_contract_check.py                 # local defaults
    python scripts/cc_contract_check.py --no-smoke      # static only
    python scripts/cc_contract_check.py --openapi-url https://host:8000/openapi.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTML = REPO_ROOT / "core" / "kai" / "command_center.html"

_FILTER_SUFFIXES = (".png", ".js", ".css", ".json", ".svg", ".ico", ".webmanifest")
_SHELL_PATHS = {"/", "/command-center", "/dashboard", "/miniapp", "/mobile"}

_CALL_RE = re.compile(
    r"""(?:api|apiSoft|apiT|fetch)\s*\(\s*(['"`])((?:/|https?://)[^'"`]*)\1""")
_XHR_RE = re.compile(
    r"""xhr\.open\(\s*(['"])([A-Z]+)\1\s*,\s*(['"`])((?:/|https?://)[^'"`]*)\3""")
_JSONTAB_RE = re.compile(r"loadJsonTab\([^,]+,\s*\[([^\]]*)\]")
_METHOD_RE = re.compile(r"""method\s*:\s*['"]([A-Z]+)['"]""")


def _split(raw: str) -> tuple[str, str]:
    """Split a call literal into (path, query); ``${...}`` -> ``@`` wildcard."""
    raw = raw.split("#")[0]
    path, _, query = raw.partition("?")
    path = re.sub(r"\$\{[^}]*\}", "@", path)
    path = re.sub(r"@\{[^}]*\}", "@", path)
    return path, query


def extract_endpoints(html: str) -> dict:
    """Return ``{path: {"method": str, "query": str}}`` for CC-called endpoints.

    ``@`` marks a runtime-interpolated segment; a trailing ``/`` marks a prefix
    the CC concatenates a value onto.
    """
    found: dict[str, dict] = {}

    def add(raw: str, method: str):
        if not raw.startswith("/"):
            return
        path, query = _split(raw)
        if path in _SHELL_PATHS or path.endswith(_FILTER_SUFFIXES):
            return
        found.setdefault(path, {"method": method, "query": query})

    for m in _CALL_RE.finditer(html):
        window = html[m.end():m.end() + 240]
        mm = _METHOD_RE.search(window)
        add(m.group(2), mm.group(1) if mm else "GET")

    for m in _XHR_RE.finditer(html):
        add(m.group(4), m.group(2).upper())

    for m in _JSONTAB_RE.finditer(html):
        for raw in re.findall(r"['\"`](/(?:[^'\"`]*))['\"`]", m.group(1)):
            add(raw, "GET")
    return found


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s != ""]


def _template_matches(template: str, concrete: str) -> bool:
    """True when a concrete CC path matches an OpenAPI path template."""

    def walk(t: list[str], c: list[str]) -> bool:
        if not t:
            return not c
        head, rest = t[0], t[1:]
        if head.startswith("{") and head.endswith("}"):
            if head == "{path}":
                return len(c) >= 1
            return bool(c) and walk(rest, c[1:])
        if not c:
            return False
        return (c[0] == "@" or c[0] == head) and walk(rest, c[1:])

    return walk(_segments(template), _segments(concrete))


def find_missing(endpoints: dict, openapi_paths: list[str]) -> list[str]:
    missing = []
    for concrete in sorted(endpoints):
        cand = concrete + "@" if concrete.endswith("/") and concrete != "/" else concrete
        if any(_template_matches(t, cand) for t in openapi_paths):
            continue
        # A '/' prefix the CC concatenates onto: accept when a template lives
        # under it (``'/api/sessions/' + id``).
        if concrete.endswith("/") and any(t.startswith(concrete) for t in openapi_paths):
            continue
        missing.append(concrete)
    return missing


def _load_openapi(args) -> list[str]:
    if args.openapi:
        data = json.loads(Path(args.openapi).read_text())
    elif args.openapi_url:
        data = json.loads(_http_get(args.openapi_url, args))
    else:
        sys.path.insert(0, str(REPO_ROOT))
        from core.api import app  # noqa: PLC0415
        data = app.openapi()
    return list(data.get("paths", {}).keys())


def _ssl_context(args):
    if args.insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _http_get(url: str, args):  # noqa: ANN001
    headers = {}
    if args.session:
        headers["X-Kai-Session"] = args.session
    if args.bridge:
        headers["Authorization"] = f"Bearer {args.bridge}"
    if args.identity:
        headers["X-Kai-User"] = "cc-contract@kai"
        headers["X-Kai-User-Id"] = "cc-contract"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=args.timeout,
                                context=_ssl_context(args)) as resp:
        return resp.read().decode("utf-8", "replace")


def _smoke_query(raw: str) -> str:
    """Give every query key a concrete non-empty value for the probe."""
    if not raw:
        return ""
    parts = []
    for pair in raw.split("&"):
        if not pair:
            continue
        key, _, val = pair.partition("=")
        val = re.sub(r"\$\{[^}]*\}", "test", val)
        if not val or "@" in val or "{" in val:
            val = "test"
        parts.append(f"{key}={val}")
    return "?" + "&".join(parts) if parts else ""


def smoke(endpoints: dict, args) -> list[str]:
    """GET-probe every concrete GET endpoint; report 404/405/5xx/network."""
    failures = []
    for path, meta in sorted(endpoints.items()):
        if meta["method"] != "GET" or path.endswith("/"):
            continue
        dynamic = "@" in path
        concrete = (path + "@" if dynamic else path).replace("@", "1")
        url = args.base_url.rstrip("/") + concrete + _smoke_query(meta["query"])
        try:
            _http_get(url, args)
        except urllib.error.HTTPError as exc:
            # A dynamic segment we filled with "1" may legitimately 404 (no such
            # resource) — only a 5xx proves the *endpoint* is broken there.
            if exc.code >= 500 or (exc.code in (404, 405) and not dynamic):
                failures.append(f"{exc.code} {path}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"ERR {path} ({type(exc).__name__})")
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", default=str(DEFAULT_HTML))
    ap.add_argument("--openapi", default=None)
    ap.add_argument("--openapi-url", default=None)
    ap.add_argument("--base-url", default=os.environ.get(
        "CC_BASE_URL", "https://127.0.0.1:8000"))
    ap.add_argument("--session", default=os.environ.get("KAI_SESSION", ""))
    ap.add_argument("--bridge", default=os.environ.get("KAI_BRIDGE_TOKEN", ""))
    ap.add_argument("--identity", action="store_true")
    ap.add_argument("--insecure", action="store_true", default=True)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.bridge:
        try:
            args.bridge = Path("/root/.ai-orchestrator/api_token").read_text().strip()
        except OSError:
            args.bridge = ""

    endpoints = extract_endpoints(Path(args.html).read_text())
    try:
        openapi_paths = _load_openapi(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not load OpenAPI ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 2
    missing = find_missing(endpoints, openapi_paths)
    smoke_failures = [] if args.no_smoke else smoke(endpoints, args)
    ok = not missing and not smoke_failures

    if args.json:
        print(json.dumps({"ok": ok, "endpoint_count": len(endpoints),
                          "missing": missing, "smoke_failures": smoke_failures},
                         indent=2))
        return 0 if ok else 1

    print(f"CC endpoints called: {len(endpoints)}")
    print(f"OpenAPI routes:      {len(openapi_paths)}")
    if missing:
        print(f"\nMISSING from OpenAPI ({len(missing)}):")
        for m in missing:
            print(f"  - {m}")
    if smoke_failures:
        print(f"\nSMOKE failures ({len(smoke_failures)}):")
        for f in smoke_failures:
            print(f"  - {f}")
    print("\nCONTRACT OK" if ok else "\nCONTRACT DRIFT DETECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
