from __future__ import annotations

from .models import Record


def check(discovered: list[Record], stored: list[Record]) -> dict:
    stored_ids = {r.id for r in stored}
    missing = sorted({r.id for r in discovered if r.id not in stored_ids})
    return {
        "ok": not missing,
        "discovered": len({r.id for r in discovered}),
        "stored": len(stored_ids),
        "missing": missing,
    }
