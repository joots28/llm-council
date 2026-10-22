"""JSON storage with client ownership checks.

JSON storage remains suitable for local development only. Use a durable database
adapter for multi-instance production deployments.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR

_CONVERSATION_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def ensure_data_dir():
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def _validate_id(conversation_id: str) -> None:
    if not _CONVERSATION_ID.fullmatch(conversation_id):
        raise ValueError("Invalid conversation id")


def get_conversation_path(conversation_id: str) -> str:
    _validate_id(conversation_id)
    root = Path(DATA_DIR).resolve()
    path = (root / f"{conversation_id}.json").resolve()
    if path.parent != root:
        raise ValueError("Invalid conversation id")
    return str(path)


def create_conversation(conversation_id: str, client_id: str = "local") -> Dict[str, Any]:
    ensure_data_dir()
    _validate_id(conversation_id)
    conversation = {
        "id": conversation_id,
        "owner_client_id": client_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": "New Conversation",
        "messages": [],
    }
    save_conversation(conversation)
    return conversation


def get_conversation(
    conversation_id: str, client_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    path = get_conversation_path(conversation_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if client_id is not None and data.get("owner_client_id") != client_id:
        return None
    return data


def save_conversation(conversation: Dict[str, Any]):
    ensure_data_dir()
    path = get_conversation_path(conversation["id"])
    with open(path, "w", encoding="utf-8") as file:
        json.dump(conversation, file, indent=2, ensure_ascii=False)


def list_conversations(client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_data_dir()
    conversations = []
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(DATA_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        if client_id is not None and data.get("owner_client_id") != client_id:
            continue
        conversations.append({
            "id": data["id"],
            "created_at": data["created_at"],
            "title": data.get("title", "New Conversation"),
            "message_count": len(data.get("messages", [])),
        })
    conversations.sort(key=lambda item: item["created_at"], reverse=True)
    return conversations


def add_user_message(conversation_id: str, content: str, client_id: Optional[str] = None):
    conversation = get_conversation(conversation_id, client_id)
    if conversation is None:
        raise ValueError("Conversation not found")
    conversation["messages"].append({"role": "user", "content": content})
    save_conversation(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    client_id: Optional[str] = None,
):
    conversation = get_conversation(conversation_id, client_id)
    if conversation is None:
        raise ValueError("Conversation not found")
    conversation["messages"].append({
        "role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3
    })
    save_conversation(conversation)


def update_conversation_title(
    conversation_id: str, title: str, client_id: Optional[str] = None
):
    conversation = get_conversation(conversation_id, client_id)
    if conversation is None:
        raise ValueError("Conversation not found")
    conversation["title"] = title[:100]
    save_conversation(conversation)
