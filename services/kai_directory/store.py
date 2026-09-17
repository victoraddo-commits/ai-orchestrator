from __future__ import annotations

import dataclasses
import sqlite3
from contextlib import contextmanager
from typing import Optional

from .models import Record

_COLS = [f.name for f in dataclasses.fields(Record)]
_AUTO_SOURCES = ("registry", "docker", "ss", "cloudflared", "panel")


class RecordStore:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.execute(f"CREATE TABLE IF NOT EXISTS services ({', '.join(col + ' TEXT' for col in _COLS)}, PRIMARY KEY (id))")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def upsert(self, r: Record) -> None:
        with self._conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO services ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' for _ in _COLS)})",
                [getattr(r, col) for col in _COLS],
            )

    def get(self, id_: str) -> Optional[Record]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM services WHERE id=?", (id_,)).fetchone()
        return Record(**{k: row[k] for k in _COLS}) if row else None

    def list(self, category: str = "", q: str = "") -> list[Record]:
        sql, args = "SELECT * FROM services", []
        where = []
        if category:
            where.append("category=?"); args.append(category)
        if q:
            where.append("(name LIKE ? OR display_name LIKE ? OR tags LIKE ?)")
            args += [f"%{q}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY category, name"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [Record(**{k: r[k] for k in _COLS}) for r in rows]

    def delete(self, id_: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM services WHERE id=?", (id_,))

    def reconcile(self, discovered: list[Record]) -> dict:
        seen = {r.id for r in discovered}
        for r in discovered:
            self.upsert(r)
        stale = 0
        for existing in self.list():
            if existing.id in seen:
                continue
            if existing.source == "manual":
                continue
            if any(existing.source.startswith(s) for s in _AUTO_SOURCES) and not existing.source.endswith(":stale"):
                existing.source = existing.source + ":stale"
                self.upsert(existing)
                stale += 1
        return {"seen": len(seen), "stale": stale}
