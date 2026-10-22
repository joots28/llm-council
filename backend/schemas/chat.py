"""Schemas for OpenAI-style chat completion requests."""

from typing import Literal, List
from pydantic import BaseModel, Field, ConfigDict


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="nexus-council", min_length=1, max_length=100)
    messages: List[ChatMessage] = Field(min_length=1, max_length=40)
    stream: bool = False

    model_config = ConfigDict(extra="forbid")
