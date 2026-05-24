import os
from typing import AsyncGenerator

from groq import AsyncGroq

from sdk.providers import ProviderResult, Usage


_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))
    return _client


def _to_groq_messages(messages: list) -> list[dict]:
    # Groq is OpenAI-shaped — the system role passes through inline (no _split_system).
    # Whitelisting role+content keeps this provider's contract explicit and contains any
    # future drift in our internal message shape to this one function.
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def _normalize_usage(usage) -> tuple[Usage, dict]:
    if usage is None:
        return Usage(), {}
    raw = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return Usage(input_tokens=raw["prompt_tokens"], output_tokens=raw["completion_tokens"]), raw


def _stream_usage(chunk):
    # Groq attaches usage to the final streamed chunk under x_groq.usage (no stream_options
    # flag needed); some SDK versions also expose chunk.usage. Check both.
    x_groq = getattr(chunk, "x_groq", None)
    if x_groq is not None and getattr(x_groq, "usage", None) is not None:
        return x_groq.usage
    return getattr(chunk, "usage", None)


async def call_groq(messages: list, model: str) -> ProviderResult:
    response = await _get_client().chat.completions.create(
        model=model,
        messages=_to_groq_messages(messages),
    )
    choice = response.choices[0]
    usage, raw_usage = _normalize_usage(getattr(response, "usage", None))

    return ProviderResult(
        text=choice.message.content or "",
        usage=usage,
        raw_usage=raw_usage,
        attributes={"finish_reason": choice.finish_reason},
    )


async def stream_groq(messages: list, model: str) -> AsyncGenerator[dict, None]:
    stream = await _get_client().chat.completions.create(
        model=model,
        messages=_to_groq_messages(messages),
        stream=True,
    )

    finish_reason = None
    async for chunk in stream:
        if chunk.choices:
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            text = getattr(choice.delta, "content", None) or ""
            if text:
                yield {"text": text, "usage": None, "raw_usage": None, "attributes": None}

        usage = _stream_usage(chunk)
        if usage is not None:
            u, raw_usage = _normalize_usage(usage)
            yield {
                "text": "",
                "usage": u,
                "raw_usage": raw_usage,
                "attributes": {"finish_reason": finish_reason},
            }
