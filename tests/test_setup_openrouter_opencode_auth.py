import json

import pytest

import scripts.setup_openrouter_opencode_auth as setup_auth


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    # A nested, not-yet-existing path so parent-dir creation is exercised.
    path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    monkeypatch.setattr(setup_auth, "AUTH_PATH", path)
    return path


def test_writes_a_new_openrouter_entry_when_auth_json_does_not_exist(auth_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    assert setup_auth.main() == 0

    auth = json.loads(auth_path.read_text())
    assert auth == {"openrouter": {"type": "api", "key": "sk-or-test"}}


def test_merges_into_an_existing_auth_json_without_disturbing_other_keys(auth_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps({"opencode": {"type": "api", "key": "sk-zen"}}))

    assert setup_auth.main() == 0

    auth = json.loads(auth_path.read_text())
    assert auth["opencode"] == {"type": "api", "key": "sk-zen"}
    assert auth["openrouter"] == {"type": "api", "key": "sk-or-test"}


def test_rerunning_is_idempotent_and_updates_the_key_in_place(auth_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-first")
    assert setup_auth.main() == 0

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-second")
    assert setup_auth.main() == 0

    auth = json.loads(auth_path.read_text())
    assert auth == {"openrouter": {"type": "api", "key": "sk-or-second"}}


def test_exits_1_without_writing_when_env_var_is_unset(auth_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert setup_auth.main() == 1
    assert not auth_path.exists()
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_refuses_to_overwrite_a_corrupt_auth_json(auth_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("{not valid json")

    assert setup_auth.main() == 1
    # The corrupt file is left untouched for a human to inspect -- never
    # partially/destructively rewritten.
    assert auth_path.read_text() == "{not valid json"
