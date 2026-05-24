import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Awaitable, Callable
from uuid import UUID, uuid4

from obs.events import InferenceEvent, Publisher
from obs.log import get_logger, log_with
from obs.trace import get_trace_id
from sdk.providers import ProviderResult, Usage
from sdk.providers.bedrock import call_bedrock, stream_bedrock
from sdk.providers.gemini import call_gemini, stream_gemini
from sdk.providers.groq import call_groq, stream_groq


logger = get_logger("sdk.wrapper")


CallFn = Callable[[list, str], Awaitable[ProviderResult]]
StreamFn = Callable[[list, str], AsyncGenerator[dict, None]]


PROVIDERS: dict[str, tuple[CallFn, StreamFn]] = {
    "bedrock": (call_bedrock, stream_bedrock),
    "gemini": (call_gemini, stream_gemini),
    "groq": (call_groq, stream_groq),
}


_PREVIEW_CHARS = 500


def _truncate(text: str | None, limit: int = _PREVIEW_CHARS) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _first_user_text(messages: list) -> str | None:
    for m in messages:
        if m.get("role") == "user":
            return m.get("content")
    return None


class LLMWrapper:
    def __init__(
        self,
        publisher: Publisher,
        providers: dict[str, tuple[CallFn, StreamFn]] | None = None,
    ) -> None:
        self.publisher = publisher
        self.providers = providers or PROVIDERS

    async def chat(
        self,
        messages: list,
        provider: str,
        model: str,
        session_id: UUID | None = None,
        stream: bool = False,
    ) -> str | AsyncGenerator[str, None]:
        if provider not in self.providers:
            raise ValueError(f"unknown provider: {provider}")

        call_fn, stream_fn = self.providers[provider]

        request_id = uuid4()
        if stream:
            return self._stream(stream_fn, messages, provider, model, session_id, request_id)
        return await self._invoke(call_fn, messages, provider, model, session_id, request_id)

    async def _invoke(
        self,
        call_fn: CallFn,
        messages: list,
        provider: str,
        model: str,
        session_id: UUID | None,
        request_id: UUID,
    ) -> str:
        started_at = datetime.now(timezone.utc)
        start = time.perf_counter()
        try:
            result: ProviderResult = await call_fn(messages, model)
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            asyncio.create_task(self._publish(self._build_error_log(
                provider=provider, model=model, session_id=session_id, request_id=request_id,
                messages=messages, started_at=started_at, latency_ms=latency_ms,
                error=e,
            )))
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        asyncio.create_task(self._publish(self._build_success_log(
            provider=provider, model=model, session_id=session_id, request_id=request_id,
            messages=messages, started_at=started_at, latency_ms=latency_ms,
            ttft_ms=None, result=result,
        )))
        return result.text

    async def _stream(
        self,
        stream_fn: StreamFn,
        messages: list,
        provider: str,
        model: str,
        session_id: UUID | None,
        request_id: UUID,
    ) -> AsyncGenerator[str, None]:
        started_at = datetime.now(timezone.utc)
        start = time.perf_counter()
        first_token_at: float | None = None
        collected: list[str] = []
        merged_usage = Usage()
        merged_raw: dict = {}
        merged_attrs: dict = {}

        try:
            async for chunk in stream_fn(messages, model):
                text = chunk.get("text") or ""
                if text:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    collected.append(text)
                    yield text

                u = chunk.get("usage")
                if u is not None:
                    merged_usage = merged_usage.merge(u)
                raw = chunk.get("raw_usage")
                if raw:
                    merged_raw.update(raw)
                attrs = chunk.get("attributes")
                if attrs:
                    merged_attrs.update(attrs)
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            ttft_ms = ((first_token_at - start) * 1000) if first_token_at else None
            asyncio.create_task(self._publish(self._build_error_log(
                provider=provider, model=model, session_id=session_id, request_id=request_id,
                messages=messages, started_at=started_at, latency_ms=latency_ms,
                error=e, output_text="".join(collected), ttft_ms=ttft_ms,
            )))
            raise
        finally:
            if first_token_at is not None or collected:
                latency_ms = (time.perf_counter() - start) * 1000
                ttft_ms = ((first_token_at - start) * 1000) if first_token_at else None
                fake_result = ProviderResult(
                    text="".join(collected),
                    usage=merged_usage,
                    raw_usage=merged_raw,
                    attributes=merged_attrs,
                )
                asyncio.create_task(self._publish(self._build_success_log(
                    provider=provider, model=model, session_id=session_id, request_id=request_id,
                    messages=messages, started_at=started_at, latency_ms=latency_ms,
                    ttft_ms=ttft_ms, result=fake_result,
                )))

    def _build_success_log(
        self,
        provider: str,
        model: str,
        session_id: UUID | None,
        request_id: UUID,
        messages: list,
        started_at: datetime,
        latency_ms: float,
        ttft_ms: float | None,
        result: ProviderResult,
    ) -> dict:
        u = result.usage
        return {
            "request_id": str(request_id),
            "trace_id": get_trace_id() or None,
            "conversation_id": str(session_id) if session_id else None,
            "message_id": None,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "time_to_first_token_ms": ttft_ms,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_input_tokens": u.cache_read_input_tokens,
            "cache_creation_input_tokens": u.cache_creation_input_tokens,
            "reasoning_tokens": u.reasoning_tokens,
            "total_tokens": u.input_tokens + u.output_tokens,
            "status": "success",
            "error_message": None,
            "input_preview": _truncate(_first_user_text(messages)),
            "output_preview": _truncate(result.text),
            "raw_usage": result.raw_usage or None,
            "attributes": result.attributes or None,
            "started_at": started_at.isoformat(),
        }

    def _build_error_log(
        self,
        provider: str,
        model: str,
        session_id: UUID | None,
        request_id: UUID,
        messages: list,
        started_at: datetime,
        latency_ms: float,
        error: Exception,
        output_text: str = "",
        ttft_ms: float | None = None,
    ) -> dict:
        return {
            "request_id": str(request_id),
            "trace_id": get_trace_id() or None,
            "conversation_id": str(session_id) if session_id else None,
            "message_id": None,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "time_to_first_token_ms": ttft_ms,
            "input_tokens": 0,
            "output_tokens": 0,
            "status": "error",
            "error_message": f"{type(error).__name__}: {error}"[:1000],
            "input_preview": _truncate(_first_user_text(messages)),
            "output_preview": _truncate(output_text) if output_text else None,
            "started_at": started_at.isoformat(),
        }

    async def _publish(self, log: dict) -> None:
        # observability is best-effort: a publish failure must never break the caller.
        try:
            await self.publisher.publish(InferenceEvent.from_log(log))
        except Exception as e:
            log_with(
                logger,
                logging.WARNING,
                "event publish failed",
                request_id=log.get("request_id"),
                error=f"{type(e).__name__}: {e}",
            )
