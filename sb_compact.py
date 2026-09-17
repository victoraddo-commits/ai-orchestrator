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
import time

STORES_ROOT = "/opt/ai-orchestrator/core/second_brain/stores"


def compact(store_name: str) -> dict:
    d = os.path.join(STORES_ROOT, store_name)
    records = os.path.join(d, "records.jsonl")
    index_f = os.path.join(d, "current_index.json")
    manifest_f = os.path.join(d, "manifest.json")
    if not os.path.exists(records):
        return {"store": store_name, "skipped": "no records"}

    try:
        index = json.load(open(index_f))
    except Exception:
        index = {}
    keep_ids = set(v for v in index.values() if v)

    before = os.path.getsize(records)
    ts = time.strftime("%Y%m%dT%H%M%SZ")
    tmp = records + ".compact.tmp"
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

    # fix manifest count
    try:
        m = json.load(open(manifest_f)) if os.path.exists(manifest_f) else {}
    except Exception:
        m = {}
    m.setdefault("schema_version", 1)
    m.setdefault("merge_policy", "newest_wins")
    m.setdefault("store_name", store_name)
    m["record_count"] = kept
    json.dump(m, open(manifest_f, "w"), indent=2)

    return {"store": store_name, "before_bytes": before, "after_bytes": after,
            "kept": kept, "reclaimed_bytes": before - after}


if __name__ == "__main__":
    names = sys.argv[1:] or ["operational"]
    for n in names:
        print(json.dumps(compact(n)))
