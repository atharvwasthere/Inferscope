"""Auth on the collector's front door (T3a).

Two things worth testing separately: the *policy* (fail closed with no keys —
the collector must not start, rather than start and accept everything) and the
*check* (a request without a valid bearer token is rejected).

/health and /ready must stay open, or Kubernetes probes start failing the moment
auth lands — a self-inflicted outage that looks like a broken deploy.
"""
import os

import pytest
from fastapi import HTTPException

from ingestion.auth import KEYS_ENV, _extract_bearer, _load_keys, require_api_key

# _load_keys() is exercised directly rather than via importlib.reload(). Reloading
# rebinds the module-global API_KEYS for every later test in the session, so the
# policy tests would silently break the check tests below.


# --- policy: fail closed -------------------------------------------------------


def test_collector_refuses_to_load_without_keys(monkeypatch):
    """No keys means no boot. A control that silently no-ops is not a control."""
    monkeypatch.delenv(KEYS_ENV, raising=False)
    with pytest.raises(RuntimeError, match=KEYS_ENV):
        _load_keys()


def test_blank_keys_are_treated_as_unset(monkeypatch):
    monkeypatch.setenv(KEYS_ENV, "   ,  ,")
    with pytest.raises(RuntimeError, match=KEYS_ENV):
        _load_keys()


def test_multiple_keys_are_accepted(monkeypatch):
    monkeypatch.setenv(KEYS_ENV, "alpha, beta ,gamma")
    assert _load_keys() == frozenset({"alpha", "beta", "gamma"})


# --- the check -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_key_passes():
    await require_api_key("Bearer test-key")  # conftest sets INFERSCOPE_API_KEYS=test-key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        None,                 # no header at all
        "",                   # empty
        "test-key",           # raw key, no scheme
        "Basic test-key",     # wrong scheme
        "Bearer",             # scheme with no token
        "Bearer ",            # scheme with blank token
        "Bearer wrong-key",   # well-formed but not ours
    ],
)
async def test_invalid_credentials_are_rejected(header):
    with pytest.raises(HTTPException) as exc:
        await require_api_key(header)
    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),   # scheme is case-insensitive per RFC 7235
        ("BEARER abc", "abc"),
        ("Basic abc", None),
        ("abc", None),
        (None, None),
    ],
)
def test_bearer_extraction(header, expected):
    assert _extract_bearer(header) == expected


# --- probes stay open ----------------------------------------------------------


def test_health_and_ready_are_not_authenticated():
    """Auth is a route dependency, not middleware — probes must keep working."""
    from ingestion.main import app

    secured = {
        r.path
        for r in app.routes
        if getattr(r, "dependencies", None)
    }
    assert "/ingest" in secured
    assert "/health" not in secured
    assert "/ready" not in secured


def test_ingest_returns_401_over_http_without_a_key():
    """Proves the dependency is actually WIRED to the route.

    The unit tests above prove require_api_key rejects. They would still pass if
    someone dropped the `dependencies=[...]` from the decorator — this is the one
    that fails in that case. No lifespan is started, so no DB or Redis is touched.
    """
    from fastapi.testclient import TestClient

    from ingestion.main import app

    client = TestClient(app)  # not used as a context manager => lifespan never runs
    response = client.post("/ingest", json={})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_health_is_reachable_without_a_key():
    from fastapi.testclient import TestClient

    from ingestion.main import app

    assert TestClient(app).get("/health").status_code == 200


def test_env_name_matches_what_deployment_configures():
    """Guards a rename drifting from compose / k8s secret / .env.example."""
    assert KEYS_ENV == "INFERSCOPE_API_KEYS"
    assert os.environ.get(KEYS_ENV)  # conftest supplies it
