"""KAI Media Revenue Factory — Postgres layer (psycopg2 + numbered SQL migrations).

Follows the repo's ad-hoc migration pattern: numbered ``.sql`` files under
``migrations/`` applied in order and recorded in ``schema_migrations``. The
database (``kai_media``) is created on first migrate by connecting to the
``postgres`` maintenance database.

Reuses existing KAI infra: the kai-vault machine plane for the DB password
(``core.ai.kai_vault_client``) and the core event bus for audit fan-out
(``core.kai_event_bus``). Every connection is lazy — importing this module
never touches the database.
"""
from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from core.media_factory import config

logger = logging.getLogger(__name__)

_local = threading.local()
_password_cache: Optional[str] = None
_KNOWN_APPLIED_COLUMNS = ("version", "filename", "applied_at")

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── Credentials ────────────────────────────────────────────────────────────
def resolve_password(force: bool = False) -> str:
    """Vault-first password resolution with an env / documented fallback.

    Never logs the value. The vault being unreachable must not block work.
    """
    global _password_cache
    if _password_cache is not None and not force:
        return _password_cache
    if config.DB_PASSWORD_ENV:
        _password_cache = config.DB_PASSWORD_ENV
        return _password_cache
    try:
        from core.ai.kai_vault_client import fetch_secret, load_token

        token = load_token()
        if token:
            value = fetch_secret(config.VAULT_SECRET_PATH, token)
            if value:
                _password_cache = value
                return _password_cache
    except Exception as exc:  # noqa: BLE001 - vault is best-effort
        logger.warning("kai-vault DB credential lookup failed (%s)", type(exc).__name__)
    _password_cache = config.DB_PASSWORD_FALLBACK
    return _password_cache


def _new_conn(database: str | None = None):
    cfg = config.db_config(database)
    cfg["password"] = resolve_password()
    # CT111's cluster defaults client_encoding to SQL_ASCII under a C locale;
    # force UTF-8 so model output / migrated SQL with non-ASCII is safe.
    cfg["client_encoding"] = "UTF8"
    return psycopg2.connect(**cfg)


def get_conn(database: str | None = None):
    """Thread-local, auto-reconnecting connection for *database*."""
    db = database or config.DB_NAME
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns
    conn = conns.get(db)
    if conn is None or conn.closed:
        conn = _new_conn(db)
        conns[db] = conn
    return conn


def close_all() -> None:
    """Close every thread-local connection (used by tests / shutdown)."""
    conns = getattr(_local, "conns", None) or {}
    for conn in list(conns.values()):
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    conns.clear()


@contextmanager
def cursor(database: str | None = None, commit: bool = True):
    conn = get_conn(database)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ── Query helpers ──────────────────────────────────────────────────────────
def query(sql: str, params: Iterable[Any] = (), database: str | None = None) -> list[dict]:
    with cursor(database) as cur:
        cur.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params: Iterable[Any] = (), database: str | None = None) -> Optional[dict]:
    rows = query(sql, params, database)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = (), database: str | None = None) -> int:
    """Run a statement; returns affected rowcount."""
    with cursor(database) as cur:
        cur.execute(sql, tuple(params))
        return cur.rowcount


def insert_returning(sql: str, params: Iterable[Any] = (), database: str | None = None) -> Optional[dict]:
    with cursor(database) as cur:
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        if row is None:
            return None
        return {k: v for k, v in dict(row).items()}


def jsonb(value: Any) -> Json:
    return Json(value or {})


def count(table: str) -> int:
    if not _TABLE_RE.match(table):
        raise ValueError(f"invalid table name: {table!r}")
    row = query_one(f"SELECT count(*) AS n FROM {table}")
    return int(row["n"]) if row else 0


# ── Migrations ─────────────────────────────────────────────────────────────
def database_exists() -> bool:
    row = query_one(
        "SELECT 1 AS ok FROM pg_database WHERE datname = %s",
        (config.DB_NAME,),
        database=config.DB_MAINTENANCE_NAME,
    )
    return row is not None


def create_database() -> bool:
    """Create the media database if absent. Returns True if it was created."""
    if not _TABLE_RE.match(config.DB_NAME):
        raise ValueError(f"invalid database name: {config.DB_NAME!r}")
    if database_exists():
        return False
    conn = _new_conn(config.DB_MAINTENANCE_NAME)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # UTF8 explicitly: this cluster's templates are SQL_ASCII under a C
            # locale, which cannot store non-ASCII model output / JSON escapes.
            cur.execute(
                f'CREATE DATABASE "{config.DB_NAME}" '
                "ENCODING 'UTF8' TEMPLATE template0"
            )
    finally:
        conn.close()
    logger.info("created database %s", config.DB_NAME)
    return True


def _migration_files() -> list[Path]:
    files = []
    for path in config.MIGRATIONS_DIR.glob("*.sql"):
        if re.match(r"^\d+", path.name):
            files.append(path)
    return sorted(files, key=lambda p: int(p.name.split("_", 1)[0]))


def migrate() -> dict:
    """Create the DB if needed and apply any unapplied numbered migrations."""
    created_db = create_database()
    applied: list[str] = []
    already_applied = 0
    with cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                filename   TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in cur.fetchall()}
        for path in _migration_files():
            version = path.name.split("_", 1)[0]
            if version in done:
                already_applied += 1
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)",
                (version, path.name),
            )
            applied.append(path.name)
            logger.info("applied migration %s", path.name)
    return {
        "database": config.DB_NAME,
        "created_database": created_db,
        "applied": applied,
        "already_applied": already_applied,
    }


# ── Audit + pipeline events ────────────────────────────────────────────────
def audit(
    event_type: str,
    *,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    actor: str = "system",
    payload: Optional[dict] = None,
    source: str = "media_factory",
) -> dict:
    """Append to the immutable audit ledger and fan out on the event bus.

    Fail-safe: a DB outage must not crash the caller, so the result is
    reported rather than raised.
    """
    payload = payload or {}
    event_id = None
    db_ok = True
    try:
        row = insert_returning(
            """
            INSERT INTO media_audit_events
                (event_type, entity_type, entity_id, actor, source, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (event_type, entity_type, entity_id, actor, source, jsonb(payload)),
        )
        event_id = row["id"] if row else None
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        logger.warning("audit write failed (%s)", type(exc).__name__)
    try:
        from core.kai_event_bus import publish

        publish(
            f"media.{event_type}",
            {
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "actor": actor,
                "payload": payload,
            },
            source="media_factory",
            severity="informational",
            journal=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit event-bus publish failed (%s)", type(exc).__name__)
    return {"ok": db_ok, "id": event_id}


def record_event(
    stage: str,
    status: str,
    *,
    cycle_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> Optional[int]:
    try:
        row = insert_returning(
            """
            INSERT INTO media_events (cycle_id, stage, status, detail)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (cycle_id, stage, status, jsonb(detail or {})),
        )
        return row["id"] if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("media_event write failed (%s)", type(exc).__name__)
        return None


# ── Health ─────────────────────────────────────────────────────────────────
def health() -> dict:
    try:
        row = query_one("SELECT current_database() AS db, now() AS ts")
        tables = query_one(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public'"
        )
        return {
            "status": config.STATUS_VERIFIED,
            "database": row["db"],
            "public_tables": int(tables["n"]),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": config.STATUS_DEGRADED, "error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":  # pragma: no cover - manual migration entrypoint
    logging.basicConfig(level=logging.INFO)
    print(migrate())
