"""Phase 13H: authentication tests for the new autonomy endpoints.

The plan is explicit: every write endpoint MUST enforce
``Depends(require_bridge_token)`` -- the same auth as the deprecated
enable/disable endpoints. These tests are the regression fence for
that requirement. If a future refactor drops the dependency, one of
these tests will fail *before* the missing gate ever ships.
"""

from fastapi.testclient import TestClient

import core.api as api_module
import core.autonomy as autonomy


client = TestClient(api_module.app)


def _auth_headers():
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


# ---------------------------------------------------------------------------
# GET /api/autonomy -- read-only, ungated (matches /kai/identity policy).
# ---------------------------------------------------------------------------


def test_get_autonomy_is_unauthenticated_read():
    response = client.get("/api/autonomy")

    assert response.status_code == 200
    body = response.json()
    assert body["level"] == 1  # fresh-install default
    assert body["set_by"] == autonomy.SYSTEM_DEFAULT_IDENTITY
    assert "updated_at" in body


# ---------------------------------------------------------------------------
# PUT /api/autonomy/level -- authenticated write.
# ---------------------------------------------------------------------------


def test_put_autonomy_level_rejects_unauthenticated_request():
    response = client.put("/api/autonomy/level", json={"level": 3})

    assert response.status_code == 401


def test_put_autonomy_level_rejects_wrong_bearer_token():
    response = client.put(
        "/api/autonomy/level",
        json={"level": 3},
        headers={"Authorization": "Bearer not-the-real-token"},
    )

    assert response.status_code == 401


def test_put_autonomy_level_accepts_authenticated_request():
    response = client.put(
        "/api/autonomy/level",
        json={"level": 3},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["level"] == 3
    # BRIDGE_OPERATOR is the identity require_bridge_token records for
    # this shared-secret caller -- exactly what we want in set_by, so an
    # audit reader can see WHICH authenticated caller made the change.
    assert body["set_by"] == api_module.BRIDGE_OPERATOR


def test_put_autonomy_level_rejects_out_of_range():
    response = client.put(
        "/api/autonomy/level",
        json={"level": 42},
        headers=_auth_headers(),
    )

    assert response.status_code == 400


def test_put_autonomy_level_rejects_non_integer():
    response = client.put(
        "/api/autonomy/level",
        json={"level": "very high"},
        headers=_auth_headers(),
    )

    # Pydantic rejects strings -> 422; a plain int-cast failure would be
    # our own 400. Either is acceptable auth behavior; the point is
    # "not 200 and no state change".
    assert response.status_code in (400, 422)
    # The on-disk record must not have been mutated.
    record = autonomy.get_autonomy_level()
    assert record["level"] == 1


# ---------------------------------------------------------------------------
# Deprecated wrappers (POST /roadmap/autonomous/enable & /disable) still gate.
# ---------------------------------------------------------------------------


def test_deprecated_enable_endpoint_still_requires_auth():
    response = client.post("/roadmap/autonomous/enable")
    assert response.status_code == 401


def test_deprecated_disable_endpoint_still_requires_auth():
    response = client.post("/roadmap/autonomous/disable")
    assert response.status_code == 401


def test_deprecated_enable_endpoint_maps_to_level_4_with_operator_recorded():
    """The wrapper's job: preserve the old binary API AND record who
    used it, so audit history is continuous across the 13H cutover."""
    response = client.post("/roadmap/autonomous/enable", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == {"enabled": True}

    record = autonomy.get_autonomy_level()
    assert record["level"] == 4
    assert record["set_by"] == api_module.BRIDGE_OPERATOR


def test_deprecated_disable_endpoint_maps_to_level_1_not_level_0():
    """"Disable" (pre-13H) meant "stop the roadmap loop", which is
    Level 1's exact semantic. Silently reaching for Level 0 would
    disable observe/report too and would be a behavior change
    invisible to the caller."""
    # Start at 4 so we can watch the wrapper walk us back down.
    autonomy.set_autonomy_level(4, "seed")

    response = client.post("/roadmap/autonomous/disable", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == {"enabled": False}

    record = autonomy.get_autonomy_level()
    assert record["level"] == 1


# ---------------------------------------------------------------------------
# /kai/identity carries the 13H autonomy fields for the plugin UI.
# ---------------------------------------------------------------------------


def test_kai_identity_endpoint_reports_autonomy_level_and_set_by():
    autonomy.set_autonomy_level(3, "operator-in-test")

    response = client.get("/kai/identity")

    assert response.status_code == 200
    body = response.json()
    # Old boolean field preserved for pre-13H clients.
    assert body["autonomous_mode"] is False  # level 3 < 4
    # 13H additions the plugin renders in the Overview toggle.
    assert body["autonomy_level"] == 3
    assert body["autonomy_set_by"] == "operator-in-test"
    assert "autonomy_updated_at" in body
