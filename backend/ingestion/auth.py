"""API-key auth for the collector's front door.

Fail-closed by design: with no keys configured the process refuses to start. A
security control that silently no-ops is how ``POST /ingest`` stayed open to
anyone who could reach it for this long, and "the deploy is misconfigured" should
be loud at boot rather than quiet and accepting traffic.

Keys are read from the environment at import, like every other config in this
codebase (``DATABASE_URL``, ``REDIS_URL``). Rotating a key therefore means
restarting the collector — a rolling restart in Kubernetes. Per-request key
lookup would mean storing keys in Postgres, which is the multi-tenant path gated
behind D-OPEN-C and deliberately not built here.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status

KEYS_ENV = "INFERSCOPE_API_KEYS"


def _load_keys() -> frozenset[str]:
    raw = os.environ.get(KEYS_ENV, "")
    keys = frozenset(k.strip() for k in raw.split(",") if k.strip())
    if not keys:
        raise RuntimeError(
            f"{KEYS_ENV} is required — the collector will not start unauthenticated. "
            f'Set it to a comma-separated list of accepted keys (e.g. {KEYS_ENV}="dev" '
            "for local use), and give SDK clients the same value as INFERSCOPE_API_KEY."
        )
    return keys


API_KEYS = _load_keys()


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    """Reject anything without a recognised ``Authorization: Bearer <key>``.

    Compared with ``compare_digest`` over every configured key rather than a set
    lookup: a plain ``in`` test short-circuits on the first differing byte, which
    leaks key material through response timing.
    """
    token = _extract_bearer(authorization)
    if token is None or not any(secrets.compare_digest(token, k) for k in API_KEYS):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
