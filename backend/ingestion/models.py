from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class InferenceLogPayload(BaseModel):
    request_id: UUID
    trace_id: UUID | None = None
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    provider: str
    model: str

    latency_ms: float | None = None
    time_to_first_token_ms: float | None = None

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int | None = None

    cost_usd: float | None = None

    status: Literal["success", "error"]
    error_message: str | None = None

    input_preview: str | None = None
    output_preview: str | None = None

    raw_usage: dict | None = None
    attributes: dict | None = None

    started_at: datetime | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in {"bedrock", "gemini", "groq"}:
            raise ValueError(f"unsupported provider: {v}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in {"success", "error"}:
            raise ValueError(f"invalid status: {v}")
        return v
