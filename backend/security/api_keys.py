"""Fail-closed API-key authentication for authorized clients.

Configure API_CLIENT_KEY_HASHES as comma-separated client_id:sha256_hex entries.
Only hashes are stored in server configuration; raw client keys are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class ApiClient:
    client_id: str


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
            raise RuntimeError(
                "API_CLIENT_KEY_HASHES must contain comma-separated client_id:sha256_hex entries"
            )
        try:
            int(digest, 16)
        except ValueError as exc:
            raise RuntimeError("API_CLIENT_KEY_HASHES contains an invalid SHA-256 hash") from exc
        clients[client_id] = digest
    return clients


def authenticate_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> ApiClient:
    """Authenticate a bearer key against configured SHA-256 hashes."""
    clients = _configured_hashes()
    if not clients:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    for client_id, expected_hash in clients.items():
        if hmac.compare_digest(supplied_hash, expected_hash):
            return ApiClient(client_id=client_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid API key is required",
        headers={"WWW-Authenticate": "Bearer"},
    )
