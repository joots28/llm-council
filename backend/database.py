"""Async PostgreSQL connection lifecycle and idempotent schema initialization."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def enabled() -> bool:
    return bool(database_url())


async def connect() -> None:
    global _pool
    url = database_url()
    if not url or _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        url,
        min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
        max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
        command_timeout=float(os.getenv("DB_COMMAND_TIMEOUT_SECONDS", "10")),
        server_settings={"application_name": "nexus-ai-api"},
    )
    migration = Path(__file__).resolve().parent.parent / "migrations" / "001_initial.sql"
    async with _pool.acquire() as connection:
        await connection.execute(migration.read_text(encoding="utf-8"))


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DATABASE_URL is set but the database pool is not initialized")
    return _pool
