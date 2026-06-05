import json
import os
from typing import AsyncGenerator
from uuid import UUID

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
            max_inactive_connection_lifetime=300,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    """Accessor injected into the producer/poller — resolves the pool lazily,
    since they are constructed at import time before init_pool() runs."""
    return _pool


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    assert _pool is not None, "pool not initialized"
    async with _pool.acquire() as conn:
        yield conn


async def create_conversation(db: asyncpg.Connection, title: str | None = None) -> UUID:
    return await db.fetchval(
        "INSERT INTO conversations (title) VALUES ($1) RETURNING id",
        title,
    )


async def get_provider_models(db: asyncpg.Connection) -> list[dict]:
    rows = await db.fetch(
        "SELECT provider, model FROM provider_models ORDER BY provider, model"
    )
    return [{"provider": r["provider"], "model": r["model"]} for r in rows]


async def update_conversation_title(db: asyncpg.Connection, conversation_id: UUID, title: str) -> None:
    await db.execute(
        "UPDATE conversations SET title = $1 WHERE id = $2",
        title,
        conversation_id,
    )


async def get_conversation(db: asyncpg.Connection, conversation_id: UUID) -> dict | None:
    row = await db.fetchrow(
        "SELECT id, title, created_at, closed_at FROM conversations WHERE id = $1",
        conversation_id,
    )
    return dict(row) if row else None


async def save_message(
    db: asyncpg.Connection,
    conversation_id: UUID,
    role: str,
    content: str,
    pii_map: dict | None = None,
) -> UUID:
    return await db.fetchval(
        """
        INSERT INTO messages (conversation_id, role, content, pii_map)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        conversation_id,
        role,
        content,
        json.dumps(pii_map) if pii_map else None,
    )


async def get_conversation_messages(db: asyncpg.Connection, conversation_id: UUID) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT role, content, pii_map, created_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        """,
        conversation_id,
    )
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "pii_map": json.loads(r["pii_map"]) if r["pii_map"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def get_all_conversations(db: asyncpg.Connection) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT c.id, c.title, c.created_at, c.closed_at,
               COUNT(m.id) AS message_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """
    )
    return [dict(r) for r in rows]


async def mark_closed(db: asyncpg.Connection, conversation_id: UUID) -> dict | None:
    row = await db.fetchrow(
        "UPDATE conversations SET closed_at = NOW() WHERE id = $1 RETURNING id, closed_at",
        conversation_id,
    )
    return dict(row) if row else None
