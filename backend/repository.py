"""Conversation and client-key repository with PostgreSQL and local JSON modes."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import database, storage


def _decode_conversation(row) -> Dict[str, Any]:
    messages = row["messages"]
    if isinstance(messages, str):
        messages = json.loads(messages)
    return {
        "id": str(row["id"]),
        "owner_client_id": row["client_id"],
        "created_at": row["created_at"].isoformat(),
        "title": row["title"],
        "messages": messages,
    }


async def ensure_client(client_id: str, display_name: str = "") -> None:
    if not database.enabled():
        return
    async with database.pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO nexus_api_clients(client_id, display_name)
               VALUES($1, $2) ON CONFLICT(client_id) DO NOTHING""",
            client_id, display_name,
        )


async def find_active_key(key_hash: str) -> Optional[Dict[str, str]]:
    if not database.enabled():
        return None
    async with database.pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT k.key_id::text AS key_id, k.client_id
               FROM nexus_api_keys k
               JOIN nexus_api_clients c USING(client_id)
               WHERE k.key_hash=$1 AND k.revoked_at IS NULL AND c.disabled_at IS NULL""",
            key_hash,
        )
        if not row:
            return None
        await conn.execute(
            "UPDATE nexus_api_keys SET last_used_at=NOW() WHERE key_id=$1::uuid",
            row["key_id"],
        )
        return {"key_id": row["key_id"], "client_id": row["client_id"]}


async def create_client_key(client_id: str, display_name: str, key_hash: str) -> str:
    if not database.enabled():
        raise RuntimeError("Persistent key administration requires DATABASE_URL")
    key_id = str(uuid.uuid4())
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO nexus_api_clients(client_id, display_name)
                   VALUES($1, $2)
                   ON CONFLICT(client_id) DO UPDATE SET display_name=EXCLUDED.display_name""",
                client_id, display_name,
            )
            await conn.execute(
                "INSERT INTO nexus_api_keys(key_id, client_id, key_hash) VALUES($1::uuid, $2, $3)",
                key_id, client_id, key_hash,
            )
    return key_id


async def revoke_client_key(key_id: str) -> bool:
    if not database.enabled():
        raise RuntimeError("Persistent key administration requires DATABASE_URL")
    async with database.pool().acquire() as conn:
        result = await conn.execute(
            """UPDATE nexus_api_keys SET revoked_at=NOW()
               WHERE key_id=$1::uuid AND revoked_at IS NULL""",
            key_id,
        )
    return result.endswith(" 1")


async def create_conversation(conversation_id: str, client_id: str) -> Dict[str, Any]:
    if not database.enabled():
        return await asyncio.to_thread(storage.create_conversation, conversation_id, client_id)
    await ensure_client(client_id)
    async with database.pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO nexus_conversations(id, client_id)
               VALUES($1::uuid, $2)
               RETURNING id, client_id, created_at, title, messages""",
            conversation_id, client_id,
        )
    return _decode_conversation(row)


async def get_conversation(conversation_id: str, client_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not database.enabled():
        try:
            return await asyncio.to_thread(storage.get_conversation, conversation_id, client_id)
        except ValueError:
            return None
    async with database.pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, client_id, created_at, title, messages
               FROM nexus_conversations
               WHERE id=$1::uuid AND ($2::text IS NULL OR client_id=$2)""",
            conversation_id, client_id,
        )
    return _decode_conversation(row) if row else None


async def list_conversations(client_id: str) -> List[Dict[str, Any]]:
    if not database.enabled():
        return await asyncio.to_thread(storage.list_conversations, client_id)
    async with database.pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, created_at, title, jsonb_array_length(messages) AS message_count
               FROM nexus_conversations WHERE client_id=$1 ORDER BY created_at DESC""",
            client_id,
        )
    return [{
        "id": str(row["id"]), "created_at": row["created_at"].isoformat(),
        "title": row["title"], "message_count": row["message_count"],
    } for row in rows]


async def _mutate_conversation(conversation_id: str, client_id: str, operation: str, value: Any) -> None:
    if not database.enabled():
        if operation == "message":
            await asyncio.to_thread(storage.add_user_message, conversation_id, value, client_id)
        elif operation == "assistant":
            await asyncio.to_thread(storage.add_assistant_message, conversation_id, *value, client_id)
        elif operation == "title":
            await asyncio.to_thread(storage.update_conversation_title, conversation_id, value, client_id)
        return
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT messages FROM nexus_conversations
                   WHERE id=$1::uuid AND client_id=$2 FOR UPDATE""",
                conversation_id, client_id,
            )
            if row is None:
                raise ValueError("Conversation not found")
            messages = row["messages"]
            if isinstance(messages, str):
                messages = json.loads(messages)
            if operation == "message":
                messages.append({"role": "user", "content": value})
                await conn.execute(
                    "UPDATE nexus_conversations SET messages=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid",
                    conversation_id, json.dumps(messages),
                )
            elif operation == "assistant":
                stage1, stage2, stage3 = value
                messages.append({"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3})
                await conn.execute(
                    "UPDATE nexus_conversations SET messages=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid",
                    conversation_id, json.dumps(messages),
                )
            elif operation == "title":
                await conn.execute(
                    "UPDATE nexus_conversations SET title=$2, updated_at=NOW() WHERE id=$1::uuid",
                    conversation_id, str(value)[:100],
                )


async def add_user_message(conversation_id: str, content: str, client_id: str) -> None:
    await _mutate_conversation(conversation_id, client_id, "message", content)


async def add_assistant_message(conversation_id: str, stage1, stage2, stage3, client_id: str) -> None:
    await _mutate_conversation(conversation_id, client_id, "assistant", (stage1, stage2, stage3))


async def update_conversation_title(conversation_id: str, title: str, client_id: str) -> None:
    await _mutate_conversation(conversation_id, client_id, "title", title)


async def record_usage(client_id: Optional[str], key_id: Optional[str], method: str,
                       route: str, status_code: int, duration_ms: int) -> None:
    if not database.enabled():
        return
    async with database.pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO nexus_usage_events
               (client_id, api_key_id, method, route, status_code, duration_ms)
               VALUES($1, $2::uuid, $3, $4, $5, $6)""",
            client_id, key_id, method, route, status_code, max(0, duration_ms),
        )


async def usage_summary(client_id: str, days: int = 30) -> Dict[str, Any]:
    if not database.enabled():
        raise RuntimeError("Usage reports require DATABASE_URL")
    async with database.pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*)::bigint AS requests,
                      COUNT(*) FILTER (WHERE status_code >= 400)::bigint AS errors,
                      COALESCE(SUM(duration_ms), 0)::bigint AS duration_ms
               FROM nexus_usage_events
               WHERE client_id=$1 AND created_at >= NOW() - ($2::int * INTERVAL '1 day')""",
            client_id, max(1, min(days, 90)),
        )
    return {"client_id": client_id, "days": max(1, min(days, 90)),
            "requests": row["requests"], "errors": row["errors"], "duration_ms": row["duration_ms"]}

async def list_client_keys(client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List key metadata only; never return stored hashes or raw secrets."""
    if not database.enabled():
        raise RuntimeError("Persistent key administration requires DATABASE_URL")
    async with database.pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT key_id::text AS key_id, client_id, created_at, revoked_at, last_used_at
               FROM nexus_api_keys
               WHERE ($1::text IS NULL OR client_id=$1)
               ORDER BY created_at DESC LIMIT 500""",
            client_id,
        )
    return [{
        "key_id": row["key_id"],
        "client_id": row["client_id"],
        "created_at": row["created_at"].isoformat(),
        "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
        "active": row["revoked_at"] is None,
    } for row in rows]


async def reserve_usage(client_id: str, key_id: Optional[str], method: str,
                        route: str, daily_limit: int) -> Optional[int]:
    """Atomically reserve a request slot and usage row; None means quota exhausted."""
    if not database.enabled():
        return 0
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            # Serialize quota checks per client so concurrent requests cannot all pass
            # the same final available slot.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", client_id)
            if daily_limit > 0:
                used = await conn.fetchval(
                    """SELECT COUNT(*) FROM nexus_usage_events
                       WHERE client_id=$1 AND created_at >= date_trunc('day', NOW())""",
                    client_id,
                )
                if used >= daily_limit:
                    return None
            row = await conn.fetchrow(
                """INSERT INTO nexus_usage_events
                   (client_id, api_key_id, method, route, status_code, duration_ms)
                   VALUES($1, $2::uuid, $3, $4, 0, 0) RETURNING id""",
                client_id, key_id, method, route,
            )
            return int(row["id"])


async def complete_usage(event_id: int, status_code: int, duration_ms: int) -> None:
    if not database.enabled() or event_id <= 0:
        return
    async with database.pool().acquire() as conn:
        await conn.execute(
            """UPDATE nexus_usage_events SET status_code=$2, duration_ms=$3
               WHERE id=$1""",
            event_id, status_code, max(0, duration_ms),
        )
