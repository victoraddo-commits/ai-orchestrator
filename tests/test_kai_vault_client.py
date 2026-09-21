"""kai-vault bridge: add-only populate behind credential_vault's interface.
Secret values NEVER appear in assertions, logs, or failures."""
import logging
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
    # Point VAULT_TOKEN_FILE at a path that cannot exist (module global is
    # resolved at import, so patch the attribute, not the env). Asserting on
    # the RETURN value would echo a real token into output if this ever fails.
    with mock.patch.dict(kvc.os.environ, {"VAULT_BEARER_TOKEN": ""}), \
         mock.patch.object(kvc, "VAULT_TOKEN_FILE", "/nonexistent/vault-token"):
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


# ---------------------------------------------------------------------------
# Deletion (2026-09-21): the deployed machine plane implements only
# reveal/set for a static service subject — it ignores unknown operations and
# returns the value for any existing path with HTTP 200. delete_for_provider
# must therefore require explicit confirmation and never treat a reveal as a
# successful delete.
# ---------------------------------------------------------------------------


def test_delete_for_provider_false_without_token():
    with mock.patch.dict(kvc.os.environ, {"VAULT_BEARER_TOKEN": ""}), \
         mock.patch.object(kvc, "VAULT_TOKEN_FILE", "/nonexistent/vault-token"):
        assert kvc.delete_for_provider("gpuai") is False


def test_delete_for_provider_not_fooled_by_reveal_response(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return {"value": "still-here"}

    monkeypatch.setattr(kvc.requests, "post", lambda *a, **k: R())
    assert kvc.delete_for_provider("gpuai", token="tok") is False


def test_delete_for_provider_true_only_on_explicit_confirmation(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return {"deleted": True}

    monkeypatch.setattr(kvc.requests, "post", lambda *a, **k: R())
    assert kvc.delete_for_provider("gpuai", token="tok") is True


def test_delete_for_provider_false_on_http_error(monkeypatch):
    class R:
        status_code = 403

    monkeypatch.setattr(kvc.requests, "post", lambda *a, **k: R())
    assert kvc.delete_for_provider("gpuai", token="tok") is False


# ---------------------------------------------------------------------------
# Endpoint + TLS (2026-09-20): default pointed at 192.168.1.117:8120 -- a
# host that does not exist -- so every scheduler cycle logged "kai-vault
# unreachable". The working machine plane is CT107 over TLS :8443.
# ---------------------------------------------------------------------------

def test_default_vault_url_is_the_working_host(monkeypatch):
    monkeypatch.delenv("VAULT_URL", raising=False)
    assert kvc.DEFAULT_VAULT_URL == "https://192.168.1.107:8443"
    assert kvc.vault_url() == "https://192.168.1.107:8443"
    assert "192.168.1.117" not in kvc.DEFAULT_VAULT_URL


def test_vault_url_env_overrides_default(monkeypatch):
    monkeypatch.setenv("VAULT_URL", "https://vault.internal:9443/")
    assert kvc.vault_url() == "https://vault.internal:9443"

    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"value": "v"}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return R()

    monkeypatch.setattr(kvc.requests, "post", fake_post)
    assert kvc.fetch_secret("p", "tok") == "v"
    assert seen["url"] == "https://vault.internal:9443/api/v1/machine/secret"


@pytest.mark.parametrize("status", [401, 404])
def test_http_error_is_not_reported_as_unreachable(monkeypatch, caplog, status):
    class R:
        status_code = status

    monkeypatch.setattr(kvc.requests, "post", lambda *a, **k: R())

    with caplog.at_level(logging.WARNING):
        assert kvc.fetch_secret("some/path", "tok") is None

    assert "unreachable" not in caplog.text.lower()
    assert str(status) in caplog.text


def test_verify_uses_ca_bundle_when_present(monkeypatch, tmp_path):
    ca = tmp_path / "vault.crt"
    ca.write_text("dummy-ca")
    monkeypatch.setenv("VAULT_CA_BUNDLE", str(ca))
    assert kvc._verify_for("https://192.168.1.107:8443/api/v1/machine/secret") == str(ca)


def test_verify_is_insecure_only_for_known_internal_host(monkeypatch):
    # Missing CA bundle -> explicit insecure mode, scoped to the known vault
    # host only. Any other host keeps full certificate verification: never a
    # global disable.
    monkeypatch.setenv("VAULT_CA_BUNDLE", "/nonexistent/vault.crt")
    assert kvc._verify_for("https://192.168.1.107:8443/x") is False
    assert kvc._verify_for("https://evil.example.com/x") is True


def test_fetch_secret_passes_tls_verify(monkeypatch):
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"value": "v"}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return R()

    monkeypatch.setattr(kvc.requests, "post", fake_post)
    kvc.fetch_secret("p", "tok")
    assert "verify" in seen


def test_ssl_context_none_for_http():
    assert kvc.ssl_context_for("http://192.168.1.107:8120/health") is None


def test_ssl_context_insecure_only_for_known_internal_host(monkeypatch):
    import ssl
    monkeypatch.setenv("VAULT_CA_BUNDLE", "/nonexistent/vault.crt")
    known = kvc.ssl_context_for("https://192.168.1.107:8443/x")
    assert known.verify_mode == ssl.CERT_NONE
    other = kvc.ssl_context_for("https://evil.example.com/x")
    assert other.verify_mode == ssl.CERT_REQUIRED
    assert other.check_hostname is True


def test_ssl_context_verified_with_ca_bundle(monkeypatch):
    import os
    import ssl
    ca = "/etc/kai/tls/vault-mp.crt"
    if not os.path.exists(ca):
        pytest.skip("no internal CA bundle on this host")
    monkeypatch.setenv("VAULT_CA_BUNDLE", ca)
    ctx = kvc.ssl_context_for("https://192.168.1.107:8443/x")
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
