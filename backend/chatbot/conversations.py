from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from chatbot.db import (
    get_all_conversations,
    get_conversation_messages,
    get_db,
    mark_closed,
)
from inferscope.pii_tokenizer import PiiTokenizer

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Display boundary: stored content is tokenized; the user always sees originals.
tokenizer = PiiTokenizer()


@router.get("")
async def list_conversations(db: asyncpg.Connection = Depends(get_db)) -> list[dict]:
    rows = await get_all_conversations(db)
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "closed": r["closed_at"] is not None,
            "message_count": r["message_count"],
        }
        for r in rows
    ]


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: UUID, db: asyncpg.Connection = Depends(get_db)
) -> list[dict]:
    rows = await get_conversation_messages(db, conversation_id)
    return [
        {
            "role": r["role"],
            "content": tokenizer.detokenize(r["content"], r["pii_map"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@router.patch("/{conversation_id}/close")
async def close_conversation(
    conversation_id: UUID, db: asyncpg.Connection = Depends(get_db)
) -> dict:
    """Soft-lock the conversation: future sends to this id return 409.
    Does not abort an in-flight stream — use POST /streams/{stream_id}/abort for that."""
    result = await mark_closed(db, conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "id": str(result["id"]),
        "closed_at": result["closed_at"].isoformat(),
    }
