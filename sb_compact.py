#!/usr/bin/env python3
"""Second Brain compaction — keep only current (non-superseded) heads.

The append-only stores grow without bound because the same entities are
re-written every cycle. `current_index.json` maps entity -> current record id,
so we can rewrite records.jsonl to just those heads. This is safe under the
store's declared `newest_wins` merge policy and bounds the file to one record
per entity.

Usage: python3 sb_compact.py [store_name ...]   (default: operational)
"""
import json
import os
import shutil
import sys
import threading
import time

STORES_ROOT = "/opt/ai-orchestrator/core/second_brain/stores"


def _atomic_write_json(path: str, obj, *, indent: int = 2) -> None:
    """Write ``obj`` to ``path`` atomically.

    Writes a sibling temp file in the same directory, flushes it to disk, then
    ``os.replace()``s it over the target. A concurrent reader therefore always
    observes either the previous complete file or the new complete file --
    never a truncated/partial one. The old ``json.dump(obj, open(path, "w"))``
    truncated the live file and, racing another writer, could leave a stray
    trailing brace behind.
    """
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=indent)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def compact(store_name: str) -> dict:
    d = os.path.join(STORES_ROOT, store_name)
    records = os.path.join(d, "records.jsonl")
    index_f = os.path.join(d, "current_index.json")
    manifest_f = os.path.join(d, "manifest.json")
    if not os.path.exists(records):
        return {"store": store_name, "skipped": "no records"}

    try:
        with open(index_f) as fh:
            index = json.load(fh)
    except Exception:
        index = {}
    keep_ids = set(v for v in index.values() if v)

    before = os.path.getsize(records)
    ts = time.strftime("%Y%m%dT%H%M%SZ")
    # Unique per writer: two concurrent compactions must not clobber each
    # other's temp file before os.replace().
    tmp = f"{records}.compact.{os.getpid()}.{threading.get_ident()}.tmp"
    kept = 0
    if keep_ids:
        with open(records) as fin, open(tmp, "w") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("id") in keep_ids:
                    fout.write(line + "\n")
                    kept += 1
    else:
        # No usable index -> keep the latest record per entity.
        latest = {}
        with open(records) as fin:
            for line in fin:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                latest[rec.get("entity")] = line.rstrip("\n")
        with open(tmp, "w") as fout:
            for line in latest.values():
                fout.write(line + "\n")
                kept += 1

    shutil.copyfile(records, records + ".bak-" + ts)
    os.replace(tmp, records)
    after = os.path.getsize(records)

    # fix manifest count (atomic: never truncate the live file in place)
    try:
        with open(manifest_f) as fh:
            m = json.load(fh)
    except Exception:
        m = {}
    m.setdefault("schema_version", 1)
    m.setdefault("merge_policy", "newest_wins")
    m.setdefault("store_name", store_name)
    m["record_count"] = kept
    _atomic_write_json(manifest_f, m)

    return {"store": store_name, "before_bytes": before, "after_bytes": after,
            "kept": kept, "reclaimed_bytes": before - after}


if __name__ == "__main__":
    names = sys.argv[1:] or ["operational"]
    for n in names:
        print(json.dumps(compact(n)))
