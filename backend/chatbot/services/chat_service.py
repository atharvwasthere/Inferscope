import asyncio
import json
import logging
import os
from uuid import UUID
from typing import AsyncGenerator

import asyncpg
from fastapi import HTTPException

from chatbot.db import (
    create_conversation,
    get_conversation,
    get_conversation_messages,
    get_pool,
    save_message,
    update_conversation_title,
)
from chatbot.services.title_service import TitleService
from obs.log import get_logger, log_with
from sdk.pii_tokenizer import PiiTokenizer
from sdk.wrapper import LLMWrapper


logger = get_logger("chatbot.chat_service")

SYSTEM_PROMPT_DEFAULT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are inferscope, a helpful and concise assistant. Avoid filler. Answer directly.",
)
MAX_TURNS_DEFAULT = int(os.environ.get("MAX_TURNS", "10"))

# Always-on guidance about privacy tokens, appended to the persona system prompt.
TOKEN_GUIDANCE = (
    "User messages may contain privacy tokens in the format [PII:TYPE:N] where TYPE is EMAIL, "
    "PHONE_IN, etc. These are opaque placeholders. Never repeat them verbatim in your response. "
    "Instead refer to them naturally: 'the email you provided', 'your phone number'. "
    "Never try to guess or reconstruct the original values."
)

_SUPPORTED_PROVIDERS = {"bedrock", "gemini", "groq"}


class ChatService:
    def __init__(
        self,
        wrapper: LLMWrapper,
        db: asyncpg.Connection,
        system_prompt: str = SYSTEM_PROMPT_DEFAULT,
        max_turns: int = MAX_TURNS_DEFAULT,
        tokenizer: PiiTokenizer | None = None,
        title_service: TitleService | None = None,
    ) -> None:
        self.wrapper = wrapper
        self.db = db
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.tokenizer = tokenizer or PiiTokenizer()
        self.title_service = title_service or TitleService()
        # per-request state captured in prepare(), read in stream_tokens()
        self._was_created = False
        self._first_user_tokenized = ""

    async def prepare(
        self,
        message: str,
        conversation_id: UUID | None,
        provider: str,
    ) -> tuple[UUID, list[dict]]:
        """Pre-flight: validate, resolve conversation, persist user message, build prompt.

        Runs BEFORE the stream opens so HTTP errors (404/409/400) still set status code.
        """
        if provider not in _SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"unsupported provider: {provider}")

        # Tokenize PII before anything is persisted: the LLM sees tokens, the user sees
        # originals (detokenized at the display API). History stays tokenized → LLM context
        # safe. The conversation title is derived from the tokenized text too, so PII never
        # lands in a title either.
        tokenized, pii_map = self.tokenizer.tokenize(message)

        # capture first-message state before conversation_id is (re)assigned
        self._was_created = conversation_id is None
        self._first_user_tokenized = tokenized

        conversation_id = await self._resolve_conversation(tokenized, conversation_id)
        await save_message(self.db, conversation_id, "user", tokenized, pii_map=pii_map)

        history = await get_conversation_messages(self.db, conversation_id)
        messages = self._build_messages(history)
        return conversation_id, messages

    async def stream_tokens(
        self,
        conversation_id: UUID,
        messages: list[dict],
        provider: str,
        model: str,
    ) -> AsyncGenerator[str, None]:
        """SSE generator. Saves assistant reply in finally — survives client disconnect."""
        yield self._sse("meta", {"conversation_id": str(conversation_id)})

        collected: list[str] = []
        try:
            gen = await self.wrapper.chat(
                messages, provider, model, session_id=conversation_id, stream=True
            )
            async for token in gen:
                collected.append(token)
                yield self._sse("message", {"text": token})
        finally:
            reply = "".join(collected)
            if reply:
                await save_message(self.db, conversation_id, "assistant", reply)
            # First message → generate a real title in the background (fire-and-forget).
            if self._was_created and reply:
                asyncio.create_task(
                    self._update_title(conversation_id, self._first_user_tokenized, reply)
                )
            yield self._sse("done", {"conversation_id": str(conversation_id), "status": "complete"})

    async def _update_title(self, conversation_id: UUID, tokenized_user: str, assistant_reply: str) -> None:
        """Background task: generate a topic title and persist it. Never breaks the chat path."""
        try:
            # tokenize the assistant reply too — defense in depth so the title model sees no raw PII
            tok_assistant, _ = self.tokenizer.tokenize(assistant_reply)
            title = await self.title_service.generate_title(tokenized_user, tok_assistant)
            # the request-scoped connection is gone by now — acquire a fresh one from the pool
            pool = get_pool()
            if pool is None:
                return
            async with pool.acquire() as conn:
                await update_conversation_title(conn, conversation_id, title)
        except Exception as e:
            log_with(
                logger,
                logging.WARNING,
                "title update failed",
                conversation_id=str(conversation_id),
                error=f"{type(e).__name__}: {e}",
            )

    async def _resolve_conversation(self, tokenized_message: str, conversation_id: UUID | None) -> UUID:
        if conversation_id is None:
            # tokenized text is already PII-free; truncation may clip a token but never leaks PII
            title = tokenized_message[:50]
            return await create_conversation(self.db, title=title)

        conv = await get_conversation(self.db, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if conv["cancelled_at"] is not None:
            raise HTTPException(status_code=409, detail="conversation is cancelled")
        return conversation_id

    def _build_messages(self, history: list[dict]) -> list[dict]:
        trimmed = self._trim(history)
        convo = [{"role": m["role"], "content": m["content"]} for m in trimmed]
        # Single system message: providers' _split_system keeps only the last one, so persona
        # and token guidance must be combined rather than sent as two separate system messages.
        system = f"{self.system_prompt}\n\n{TOKEN_GUIDANCE}"
        return [{"role": "system", "content": system}] + convo

    def _trim(self, history: list[dict]) -> list[dict]:
        max_messages = self.max_turns * 2
        return history[-max_messages:] if len(history) > max_messages else history

    @staticmethod
    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"
