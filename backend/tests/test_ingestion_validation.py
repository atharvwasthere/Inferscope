"""Ingestion payload validation — the trust boundary.

The ingestion API rejects anything that isn't a well-formed log: unknown provider,
bad status, missing idempotency key. These tests pin those guards.
"""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ingestion.models import InferenceLogPayload


def _valid(**overrides):
    base = dict(
        request_id=uuid4(),
        provider="groq",
        model="llama-3.1-8b-instant",
        status="success",
    )
    base.update(overrides)
    return base


def test_minimal_valid_payload_parses_with_defaults():
    p = InferenceLogPayload(**_valid())
    assert p.input_tokens == 0 and p.output_tokens == 0  # defaults
    assert p.total_tokens is None
    assert p.trace_id is None


@pytest.mark.parametrize("provider", ["bedrock", "gemini", "groq"])
def test_supported_providers_accepted(provider):
    p = InferenceLogPayload(**_valid(provider=provider))
    assert p.provider == provider


def test_unsupported_provider_rejected():
    with pytest.raises(ValidationError):
        InferenceLogPayload(**_valid(provider="openai"))


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        InferenceLogPayload(**_valid(status="pending"))


def test_missing_request_id_rejected():
    payload = _valid()
    del payload["request_id"]
    with pytest.raises(ValidationError):
        InferenceLogPayload(**payload)


def test_bad_request_id_type_rejected():
    with pytest.raises(ValidationError):
        InferenceLogPayload(**_valid(request_id="not-a-uuid"))
