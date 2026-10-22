"""OpenRouter adapter implementing the provider-neutral Council interface."""

from typing import Any, Dict, List, Optional
from ..openrouter import query_model as _query_model
from ..openrouter import query_models_parallel as _query_models_parallel


async def query_model(
    model: str, messages: List[Dict[str, str]], timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    return await _query_model(model, messages, timeout=timeout)


async def query_models_parallel(
    models: List[str], messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    return await _query_models_parallel(models, messages)
