from core.kai_betting import sessions as sessions_mod
from core.kai_betting.db import get_db


def _make_user(db, email):
    cursor = db.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, "hash"),
    )
    db.commit()
    return cursor.lastrowid


def test_create_and_resolve_session_round_trip(fresh_db):
    with get_db() as db:
        user_id = _make_user(db, "a@example.com")
        token = sessions_mod.create_session(db, user_id)
        resolved = sessions_mod.resolve_session(db, token)

    assert resolved is not None
    assert resolved["id"] == user_id
    assert resolved["email"] == "a@example.com"


def test_resolve_session_returns_none_for_unknown_token(fresh_db):
    with get_db() as db:
        resolved = sessions_mod.resolve_session(db, "not-a-real-token")
    assert resolved is None


def test_delete_session_invalidates_token(fresh_db):
    with get_db() as db:
        user_id = _make_user(db, "b@example.com")
        token = sessions_mod.create_session(db, user_id)
        sessions_mod.delete_session(db, token)
        resolved = sessions_mod.resolve_session(db, token)
    assert resolved is None


def test_expired_session_is_rejected_and_cleaned_up(fresh_db):
    with get_db() as db:
        user_id = _make_user(db, "c@example.com")
        token = sessions_mod.create_session(db, user_id)
        db.execute(
            "UPDATE sessions SET expires_at = datetime('now', '-1 day') WHERE token_hash = ?",
            (sessions_mod.hash_token(token),),
        )
        db.commit()

        resolved = sessions_mod.resolve_session(db, token)
        assert resolved is None

        remaining = db.execute("SELECT COUNT(*) as c FROM sessions").fetchone()
        assert remaining["c"] == 0


def test_hash_token_is_sha256_hex():
    import hashlib
    token = "example-token"
    assert sessions_mod.hash_token(token) == hashlib.sha256(token.encode()).hexdigest()


def test_resolve_session_returns_none_for_deactivated_user(fresh_db):
    with get_db() as db:
        user_id = _make_user(db, "d@example.com")
        token = sessions_mod.create_session(db, user_id)
        db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        db.commit()

        resolved = sessions_mod.resolve_session(db, token)
    assert resolved is None


def test_resolve_session_bumps_last_seen_at(fresh_db):
    with get_db() as db:
        user_id = _make_user(db, "e@example.com")
        token = sessions_mod.create_session(db, user_id)

        # Force created_at/last_seen_at into the past so the resolve's
        # datetime('now') update is guaranteed to differ.
        db.execute(
            "UPDATE sessions SET last_seen_at = datetime('now', '-1 hour') "
            "WHERE token_hash = ?",
            (sessions_mod.hash_token(token),),
        )
        db.commit()

        before = db.execute(
            "SELECT last_seen_at FROM sessions WHERE token_hash = ?",
            (sessions_mod.hash_token(token),),
        ).fetchone()["last_seen_at"]

        sessions_mod.resolve_session(db, token)

        after = db.execute(
            "SELECT last_seen_at FROM sessions WHERE token_hash = ?",
            (sessions_mod.hash_token(token),),
        ).fetchone()["last_seen_at"]

    assert after > before


def test_delete_session_on_unknown_token_is_a_noop(fresh_db):
    with get_db() as db:
        # Should not raise even though no session with this token exists.
        sessions_mod.delete_session(db, "never-issued-token")
