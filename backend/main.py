"""FastAPI application for the LLM Council and private Nexus API."""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import database, repository
from .config import CHAIRMAN_MODEL, COUNCIL_MODELS, CORS_ALLOWED_ORIGINS, MAX_REQUEST_CHARS
from .council import (
    calculate_aggregate_rankings,
    generate_conversation_title,
    run_full_council,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .openrouter import query_model
from .schemas.chat import ChatCompletionRequest
from .security.api_keys import (
    ApiClient, authenticate_api_key, generate_client_key, require_admin_token,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize configured durable storage and release the connection pool."""
    await database.connect()
    try:
        yield
    finally:
        await database.close()


app = FastAPI(
    title="Nexus AI API",
    version="1.0.0",
    description="Private, authenticated API for the multi-model LLM Council.",
    lifespan=lifespan,
)


@app.middleware("http")
async def usage_audit_middleware(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    client_id = getattr(request.state, "client_id", None)
    key_id = getattr(request.state, "api_key_id", None)
    usage_event_id = getattr(request.state, "usage_event_id", 0)
    if client_id:
        route = getattr(request.scope.get("route"), "path", request.url.path)
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            if usage_event_id:
                await repository.complete_usage(usage_event_id, response.status_code, duration_ms)
            else:
                await repository.record_usage(
                    client_id, key_id, request.method, route, response.status_code, duration_ms,
                )
        except Exception:
            logger.warning("Unable to persist API usage event")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)


class CreateApiKeyRequest(BaseModel):
    client_id: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(default="", max_length=120)


class CreatedApiKey(BaseModel):
    key_id: str
    client_id: str
    api_key: str
    warning: str = "This key is shown once. Store it securely."


class ConversationMetadata(BaseModel):
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


def _validate_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="At least one non-empty message is required")
    if len(prompt) > MAX_REQUEST_CHARS:
        raise HTTPException(status_code=413, detail="Request content exceeds the configured limit")
    return prompt


async def _get_owned_conversation(conversation_id: str, client_id: str):
    try:
        uuid.UUID(conversation_id)
    except (ValueError, TypeError, AttributeError):
        return None
    # Missing/foreign conversations are deliberately indistinguishable. Database
    # failures are not converted to 404, so operators can detect service outages.
    return await repository.get_conversation(conversation_id, client_id)


def _combine_messages(messages) -> str:
    parts = []
    for message in messages:
        content = message.content.strip()
        if content:
            parts.append(f"{message.role.upper()}:\n{content}")
    return _validate_prompt("\n\n".join(parts))


def _completion_response(model: str, content: str) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    }


@app.get("/")
async def root():
    """Basic liveness endpoint; intentionally returns no configuration details."""
    return {"status": "ok", "service": "Nexus AI API"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models", dependencies=[Depends(authenticate_api_key)])
async def list_models():
    """List the supported logical model and allowlisted provider models."""
    model_ids = ["nexus-council", *dict.fromkeys([*COUNCIL_MODELS, CHAIRMAN_MODEL])]
    return {
        "object": "list",
        "data": [{"id": model_id, "object": "model", "owned_by": "nexus"} for model_id in model_ids],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    client: ApiClient = Depends(authenticate_api_key),
):
    """OpenAI-style chat completions for the Council or allowlisted provider models."""
    del client  # Authentication is required; request data contains no trusted client identity.
    prompt = _combine_messages(request.messages)
    if request.stream:
        raise HTTPException(
            status_code=400,
            detail="Use /v1/council/completions/stream for staged streaming responses",
        )

    if request.model == "nexus-council":
        _, _, final_result, _ = await run_full_council(prompt)
        content = final_result.get("response", "")
        if final_result.get("model") == "error" or not content:
            raise HTTPException(status_code=502, detail="The Council could not produce a response")
    elif request.model in set(COUNCIL_MODELS + [CHAIRMAN_MODEL]):
        result = await query_model(request.model, [{"role": "user", "content": prompt}])
        if result is None or not result.get("content"):
            raise HTTPException(status_code=502, detail="The configured model did not return a response")
        content = result["content"]
    else:
        raise HTTPException(status_code=400, detail="Model is not in the server allowlist")

    return _completion_response(request.model, content)


@app.post("/v1/council/completions")
async def council_completions(
    request: ChatCompletionRequest,
    client: ApiClient = Depends(authenticate_api_key),
):
    """Return all three Council stages and their aggregate metadata."""
    del client
    if request.model != "nexus-council":
        raise HTTPException(status_code=400, detail="Use model 'nexus-council' for Council completions")
    if request.stream:
        raise HTTPException(status_code=400, detail="Use /v1/council/completions/stream for streaming")
    prompt = _combine_messages(request.messages)
    stage1, stage2, stage3, metadata = await run_full_council(prompt)
    if not stage1 or not stage3.get("response"):
        raise HTTPException(status_code=502, detail="The Council could not produce a complete response")
    return {
        "id": f"council-{uuid.uuid4().hex}",
        "object": "council.completion",
        "model": "nexus-council",
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "metadata": metadata,
    }


@app.post("/v1/council/completions/stream")
async def council_completions_stream(
    request: ChatCompletionRequest,
    client: ApiClient = Depends(authenticate_api_key),
):
    """Stream Council stage events using Server-Sent Events."""
    del client
    if request.model != "nexus-council":
        raise HTTPException(status_code=400, detail="Use model 'nexus-council' for Council streaming")
    prompt = _combine_messages(request.messages)

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1 = await stage1_collect_responses(prompt)
            if not stage1:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Council models did not respond'})}\n\n"
                return
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1})}\n\n"

            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2, label_to_model = await stage2_collect_rankings(prompt, stage1)
            aggregate = calculate_aggregate_rankings(stage2, label_to_model)
            metadata = {"label_to_model": label_to_model, "aggregate_rankings": aggregate}
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2, 'metadata': metadata})}\n\n"

            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3 = await stage3_synthesize_final(prompt, stage1, stage2)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3})}\n\n"
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not expose provider errors, credentials, or internal exception details.
            yield f"data: {json.dumps({'type': 'error', 'message': 'Council request failed'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/admin/keys", response_model=CreatedApiKey, dependencies=[Depends(require_admin_token)])
async def create_api_key(request: CreateApiKeyRequest):
    """Create a client key. The raw secret is returned once and never stored."""
    raw_key, digest = generate_client_key()
    try:
        key_id = await repository.create_client_key(
            request.client_id, request.display_name, digest
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CreatedApiKey(key_id=key_id, client_id=request.client_id, api_key=raw_key)


@app.get("/v1/admin/keys", dependencies=[Depends(require_admin_token)])
async def list_api_keys(client_id: str | None = None):
    """List key metadata for administration without exposing key material."""
    try:
        return {"data": await repository.list_client_keys(client_id)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Persistent key administration requires DATABASE_URL") from exc


@app.delete("/v1/admin/keys/{key_id}", dependencies=[Depends(require_admin_token)])
async def revoke_api_key(key_id: str):
    """Revoke a database-backed key immediately."""
    try:
        revoked = await repository.revoke_client_key(key_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid key id or database configuration") from exc
    if not revoked:
        raise HTTPException(status_code=404, detail="Active API key not found")
    return {"status": "revoked", "key_id": key_id}


@app.get("/v1/usage/me")
async def get_my_usage(
    days: int = 30,
    client: ApiClient = Depends(authenticate_api_key),
):
    if not database.enabled():
        raise HTTPException(status_code=503, detail="Usage reporting requires DATABASE_URL")
    return await repository.usage_summary(client.client_id, days)


# Legacy UI endpoints remain available but are now authenticated and client-scoped.
@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations(client: ApiClient = Depends(authenticate_api_key)):
    return await repository.list_conversations(client.client_id)


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(
    request: CreateConversationRequest,
    client: ApiClient = Depends(authenticate_api_key),
):
    del request
    conversation_id = str(uuid.uuid4())
    return await repository.create_conversation(conversation_id, client.client_id)


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: str,
    client: ApiClient = Depends(authenticate_api_key),
):
    conversation = await _get_owned_conversation(conversation_id, client.client_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    client: ApiClient = Depends(authenticate_api_key),
):
    conversation = await _get_owned_conversation(conversation_id, client.client_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    prompt = _validate_prompt(request.content)
    is_first_message = len(conversation["messages"]) == 0
    await repository.add_user_message(conversation_id, prompt, client.client_id)
    if is_first_message:
        title = await generate_conversation_title(prompt)
        await repository.update_conversation_title(conversation_id, title, client.client_id)
    stage1, stage2, stage3, metadata = await run_full_council(prompt)
    await repository.add_assistant_message(conversation_id, stage1, stage2, stage3, client.client_id)
    return {"stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata}


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(
    conversation_id: str,
    request: SendMessageRequest,
    client: ApiClient = Depends(authenticate_api_key),
):
    conversation = await _get_owned_conversation(conversation_id, client.client_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    prompt = _validate_prompt(request.content)
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        title_task = None
        try:
            await repository.add_user_message(conversation_id, prompt, client.client_id)
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(prompt))
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1 = await stage1_collect_responses(prompt)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1})}\n\n"
            if not stage1:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Council models did not respond'})}\n\n"
                return
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2, label_to_model = await stage2_collect_rankings(prompt, stage1)
            aggregate = calculate_aggregate_rankings(stage2, label_to_model)
            metadata = {"label_to_model": label_to_model, "aggregate_rankings": aggregate}
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2, 'metadata': metadata})}\n\n"
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3 = await stage3_synthesize_final(prompt, stage1, stage2)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3})}\n\n"
            if title_task:
                title = await title_task
                await repository.update_conversation_title(conversation_id, title, client.client_id)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"
            await repository.add_assistant_message(conversation_id, stage1, stage2, stage3, client.client_id)
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        except asyncio.CancelledError:
            if title_task and not title_task.done():
                title_task.cancel()
            raise
        except Exception:
            if title_task and not title_task.done():
                title_task.cancel()
            yield f"data: {json.dumps({'type': 'error', 'message': 'Council request failed'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").getenv("PORT", "8001")))
