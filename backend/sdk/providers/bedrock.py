import json
import os
from typing import AsyncGenerator

import aioboto3

from sdk.providers import ProviderResult, Usage, _split_system


AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_session = aioboto3.Session()


def _normalize_usage(raw: dict) -> Usage:
    if not raw:
        return Usage()
    return Usage(
        input_tokens=int(raw.get("input_tokens", 0) or 0),
        output_tokens=int(raw.get("output_tokens", 0) or 0),
        cache_read_input_tokens=int(raw.get("cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens", 0) or 0),
    )


def _build_body(messages: list) -> dict:
    system, convo = _split_system(messages)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": convo,
    }
    if system:
        body["system"] = system
    return body


async def call_bedrock(messages: list, model: str) -> ProviderResult:
    body = _build_body(messages)
    async with _session.client("bedrock-runtime", region_name=AWS_REGION) as client:
        response = await client.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(await response["body"].read())

    raw_usage = result.get("usage", {})
    return ProviderResult(
        text=result["content"][0]["text"],
        usage=_normalize_usage(raw_usage),
        raw_usage=raw_usage,
        attributes={
            "stop_reason": result.get("stop_reason"),
            "stop_sequence": result.get("stop_sequence"),
            "max_tokens": body["max_tokens"],
        },
    )


async def stream_bedrock(messages: list, model: str) -> AsyncGenerator[dict, None]:
    body = _build_body(messages)
    async with _session.client("bedrock-runtime", region_name=AWS_REGION) as client:
        response = await client.invoke_model_with_response_stream(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        async for event in response["body"]:
            chunk = json.loads(event["chunk"]["bytes"])
            ctype = chunk.get("type")

            if ctype == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield {"text": delta.get("text", ""), "usage": None, "raw_usage": None, "attributes": None}

            elif ctype == "message_start":
                raw = chunk.get("message", {}).get("usage", {})
                yield {"text": "", "usage": _normalize_usage(raw), "raw_usage": raw, "attributes": None}

            elif ctype == "message_delta":
                raw = chunk.get("usage", {})
                delta = chunk.get("delta", {})
                yield {
                    "text": "",
                    "usage": _normalize_usage(raw),
                    "raw_usage": raw,
                    "attributes": {
                        "stop_reason": delta.get("stop_reason"),
                        "stop_sequence": delta.get("stop_sequence"),
                    },
                }
