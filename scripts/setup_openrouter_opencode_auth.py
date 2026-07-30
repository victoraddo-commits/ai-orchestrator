"""Registers OPENROUTER_API_KEY with opencode's own credential store so the
openrouter_claude_opus/openrouter_claude_sonnet coding providers
(core.ai_provider) can drive OpenRouter models through the opencode CLI.

Idempotent -- re-run safely any time, e.g. after a fresh deploy or if
auth.json is reset. Merges into an existing auth.json without disturbing
other keys (the OpenCode Zen "opencode" entry survives), and never partially
writes: the full dict is read, one key mutated, and the full dict written
back. Exits 1 without writing anything when OPENROUTER_API_KEY is unset.

`opencode auth login openrouter` cannot be used for this: it fails
headlessly with "fetch() URL is invalid" (confirmed 2026-07-28) -- this
direct auth.json write is the only confirmed-working setup path.
"""

import json
import os
import sys
from pathlib import Path

AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


def main():
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    try:
        auth = json.loads(AUTH_PATH.read_text()) if AUTH_PATH.exists() else {}
    except json.JSONDecodeError:
        print(f"refusing to overwrite corrupt (unparseable) {AUTH_PATH}", file=sys.stderr)
        return 1

    auth["openrouter"] = {"type": "api", "key": key}
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(auth))
    print(f"openrouter credential written to {AUTH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
