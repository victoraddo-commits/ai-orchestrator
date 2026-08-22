import sqlite3


def test_fresh_db_creates_users_table(fresh_db):
    conn = sqlite3.connect(fresh_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "users" in tables
    conn.close()


def test_sessions_table_created(fresh_db):
    conn = sqlite3.connect(fresh_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sessions" in tables

    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert cols == {"id", "user_id", "token_hash", "created_at", "expires_at", "last_seen_at"}

    indexes = {r[1] for r in conn.execute("PRAGMA index_list(sessions)")}
    assert "idx_sessions_token_hash" in indexes
    conn.close()
