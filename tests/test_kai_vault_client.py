"""kai-vault bridge: add-only populate behind credential_vault's interface.
Secret values NEVER appear in assertions, logs, or failures."""
from unittest import mock

import pytest
import requests

import core.ai.kai_vault_client as kvc


def test_fetch_secret_returns_none_on_connection_error():
    with mock.patch.object(kvc.requests, "post",
                           side_effect=requests.ConnectionError("down")):
        assert kvc.fetch_secret("some/path", "tok") is None


def test_fetch_secret_none_on_non_200():
    class R:
        status_code = 403
    with mock.patch.object(kvc.requests, "post", return_value=R()):
        assert kvc.fetch_secret("some/path", "tok") is None


def test_no_token_means_no_vault_source():
    with mock.patch.dict(kvc.os.environ, {"VAULT_BEARER_TOKEN": ""}), \
         mock.patch.object(kvc.os.path, "isfile", return_value=False):
        assert kvc.load_token() is None
        assert kvc.fetch_for_provider("gpuai") is None


def test_retrieve_credential_prefers_vault_then_falls_back():
    import core.ai.credential_vault as cv
    with mock.patch.object(cv._secrets_store, "get_secret",
                           return_value={"api_key": "enc", "api_base": "https://x",
                                         "models": [], "created_at": "t"}), \
         mock.patch.object(cv, "decrypt", return_value="stored-aes-value"), \
         mock.patch.object(kvc, "fetch_for_provider", return_value="vault-value"):
        cred = cv.retrieve_credential("gpuai")
    assert cred["api_key"] == "vault-value"
    assert cred["source"] == "kai-vault"

    with mock.patch.object(cv._secrets_store, "get_secret",
                           return_value={"api_key": "enc", "api_base": "https://x",
                                         "models": [], "created_at": "t"}), \
         mock.patch.object(cv, "decrypt", return_value="stored-aes-value"), \
         mock.patch.object(kvc, "fetch_for_provider", return_value=None):
        cred = cv.retrieve_credential("gpuai")
    assert cred["api_key"] == "stored-aes-value"     # AES-GCM fallback intact
    assert cred["source"] == "local-vault"


def test_values_never_logged(caplog):
    import logging
    with mock.patch.object(kvc.requests, "post") as post:
        class R:
            status_code = 200
            def json(self):
                return {"value": "SUPERSECRETVALUE"}
        post.return_value = R()
        with caplog.at_level(logging.DEBUG):
            got = kvc.fetch_secret("p", "tok")
    assert got == "SUPERSECRETVALUE"           # returned to caller only
    assert "SUPERSECRETVALUE" not in caplog.text


def test_secret_path_convention():
    assert kvc.secret_path_for_provider("GPU.ai") == \
        "ai-orchestrator/providers/gpu_ai"
