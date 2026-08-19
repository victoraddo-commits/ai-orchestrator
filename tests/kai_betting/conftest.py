import os
import tempfile

import pytest

from core.kai_betting import db as db_mod


@pytest.fixture
def fresh_db(monkeypatch):
    """Point core.kai_betting.db at a fresh temp SQLite file for the test's duration."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    db_mod.init_db()
    yield path
    os.unlink(path)
