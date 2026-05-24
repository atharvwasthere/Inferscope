"""Conversation title generation.

Single responsibility: turn a (tokenized) first exchange into a short topic title.
Knows nothing about the database, Redis, or FastAPI. Calls the Groq provider
directly — deliberately bypassing the wrapper, so title generation does not emit
telemetry, hit the event bus, or need a publisher.

``generate_title`` never raises: a failure here must never break the chat path.
"""
from __future__ import annotations

import logging

from obs.log import get_logger, log_with
from sdk.providers.groq import call_groq

logger = get_logger("chatbot.title_service")

# Always use the fastest cheap model for titles, regardless of the user's provider/model choice.
TITLE_MODEL = "llama-3.1-8b-instant"
_FALLBACK = "New Conversation"
_MAX_LEN = 50

_SYSTEM = (
    "Generate a conversation title in 5 words or less. Describe the topic only. "
    "Output ONLY the title text — no quotes, no preamble, and never echo labels like "
    "'User:' or 'Assistant:' from the input. "
    "Never include emails, phone numbers, or any personal information. "
    "Never repeat PII tokens like [PII:EMAIL:1]."
)


class TitleService:
    async def generate_title(self, tokenized_user_msg: str, tokenized_assistant_msg: str) -> str:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": f"User: {tokenized_user_msg}\nAssistant: {tokenized_assistant_msg}",
            },
        ]
        try:
            result = await call_groq(messages, TITLE_MODEL)
            title = (result.text or "").strip().strip('"').strip()
            return title[:_MAX_LEN] if title else _FALLBACK
        except Exception as e:
            log_with(
                logger,
                logging.WARNING,
                "title generation failed",
                error=f"{type(e).__name__}: {e}",
            )
            return _FALLBACK
