"""Provider-neutral interface definitions."""

from typing import Any, Dict, List, Optional, Protocol


class ChatProvider(Protocol):
    async def query_model(
        self, model: str, messages: List[Dict[str, str]], timeout: float = 120.0
    ) -> Optional[Dict[str, Any]]: ...

    async def query_models_parallel(
        self, models: List[str], messages: List[Dict[str, str]]
    ) -> Dict[str, Optional[Dict[str, Any]]]: ...
