"""Private API-key authentication, revocation checks, and per-client rate limits."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .. import database, repository

_bearer = HTTPBearer(auto_error=False)
_rate_buckets = defaultdict(deque)


@dataclass(frozen=True)
class ApiClient:
    client_id: str
    key_id: Optional[str] = None


def _configured_hashes() -> Dict[str, str]:
    raw = os.getenv("API_CLIENT_KEY_HASHES", "")
    clients: Dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        client_id, separator, digest = entry.partition(":")
        client_id, digest = client_id.strip(), digest.strip().lower()
        if not separator or not client_id or len(digest) != 64:
            raise RuntimeError("API_CLIENT_KEY_HASHES must contain client_id:sha256_hex entries")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise RuntimeError("API_CLIENT_KEY_HASHES contains an invalid SHA-256 hash") from exc
        clients[client_id] = digest
    return clients


def _apply_rate_limit(client_id: str) -> None:
    limit = max(1, int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60")))
    now = time.monotonic()
    bucket = _rate_buckets[client_id]
    while bucket and now - bucket[0] >= 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded; retry shortly",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


async def authenticate_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> ApiClient:
    """Authenticate persistent database keys and legacy environment-configured keys."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    client: Optional[ApiClient] = None

    if database.enabled():
        found = await repository.find_active_key(supplied_hash)
        if found:
            client = ApiClient(client_id=found["client_id"], key_id=found["key_id"])

    if client is None:
        for client_id, expected_hash in _configured_hashes().items():
            if hmac.compare_digest(supplied_hash, expected_hash):
                client = ApiClient(client_id=client_id)
                break

    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _apply_rate_limit(client.client_id)
    request.state.client_id = client.client_id
    request.state.api_key_id = client.key_id
    return client


def require_admin_token(request: Request) -> None:
    expected = os.getenv("NEXUS_ADMIN_TOKEN", "")
    supplied = request.headers.get("x-nexus-admin-token", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Administrative API is not configured")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Administrative authorization failed")


def generate_client_key() -> tuple[str, str]:
    raw_key = "nxs_" + secrets.token_urlsafe(36)
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key, digest
