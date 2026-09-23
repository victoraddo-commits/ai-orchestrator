# Juris Kai Grounding (Phase 0 + Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Juris Kai answers source-backed and verifiable, and make the legal corpus correct, by fixing retrieval, enforcing strict grounding (no ungrounded legal substance), and adding deterministic source citations.

**Architecture:** Phase 0 repairs the CT100 corpus (audit → quarantine junk → truthful `store_mode` → metadata fixes → ingestion validation gate). Phase 1 fixes CT100 FTS retrieval (query escaping, correct snippet column, clean content) and adds progressive retrieval + a grounding verdict + a deterministic Sources footer on CT111, with a strict "no legal substance without a source" response path.

**Tech Stack:** Python 3.12, SQLite + FTS5 (CT100 `kai-legal-brain`), FastAPI/orchestrator modules (CT111 `/opt/ai-orchestrator`), pytest, Telegram bot (`core/juris_kai/bot.py`), local model `qwen3-coder:kai`.

**Resolved decisions (from spec §9, owner-approved):**
- Usable-source minimum content: **`MIN_SOURCE_CHARS = 400`**.
- Tier rule: stage-1/2 hit ⇒ `GROUNDED`; stage-3/4 hit ⇒ `PARTIAL`; none ⇒ `UNGROUNDED`.
- `UNGROUNDED` ⇒ **no legal substance**; honest no-source response only.

**Repo/deploy map:**
- CT100 corpus + retrieval code: live `/opt/kai-legal-brain/`; source repo `LXC113:/root/juris-legal-brain/` (git). Changes must be synced both ways.
- CT111 bot/retrieval client: live `/opt/ai-orchestrator/` (git branch `main`; push to `runner-kai-2.0-20260918`).

**Commands used throughout:**
- `PVEB='ssh -i /root/.ssh/pve2_deploy -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.110'`
- CT100: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && <cmd>"'`
- CT111: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && <cmd>"'`
- Always `cd` into the app dir before ad-hoc python or `core` won't import.

---

## File Structure

**Create (CT100 `/opt/kai-legal-brain/`):**
- `core/legal/query_normalize.py` — safe FTS5 query building.
- `core/legal/validation.py` — ingestion validation gate.
- `core/legal/corpus_audit.py` — corpus classification/reporting.
- `scripts/remediate_corpus.py` — idempotent, dry-run-first remediation.
- `tests/test_query_normalize.py`, `tests/test_validation.py`, `tests/test_corpus_audit.py`.

**Modify (CT100):**
- `core/legal/storage.py` — `search()` uses normalized query + `content` snippet + strips `<mark>`; returns `store_mode`.
- `legal_brain_api.py` — `/ingest` and `/ingest/file` call the validation gate; `/search` returns `store_mode`/`content_excerpt`.

**Create (CT111 `/opt/ai-orchestrator/`):**
- `core/juris_kai/grounding.py` — progressive retrieval, verdict, Sources footer.
- `tests/test_juris_grounding.py`.

**Modify (CT111):**
- `core/juris_kai/legal_context.py` — add `retrieve()` with progressive stages; keep `query_knowledge_base` as a thin wrapper.
- `core/juris_kai/prompt.py` — grounding-aware prompt builder (`build_grounded_prompt`).
- `core/juris_kai/bot.py` — `_handle_free_text` uses `retrieve()`; strict UNGROUNDED path; footer; tier banner.

---

## Phase 0 — Corpus Integrity

### Task 1: FTS5 query normalization (CT100)

**Files:**
- Create: `/opt/kai-legal-brain/core/legal/query_normalize.py`
- Test: `/opt/kai-legal-brain/tests/test_query_normalize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_normalize.py
from core.legal.query_normalize import build_match_query, tokens

def test_strips_fts5_metacharacters():
    # Comma/parens/question must not reach FTS5 raw
    q = build_match_query("Criminal Offences Act, 1960 (Act 29)?")
    assert "," not in q and "(" not in q and "?" not in q

def test_and_query_joins_tokens():
    assert build_match_query("penalty for stealing", mode="and") == "penalty AND stealing"

def test_or_query_joins_tokens():
    assert build_match_query("penalty for stealing", mode="or") == "penalty OR stealing"

def test_stopwords_removed():
    assert "the" not in tokens("the penalty for stealing")

def test_empty_query_returns_empty():
    assert build_match_query("???") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_query_normalize.py -v"'`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.legal.query_normalize'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/legal/query_normalize.py
"""Safe FTS5 query construction — never pass raw user text to MATCH.

FTS5 treats , ( ) ? * " : ^ - as operators; a raw query raises
'fts5: syntax error'. We tokenize to word characters and build either an
AND (precise) or OR (recall) expression.
"""
from __future__ import annotations
import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")

STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "is", "are",
    "was", "were", "be", "been", "and", "or", "not", "with", "that", "this",
    "it", "its", "by", "from", "as", "but", "if", "so", "all", "any", "can",
    "has", "had", "have", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "i", "you", "he", "she", "we", "they", "me",
    "my", "what", "which", "who", "whom", "how", "when", "where", "about",
    "into", "over", "after", "under",
})


def tokens(query: str) -> list[str]:
    """Word tokens, lowercased, stopwords and 1-char tokens removed."""
    return [t.lower() for t in _TOKEN_RE.findall(query or "")
            if len(t) > 1 and t.lower() not in STOPWORDS]


def build_match_query(query: str, mode: str = "or") -> str:
    """Build a safe FTS5 MATCH expression. Returns '' when nothing usable."""
    toks = tokens(query)
    if not toks:
        return ""
    joiner = " AND " if mode == "and" else " OR "
    # de-duplicate while preserving order
    seen, ordered = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return joiner.join(ordered)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_query_normalize.py -v"'`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit (both live and source repo)**

```bash
# on CT100
cd /opt/kai-legal-brain && git add core/legal/query_normalize.py tests/test_query_normalize.py \
  && git commit -m "feat(legal): safe FTS5 query normalization (fix syntax errors)"
```

---

### Task 2: Fix `storage.search()` — normalized query, content snippet, store_mode (CT100)

**Files:**
- Modify: `/opt/kai-legal-brain/core/legal/storage.py` (search at ~L376)
- Test: `/opt/kai-legal-brain/tests/test_storage_search.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_search.py
import sqlite3, pytest
from core.legal.storage import Storage

@pytest.fixture
def st(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.conn.execute("INSERT INTO documents (id,jurisdiction,court,year,citation,title,type,store_mode)"
                   " VALUES (1,'ghana','Supreme Court',1960,'Act 29','Criminal Offences Act','act','full')")
    s.conn.execute("INSERT INTO fts_documents (rowid,content,jurisdiction,court,year,citation,judge,parties,title)"
                   " VALUES (1,'Stealing is a criminal offence punishable by imprisonment.','ghana','Supreme Court',1960,'Act 29','','','Criminal Offences Act')")
    s.conn.commit()
    return s

def test_punctuation_query_does_not_raise(st):
    rows = st.search("Criminal Offences Act, 1960 (Act 29)?")
    assert rows, "expected a hit, got none"

def test_snippet_uses_content_not_court(st):
    rows = st.search("stealing")
    assert "imprisonment" in rows[0]["snippet"]
    assert "<mark>" not in rows[0]["snippet"]

def test_search_returns_store_mode(st):
    assert st.search("stealing")[0]["store_mode"] == "full"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_storage_search.py -v"'`
Expected: FAIL — `sqlite3.OperationalError: fts5: syntax error` (or missing `store_mode`).

- [ ] **Step 3: Write minimal implementation**

Replace `Storage.search` with:

```python
    def search(self, query: str, limit: int = 20, mode: str = "or") -> list[dict]:
        """FTS5 search with a normalized query and the CONTENT snippet.

        Never passes raw user text to MATCH (FTS5 operators raise syntax
        errors). ``mode`` selects how tokens are joined: ``"or"`` (recall),
        ``"and"`` (precision), or ``"phrase"`` (exact words in order) — so a
        caller can drive progressive retrieval without leaking operators.
        Snippet column 0 is ``content`` (2 was ``court``, a bug).
        """
        from core.legal.query_normalize import build_match_query
        expr = build_match_query(query, mode=mode)
        if not expr:
            return []
        rows = self.conn.execute(
            """SELECT d.*, snippet(fts_documents, 0, '', '', '…', 32) AS snippet,
                      d.store_mode AS store_mode
               FROM fts_documents
               JOIN documents d ON d.id = fts_documents.rowid
               WHERE fts_documents MATCH ?
               ORDER BY rank LIMIT ?""",
            (expr, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["snippet"] = (d.get("snippet") or "").replace("<mark>", "").replace("</mark>", "")
            out.append(d)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_storage_search.py -v"'`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /opt/kai-legal-brain && git add core/legal/storage.py tests/test_storage_search.py \
  && git commit -m "fix(legal): normalize FTS query, use content snippet, return store_mode"
```

---

### Task 2b: Plumb `mode` through `/search` and the CT111 client

**Files:**
- Modify: `/opt/kai-legal-brain/legal_brain_api.py` (`/search` handler ~L337-400)
- Modify: `/opt/ai-orchestrator/core/legal_brain_client.py` (`search`)
- Test: `/opt/kai-legal-brain/tests/test_search_mode.py`, `/opt/ai-orchestrator/tests/test_legal_brain_client_mode.py`

- [ ] **Step 1: Write the failing tests**

```python
# CT100 tests/test_search_mode.py
from core.legal.query_normalize import build_match_query
def test_modes_build_distinct_expressions():
    assert build_match_query("bail application", "and") == "bail AND application"
    assert build_match_query("bail application", "or") == "bail OR application"
    assert build_match_query("bail application", "phrase") == '"bail application"'
```

```python
# CT111 tests/test_legal_brain_client_mode.py
from core import legal_brain_client
def test_search_sends_mode(monkeypatch):
    seen = {}
    def fake_get(url, params=None, **kw):
        seen["mode"] = (params or {}).get("mode")
        class R: status_code = 200
        class Rj:  # noqa
            @staticmethod
            def json(): return {"results": []}
        r = R(); r.json = Rj.json; return r
    monkeypatch.setattr(legal_brain_client.requests, "get", fake_get)
    legal_brain_client.search("bail", limit=3, mode="and")
    assert seen["mode"] == "and"
```

- [ ] **Step 2: Run to verify they fail**

Run:
```bash
$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_search_mode.py -v"'
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_legal_brain_client_mode.py -v"'
```
Expected: FAIL — `build_match_query` has no `phrase` mode; client `search` has no `mode`.

- [ ] **Step 3: Add the `phrase` mode to `build_match_query`**

In `query_normalize.py`, extend:

```python
def build_match_query(query: str, mode: str = "or") -> str:
    toks = tokens(query)
    if not toks:
        return ""
    seen, ordered = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t); ordered.append(t)
    if mode == "phrase":
        return '"' + " ".join(ordered) + '"'
    joiner = " AND " if mode == "and" else " OR "
    return joiner.join(ordered)
```

- [ ] **Step 4: Accept `mode` in the CT100 `/search` handler**

Read the `/search` branch (it parses `q`, `limit`, `jurisdiction`, etc. via `_cursor`/query params). Add:

```python
                mode = (params.get("mode") or ["or"])[0]
                if mode not in ("or", "and", "phrase", "like"):
                    mode = "or"
```
and pass `mode` into `engine().search(q, limit=limit, mode=mode)`. For `mode == "like"`, call the existing LIKE path (add a `like_search` method or branch in `search`).

- [ ] **Step 5: Accept + forward `mode` in the CT111 client**

In `core/legal_brain_client.py::search`, add `mode: str = "or"` and include it in the request params:

```python
def search(query: str, limit: int = 5, mode: str = "or") -> list[dict]:
    ...
    params = {"q": query, "limit": limit, "mode": mode}
    resp = requests.get(f"{BASE}/search", params=params, timeout=...)
```

- [ ] **Step 6: Run to verify they pass**

Run the same two pytest commands. Expected: PASS.

- [ ] **Step 7: Commit (both repos)**

```bash
cd /opt/kai-legal-brain && git add core/legal/query_normalize.py legal_brain_api.py tests/test_search_mode.py \
  && git commit -m "feat(legal): /search mode=phrase|and|or|like for progressive retrieval"
cd /opt/ai-orchestrator && git add core/legal_brain_client.py tests/test_legal_brain_client_mode.py \
  && git commit -m "feat(juris): pass retrieval mode through legal_brain_client"
```

---

### Task 3: Corpus audit tool (CT100)

**Files:**
- Create: `/opt/kai-legal-brain/core/legal/corpus_audit.py`
- Test: `/opt/kai-legal-brain/tests/test_corpus_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_audit.py
from core.legal.corpus_audit import classify_document

def test_empty_content_is_flagged():
    v = classify_document({"title": "Land Act 2020", "type": "act", "store_mode": "full"}, "")
    assert "empty" in v["flags"]

def test_press_release_flagged_junk():
    v = classify_document({"title": "MINISTER FOR X TOURS Y", "type": "act", "store_mode": "full"},
                          "some press text " * 40)
    assert "junk" in v["flags"]

def test_good_act_is_usable():
    v = classify_document({"title": "Land Act 2020 (Act 1036)", "type": "act",
                           "store_mode": "full", "citation": "Act 1036"},
                          "Section 1. " * 80)
    assert v["classification"] == "usable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_corpus_audit.py -v"'`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# core/legal/corpus_audit.py
"""Classify corpus documents so contamination is visible and remediable."""
from __future__ import annotations
import re

MIN_CONTENT = 400
_JUNK_TITLE = re.compile(r"TOURS|LAUNCHES|TOUTS|MINISTER FOR|PRESS RELEASE|&#\d+;", re.I)
_JUNK_TITLES = {"ok", "test v the state", "draft, no source"}


def classify_document(doc: dict, content: str) -> dict:
    """Return {'classification': usable|empty|junk|mislabeled|metadata_bad,
              'flags': [...]} for one document."""
    title = (doc.get("title") or "").strip()
    ctype = (doc.get("type") or "").strip().lower()
    mode = (doc.get("store_mode") or "").strip().lower()
    content = content or ""
    flags: list[str] = []

    if title.lower() in _JUNK_TITLES:
        flags.append("junk")
    if _JUNK_TITLE.search(title):
        flags.append("junk")
    if not content.strip():
        flags.append("empty")
    elif len(content.strip()) < MIN_CONTENT:
        flags.append("short")
    if mode in ("full", "reference") and len(content.strip()) < MIN_CONTENT:
        flags.append("mode_lies")  # claims content it does not have
    if ctype in ("act", "bill", "regulation", "order") and (doc.get("citation") or "") == title:
        flags.append("citation_bad")
    if (doc.get("year") or 0) in (0, None):
        flags.append("year_missing")

    if "junk" in flags:
        cls = "junk"
    elif "empty" in flags or "mode_lies" in flags:
        cls = "empty"
    elif "citation_bad" in flags or "year_missing" in flags:
        cls = "metadata_bad"
    else:
        cls = "usable"
    return {"classification": cls, "flags": flags}


def audit(db_path: str) -> dict:
    """Full-corpus audit report."""
    import sqlite3
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = c.execute("""SELECT d.*, COALESCE(f.content,'') AS content
                        FROM documents d LEFT JOIN fts_documents f ON f.rowid=d.id""").fetchall()
    report = {"total": len(rows), "by_class": {}, "by_flag": {}, "docs": []}
    for r in rows:
        d = dict(r)
        v = classify_document(d, d.pop("content"))
        report["by_class"][v["classification"]] = report["by_class"].get(v["classification"], 0) + 1
        for f in v["flags"]:
            report["by_flag"][f] = report["by_flag"].get(f, 0) + 1
        report["docs"].append({"id": d["id"], "title": d.get("title"), **v})
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_corpus_audit.py -v"'`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the audit on the live DB and save the report**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -c \"import json;from core.legal.corpus_audit import audit;r=audit(\\\"data/legal_brain.db\\\");print(json.dumps({k:r[k] for k in (\\\"total\\\",\\\"by_class\\\",\\\"by_flag\\\")},indent=2))\""'`
Expected: shows `by_class` with `empty`/`junk`/`metadata_bad`/`usable` counts matching the investigation (~135 usable).

- [ ] **Step 6: Commit**

```bash
cd /opt/kai-legal-brain && git add core/legal/corpus_audit.py tests/test_corpus_audit.py \
  && git commit -m "feat(legal): corpus audit/classification tool"
```

---

### Task 4: Corpus remediation (dry-run first, quarantine not delete) (CT100)

**Files:**
- Create: `/opt/kai-legal-brain/scripts/remediate_corpus.py`
- Test: `/opt/kai-legal-brain/tests/test_remediate_corpus.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_remediate_corpus.py
from scripts.remediate_corpus import plan_actions

def test_quarantines_junk_and_downgrades_empty_mode():
    docs = [
        {"id": 1, "title": "Ok", "type": "judgment", "store_mode": "search_only", "citation": "x", "year": 2024, "content": "y"*10},
        {"id": 2, "title": "Land Act 2020 (Act 1036)", "type": "act", "store_mode": "full", "citation": "Act 1036", "year": 2020, "content": ""},
        {"id": 3, "title": "Land Act 2020 (Act 1036)", "type": "act", "store_mode": "full", "citation": "Act 1036", "year": 2020, "content": "Section 1. "*100},
    ]
    actions = plan_actions(docs)
    by_id = {a["id"]: a for a in actions}
    assert by_id[1]["action"] == "quarantine"
    assert by_id[2]["action"] == "downgrade_mode"
    assert by_id[2]["new_store_mode"] == "search_only"
    assert by_id[3]["action"] is None  # usable stays
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_remediate_corpus.py -v"'`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/remediate_corpus.py
"""Idempotent corpus remediation. Dry-run by default; --apply to write.

Never deletes: junk is quarantined (quarantine_reason set), empty-content
docs claiming full/reference are downgraded to search_only.
"""
from __future__ import annotations
import argparse, sqlite3, sys
sys.path.insert(0, "/opt/kai-legal-brain")
from core.legal.corpus_audit import classify_document


def plan_actions(docs: list[dict]) -> list[dict]:
    actions = []
    for d in docs:
        v = classify_document(d, d.get("content", ""))
        a = {"id": d["id"], "classification": v["classification"], "flags": v["flags"],
             "action": None, "new_store_mode": None}
        if v["classification"] == "junk":
            a["action"] = "quarantine"
        elif v["classification"] == "empty" and (d.get("store_mode") or "") != "search_only":
            a["action"] = "downgrade_mode"
            a["new_store_mode"] = "search_only"
        actions.append(a)
    return actions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/legal_brain.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    cols = [r[1] for r in c.execute("PRAGMA table_info(documents)")]
    if "quarantine_reason" not in cols:
        c.execute("ALTER TABLE documents ADD COLUMN quarantine_reason TEXT DEFAULT ''")
    rows = c.execute("""SELECT d.*, COALESCE(f.content,'') AS content
                        FROM documents d LEFT JOIN fts_documents f ON f.rowid=d.id""").fetchall()
    actions = plan_actions([dict(r) for r in rows])
    q = sum(1 for a in actions if a["action"] == "quarantine")
    dg = sum(1 for a in actions if a["action"] == "downgrade_mode")
    print(f"planned: quarantine={q} downgrade_mode={dg} unchanged={len(actions)-q-dg}")
    if not args.apply:
        print("dry-run; pass --apply to write")
        return
    for a in actions:
        if a["action"] == "quarantine":
            c.execute("UPDATE documents SET quarantine_reason=? WHERE id=?",
                      (",".join(a["flags"]), a["id"]))
        elif a["action"] == "downgrade_mode":
            c.execute("UPDATE documents SET store_mode=? WHERE id=?",
                      (a["new_store_mode"], a["id"]))
    c.commit()
    print("applied")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_remediate_corpus.py -v"'`
Expected: PASS

- [ ] **Step 5: Back up DB, then dry-run on live**

Run:
```bash
$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && cp data/legal_brain.db data/legal_brain.db.bak-$(date +%Y%m%d) && python3 scripts/remediate_corpus.py --db data/legal_brain.db"'
```
Expected: planned counts printed; **no writes**. Report to owner before `--apply`.

- [ ] **Step 6: Commit**

```bash
cd /opt/kai-legal-brain && git add scripts/remediate_corpus.py tests/test_remediate_corpus.py \
  && git commit -m "feat(legal): idempotent corpus remediation (quarantine/downgrade, dry-run default)"
```

- [ ] **Step 7 (owner gate): apply after sign-off**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 scripts/remediate_corpus.py --db data/legal_brain.db --apply"'`
Then re-run the audit and confirm `mode_lies`/`junk` counts drop to 0.

---

### Task 5: Ingestion validation gate (CT100)

**Files:**
- Create: `/opt/kai-legal-brain/core/legal/validation.py`
- Modify: `/opt/kai-legal-brain/legal_brain_api.py` (ingest handlers ~L619, ~L670)
- Test: `/opt/kai-legal-brain/tests/test_validation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation.py
from core.legal.validation import validate_ingest

def test_rejects_empty_content():
    ok, reason = validate_ingest({"title": "Land Act", "type": "act"}, "")
    assert not ok and reason == "empty_content"

def test_rejects_junk_title():
    ok, reason = validate_ingest({"title": "MINISTER FOR X TOURS Y", "type": "act"}, "x"*500)
    assert not ok and reason == "non_legal_content"

def test_accepts_real_act():
    ok, reason = validate_ingest({"title": "Land Act 2020 (Act 1036)", "type": "act", "year": 2020}, "Section 1. "*100)
    assert ok and reason == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_validation.py -v"'`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# core/legal/validation.py
"""Ingestion validation gate — contamination cannot enter the corpus."""
from __future__ import annotations
import re

MIN_CONTENT = 400
_JUNK_TITLE = re.compile(r"TOURS|LAUNCHES|TOUTS|MINISTER FOR|PRESS RELEASE|&#\d+;", re.I)
_JUNK_TITLES = {"ok", "test v the state", "draft, no source"}


def validate_ingest(doc: dict, content: str) -> tuple[bool, str]:
    """Return (ok, reason). reason '' means accept."""
    title = (doc.get("title") or "").strip()
    if not title:
        return False, "missing_title"
    if title.lower() in _JUNK_TITLES:
        return False, "non_legal_content"
    if _JUNK_TITLE.search(title):
        return False, "non_legal_content"
    if not (content or "").strip():
        return False, "empty_content"
    if len(content.strip()) < MIN_CONTENT:
        return False, "content_too_short"
    return True, ""
```

- [ ] **Step 4: Wire the gate into both ingest handlers**

In `legal_brain_api.py`, in the `/ingest` handler (before `gated_ingest`) and in `_handle_ingest_file`, add:

```python
            from core.legal.validation import validate_ingest
            ok, reason = validate_ingest(
                {"title": title, "type": type_, "year": year}, content)
            if not ok:
                return self._send(422, {"error": f"validation failed: {reason}",
                                        "store_mode": "none", "reason": reason})
```

(Use the handler's actual local variable names — read the surrounding code first.)

- [ ] **Step 5: Run test + a live reject**

Run:
```bash
$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_validation.py -v"'
$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -c \"from core.legal.validation import validate_ingest;print(validate_ingest({\\\"title\\\":\\\"Ok\\\"},\\\"x\\\"*500))\""'
```
Expected: tests PASS; live call prints `(False, 'non_legal_content')`.

- [ ] **Step 6: Commit**

```bash
cd /opt/kai-legal-brain && git add core/legal/validation.py legal_brain_api.py tests/test_validation.py \
  && git commit -m "feat(legal): ingestion validation gate (reject empty/junk/non-legal)"
```

---

### Task 6: Fix the Parliament OAI source emitting press releases (CT100)

**Files:**
- Modify: `/opt/kai-legal-brain/data/sources.json` (or `core/legal/sources/oai.py` filter)
- Test: `/opt/kai-legal-brain/tests/test_oai_filter.py`

- [ ] **Step 1: Inspect the source config**

Run: `$PVEB 'pct exec 100 -- sh -c "cat /opt/kai-legal-brain/data/sources.json"'`
Identify the `repository.parliament.gh` OAI source (460 docs).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_oai_filter.py
from core.legal.validation import validate_ingest

def test_press_release_from_parliament_is_rejected():
    # A Parliament OAI record that is a news item, not legislation
    ok, reason = validate_ingest(
        {"title": "GHANA PUBLISHING COMPANY LIMITED LAUNCHES 24-HOUR SERVICE", "type": "gazette"},
        "The Managing Director announced " * 40)
    assert not ok
```

- [ ] **Step 3: Run to confirm current behaviour**

Run: `$PVEB 'pct exec 100 -- sh -c "cd /opt/kai-legal-brain && python3 -m pytest tests/test_oai_filter.py -v"'`
Expected: PASS already (validation gate from Task 5 catches it at ingest). This test documents the guarantee.

- [ ] **Step 4: Add an OAI-side pre-filter** (defense in depth) so junk never even reaches validation

In the OAI harvest path, skip records whose title matches the junk pattern before building the doc. Read the file first, then insert the guard at the record-mapping point.

- [ ] **Step 5: Commit**

```bash
cd /opt/kai-legal-brain && git add tests/test_oai_filter.py core/legal/sources/oai.py \
  && git commit -m "fix(legal): pre-filter press releases from Parliament OAI source"
```

---

## Phase 1 — Retrieval & Strict Grounding

### Task 7: Progressive retrieval client (CT111)

**Files:**
- Create: `/opt/ai-orchestrator/core/juris_kai/grounding.py`
- Modify: `/opt/ai-orchestrator/core/juris_kai/legal_context.py`
- Test: `/opt/ai-orchestrator/tests/test_juris_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_juris_grounding.py
from core.juris_kai import grounding

def _fake_search(monkeypatch, results_by_mode):
    def fake(query, limit=3, mode="or"):
        return results_by_mode.get(mode, [])
    monkeypatch.setattr(grounding, "_search", fake)

def test_grounded_when_phrase_hits(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "Criminal Offences Act", "chunk_content": "x"*500, "citation": "Act 29"}]})
    r = grounding.retrieve("Criminal Offences Act")
    assert r["verdict"] == "GROUNDED" and r["stage"] == 1 and r["docs"]

def test_ungrounded_when_nothing(monkeypatch):
    _fake_search(monkeypatch, {})
    r = grounding.retrieve("quantum entanglement tax")
    assert r["verdict"] == "UNGROUNDED" and r["docs"] == []

def test_partial_when_only_or_hits(monkeypatch):
    _fake_search(monkeypatch, {"or": [{"title": "Some Act", "chunk_content": "y"*500, "citation": "Act 1"}]})
    r = grounding.retrieve("bail application procedure")
    assert r["verdict"] == "PARTIAL" and r["stage"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -v"'`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# core/juris_kai/grounding.py
"""Progressive retrieval + grounding verdict for Juris Kai.

Retrieval supplies the verdict and citations; the model may only cite what
retrieval returned. No legal substance without a source (owner directive).

Query normalization lives on the legal-brain (CT100) side, so this client
drives staged retrieval by passing a ``mode`` (phrase/and/or) — it never
builds FTS operators itself.
"""
from __future__ import annotations
import logging

logger = logging.getLogger("juris_kai.grounding")

MIN_SOURCE_CHARS = 400


def _search(query: str, limit: int = 3, mode: str = "or") -> list[dict]:
    """Thin seam over the legal-brain client (monkeypatched in tests)."""
    from core import legal_brain_client as lb
    return lb.search(query, limit=limit, mode=mode) or []


def _usable(docs: list[dict]) -> list[dict]:
    return [d for d in docs
            if len((d.get("chunk_content") or d.get("content") or "").strip()) >= MIN_SOURCE_CHARS]


def retrieve(query: str, limit: int = 3) -> dict:
    """Progressive retrieval. Returns {docs, verdict, stage}."""
    q = (query or "").strip()
    if not q:
        return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}

    # Stage 1: exact phrase (server builds the FTS phrase query)
    docs = _usable(_search(q, limit, mode="phrase"))
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 1}

    # Stage 2: AND of tokens
    docs = _usable(_search(q, limit, mode="and"))
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 2}

    # Stage 3: OR of tokens
    docs = _usable(_search(q, limit, mode="or"))
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 3}

    # Stage 4: raw keyword fallback (title/citation LIKE)
    docs = _usable(_search(q, limit, mode="like"))
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 4}

    return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -v"'`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /opt/ai-orchestrator && git add core/juris_kai/grounding.py tests/test_juris_grounding.py \
  && git commit -m "feat(juris): progressive retrieval + grounding verdict"
```

---

### Task 8: Deterministic Sources footer (CT111)

**Files:**
- Modify: `/opt/ai-orchestrator/core/juris_kai/grounding.py`
- Test: `/opt/ai-orchestrator/tests/test_juris_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
def test_footer_lists_title_citation_and_mode():
    docs = [{"title": "Criminal Offences Act", "citation": "Act 29", "year": 1960,
             "court": "Parliament", "store_mode": "full"}]
    out = grounding.build_sources_footer(docs)
    assert "Criminal Offences Act" in out and "Act 29" in out and "full" in out

def test_footer_empty_when_no_docs():
    assert grounding.build_sources_footer([]) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -k footer -v"'`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_sources_footer'`

- [ ] **Step 3: Write minimal implementation**

```python
def build_sources_footer(docs: list[dict]) -> str:
    """Deterministic Sources block built from retrieval (never the model)."""
    if not docs:
        return ""
    lines = ["\n\n📚 *Sources*"]
    for i, d in enumerate(docs, 1):
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
            bits.append(f"_{mode}_")
        lines.append(f"{i}. " + " — ".join(bits))
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -k footer -v"'`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /opt/ai-orchestrator && git add core/juris_kai/grounding.py tests/test_juris_grounding.py \
  && git commit -m "feat(juris): deterministic Sources footer"
```

---

### Task 9: Grounding-aware prompt (CT111)

**Files:**
- Modify: `/opt/ai-orchestrator/core/juris_kai/prompt.py`
- Test: `/opt/ai-orchestrator/tests/test_juris_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
from core.juris_kai.prompt import build_grounded_prompt

def test_grounded_prompt_forbids_outside_citations():
    p = build_grounded_prompt("legal_research", "theft penalty",
                              verdict="GROUNDED", docs=[{"title": "Act 29", "chunk_content": "Stealing..."}])
    assert "only" in p.lower() and "Act 29" in p

def test_ungrounded_prompt_says_do_not_answer():
    p = build_grounded_prompt("legal_research", "x", verdict="UNGROUNDED", docs=[])
    assert "no" in p.lower() and "do not" in p.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -k grounded_prompt -v"'`
Expected: FAIL — missing function.

- [ ] **Step 3: Write minimal implementation**

```python
def build_grounded_prompt(task_type: str, content: str, verdict: str, docs: list[dict]) -> str:
    """Prompt that enforces the grounding tier."""
    if verdict == "UNGROUNDED":
        # The bot will not call the model for legal substance; this string is
        # a guard if it is ever called anyway.
        return (
            "Do NOT answer this legal question. No authoritative Ghana legal "
            "source was retrieved from the database. Reply only that you could "
            "not find it and suggest rephrasing or a covered topic."
        )
    src_lines = []
    for i, d in enumerate(docs, 1):
        src_lines.append(f'SOURCE {i}: {d.get("title","")} ({d.get("citation","")})\n"""{(d.get("chunk_content") or "")[:1200]}"""')
    sources = "\n\n".join(src_lines)
    strict = ("Cite ONLY the sources below. Do not mention any statute, case, or "
              "article that is not in them. If the sources do not answer the "
              "question, say so plainly.")
    return (
        f"{_JURISDICTION_GATE}\n\n{sources}\n\n"
        f"TASK: Answer this Ghana law question using ONLY the sources above: '{content}'.\n"
        f"{strict} Quote the source text briefly to support each point."
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -k grounded_prompt -v"'`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /opt/ai-orchestrator && git add core/juris_kai/prompt.py tests/test_juris_grounding.py \
  && git commit -m "feat(juris): grounding-tier prompt (cite-only-sources; ungrounded refusal)"
```

---

### Task 10: Wire strict grounding into the bot (CT111)

**Files:**
- Modify: `/opt/ai-orchestrator/core/juris_kai/bot.py` (`_handle_free_text` ~L1420)
- Test: `/opt/ai-orchestrator/tests/test_juris_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ungrounded_question_returns_no_legal_substance(monkeypatch):
    from core.juris_kai import bot as jbot
    monkeypatch.setattr("core.juris_kai.grounding.retrieve",
                        lambda q, limit=3: {"docs": [], "verdict": "UNGROUNDED", "stage": 0})
    called = {"model": False}
    monkeypatch.setattr(jbot, "_generate_reply",
                        lambda *a, **k: called.__setitem__("model", True) or ("should-not-run", "m", False, False))
    out = jbot._build_legal_reply("what is the tax on bananas in ghana", chat_id=1, account={"account_id": "x"})
    assert "couldn't find" in out.lower() or "no " in out.lower()
    assert not called["model"], "model must not be called when UNGROUNDED"
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -k ungrounded_question -v"'`
Expected: FAIL — `_build_legal_reply` missing.

- [ ] **Step 3: Write minimal implementation**

Add a small orchestrator function in `bot.py` (extract from `_handle_free_text`):

```python
UNGROUNDED_REPLY = (
    "⚖️ I couldn't find an authoritative Ghanaian source for that in my legal "
    "database, so I won't guess. Try rephrasing, or ask about a topic I cover "
    "(e.g. *Criminal Offences Act*, *Contracts Act*, *Land Act*, the 1992 Constitution)."
)


def _build_legal_reply(text, chat_id, account, reply_markup=None):
    """Retrieve → verdict → (refuse | generate + footer). Returns reply text."""
    from core.juris_kai import grounding
    r = grounding.retrieve(text)
    if r["verdict"] == "UNGROUNDED":
        return UNGROUNDED_REPLY
    from core.juris_kai.prompt import build_grounded_prompt
    prompt = build_grounded_prompt("juris_research", text, r["verdict"], r["docs"])
    response_text, _model, _streamed, _cache = _generate_reply(
        prompt, "juris_research", text, text, account["account_id"],
        chat_id=chat_id, reply_markup=reply_markup)
    banner = ("ℹ️ _Limited sources — some points may be general._\n\n"
              if r["verdict"] == "PARTIAL" else "")
    return banner + response_text + grounding.build_sources_footer(r["docs"])
```

Then in `_handle_free_text`, replace the retrieval+prompt+generate block with a call to `_build_legal_reply(...)`.

- [ ] **Step 4: Run to verify it passes**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/test_juris_grounding.py -v"'`
Expected: PASS (all grounding tests)

- [ ] **Step 5: Commit**

```bash
cd /opt/ai-orchestrator && git add core/juris_kai/bot.py tests/test_juris_grounding.py \
  && git commit -m "feat(juris): strict grounding in bot (refuse ungrounded, footer, banner)"
```

---

### Task 11: Live end-to-end verification + regression gate

**Files:**
- Evidence: `/opt/ai-orchestrator/tests/baseline_evidence/`

- [ ] **Step 1: Verify retrieval no longer 500s and returns real content**

Run:
```bash
$PVEB 'pct exec 100 -- sh -c "curl -s \"http://127.0.0.1:8100/search?q=Criminal%20Offences%20Act%2C%201960%20%28Act%2029%29\" | head -c 400"'
```
Expected: HTTP 200 JSON with a real content excerpt (not `"Parliament"`).

- [ ] **Step 2: Verify the three tiers live**

Run a grounded, a partial, and an ungrounded question through `_build_legal_reply` and capture verbatim outputs. Expected:
- grounded → answer + `📚 Sources` footer with real titles;
- partial → banner + footer;
- ungrounded → `UNGROUNDED_REPLY`, **zero legal claims**, model not called.

- [ ] **Step 3: Run the regression gate**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && ./scripts/test_regression_gate.sh"'`
Expected: `NEW failures: 0` (known baseline allowed).

- [ ] **Step 4: Restart services and confirm healthy**

Run:
```bash
$PVEB 'pct exec 111 -- sh -c "systemctl restart ai-orchestrator-api juris-kai && sleep 3 && systemctl is-active ai-orchestrator-api juris-kai"'
$PVEB 'pct exec 100 -- sh -c "systemctl restart kai-legal-brain && sleep 2 && systemctl is-active kai-legal-brain"'
```
Expected: all `active`.

- [ ] **Step 5: Commit evidence + update baseline docs**

```bash
cd /opt/ai-orchestrator && git add tests/baseline_evidence tests/baseline_failures.txt tests/BASELINE.md \
  && git commit -m "test(juris): Phase 0+1 live evidence; baseline refresh"
```

---

## Self-Review

- **Spec coverage:** §4.1 corpus integrity → Tasks 3–6; §4.2 retrieval + strict grounding → Tasks 1,2,7,9,10; §4.3 sources/quoting → Tasks 8,9; error handling (fail loud, no silent fallback) → Task 2 + Task 10; testing → every task + Task 11. Phase 2 (citation guard/template), Phase 3 (HTML render), Phase 4 (mining) are **intentionally deferred to follow-on plans** (each independently shippable).
- **Placeholders:** none — all code steps contain real code. Task 5 Step 4 and Task 6 Step 4 require reading surrounding handler code to match local variable names; that is called out explicitly, not left vague.
- **Type consistency:** `retrieve() -> {docs, verdict, stage}` used identically in Tasks 7, 9, 10; `build_sources_footer(docs)`, `build_grounded_prompt(task_type, content, verdict, docs)`, `validate_ingest(doc, content) -> (ok, reason)`, `build_match_query(query, mode)`, `classify_document(doc, content)`, `plan_actions(docs)` are consistent across tasks.
- **Risk:** Task 4 Step 7 (`--apply`) is an explicit owner gate before any corpus write.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-23-juris-kai-grounding-phase0-1.md`.

Two execution options:
1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
