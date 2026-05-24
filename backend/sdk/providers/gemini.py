import os
from typing import AsyncGenerator

from google import genai
from google.genai import types

from sdk.providers import ProviderResult, Usage, _split_system


GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
    return _client


_ROLE_MAP = {"user": "user", "assistant": "model"}


def _to_gemini_contents(messages: list) -> list[dict]:
    return [
        {"role": _ROLE_MAP[m["role"]], "parts": [{"text": m["content"]}]}
        for m in messages
    ]


def _build_config(system: str | None) -> types.GenerateContentConfig | None:
    if not system:
        return None
    return types.GenerateContentConfig(system_instruction=system)


def _normalize_usage(meta) -> tuple[Usage, dict]:
    if meta is None:
        return Usage(), {}
    raw = {
        "prompt_token_count": int(getattr(meta, "prompt_token_count", 0) or 0),
        "candidates_token_count": int(getattr(meta, "candidates_token_count", 0) or 0),
        "cached_content_token_count": int(getattr(meta, "cached_content_token_count", 0) or 0),
        "thoughts_token_count": int(getattr(meta, "thoughts_token_count", 0) or 0),
        "total_token_count": int(getattr(meta, "total_token_count", 0) or 0),
    }
    usage = Usage(
        input_tokens=raw["prompt_token_count"],
        output_tokens=raw["candidates_token_count"],
        cache_read_input_tokens=raw["cached_content_token_count"],
        reasoning_tokens=raw["thoughts_token_count"],
    )
    return usage, raw


def _extract_attributes(response) -> dict:
    finish_reason = None
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            finish_reason = str(fr) if fr is not None else None
    except Exception:
        pass
    return {"finish_reason": finish_reason}


async def call_gemini(messages: list, model: str) -> ProviderResult:
    system, convo = _split_system(messages)
    contents = _to_gemini_contents(convo)

    response = await _get_client().aio.models.generate_content(
        model=model,
        contents=contents,
        config=_build_config(system),
    )
    usage, raw_usage = _normalize_usage(getattr(response, "usage_metadata", None))

    return ProviderResult(
        text=response.text,
        usage=usage,
        raw_usage=raw_usage,
        attributes=_extract_attributes(response),
    )


async def stream_gemini(messages: list, model: str) -> AsyncGenerator[dict, None]:
    system, convo = _split_system(messages)
    contents = _to_gemini_contents(convo)

    stream = await _get_client().aio.models.generate_content_stream(
        model=model,
        contents=contents,
        config=_build_config(system),
    )

    final = None
    async for chunk in stream:
        text = getattr(chunk, "text", "") or ""
        if text:
            yield {"text": text, "usage": None, "raw_usage": None, "attributes": None}
        final = chunk

    if final is not None:
        usage, raw_usage = _normalize_usage(getattr(final, "usage_metadata", None))
        yield {
            "text": "",
            "usage": usage,
            "raw_usage": raw_usage,
            "attributes": _extract_attributes(final),
        }
