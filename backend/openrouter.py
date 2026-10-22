"""OpenRouter HTTP client. Provider-specific behavior stays behind the adapter."""

import asyncio
import logging
import os
from typing import List, Dict, Any, Optional

import httpx

from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL

logger = logging.getLogger(__name__)
_PROVIDER_CONCURRENCY = max(1, int(os.getenv("MAX_CONCURRENT_PROVIDER_CALLS", "8")))
_provider_semaphore = asyncio.Semaphore(_PROVIDER_CONCURRENCY)


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
) -> Optional[Dict[str, Any]]:
    """Query one model; return None on provider failure without leaking details."""
    if not OPENROUTER_API_KEY:
        logger.error("OpenRouter is not configured; OPENROUTER_API_KEY is missing")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}

    try:
        async with _provider_semaphore:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                choices = data.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    logger.warning("Provider returned an empty completion for model %s", model)
                    return None
                message = choices[0].get("message") or {}
                return {
                    "content": message.get("content"),
                    "reasoning_details": message.get("reasoning_details"),
                }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # Avoid logging raw provider exceptions or payloads that could contain sensitive data.
        logger.warning("OpenRouter request failed for model %s", model)
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Query the configured models concurrently."""
    import asyncio

    responses = await asyncio.gather(
        *(query_model(model, messages) for model in models),
        return_exceptions=True,
    )
    normalized: Dict[str, Optional[Dict[str, Any]]] = {}
    for model, response in zip(models, responses):
        if isinstance(response, asyncio.CancelledError):
            raise response
        if isinstance(response, BaseException):
            logger.warning("Provider task failed for model %s", model)
            normalized[model] = None
        else:
            normalized[model] = response
    return normalized
