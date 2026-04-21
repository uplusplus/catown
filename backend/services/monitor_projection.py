from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from models.database import Chatroom, Project


INPUT_PRICE_PER_1K = 0.03
OUTPUT_PRICE_PER_1K = 0.06


def parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def compact_preview(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    compact = " ".join(text.strip().split())
    return compact[:limit]


def metadata_client_turn_id(metadata: dict[str, Any]) -> str | None:
    client_turn_id = metadata.get("client_turn_id")
    if isinstance(client_turn_id, str) and client_turn_id:
        return client_turn_id

    card = metadata.get("card")
    if isinstance(card, dict):
        card_turn_id = card.get("client_turn_id")
        if isinstance(card_turn_id, str) and card_turn_id:
            return card_turn_id
    return None


def runtime_entities(card: dict[str, Any]) -> tuple[str | None, str | None]:
    card_type = str(card.get("type") or "runtime")
    agent = card.get("agent")
    from_agent = card.get("from_agent")
    to_agent = card.get("to_agent")

    if card_type == "llm_call":
        return (str(agent) if agent else "agent", "LLM")
    if card_type == "tool_call":
        return (str(agent) if agent else "agent", str(card.get("tool") or "tool"))
    if card_type == "agent_message":
        return (str(from_agent) if from_agent else "agent", str(to_agent) if to_agent else "team")
    if card_type == "boss_instruction":
        return ("Boss", str(agent) if agent else "agent")
    return (str(agent) if agent else None, None)


def extract_prompt_preview(card: dict[str, Any]) -> str:
    prompt_messages = card.get("prompt_messages")
    if not isinstance(prompt_messages, str) or not prompt_messages.strip():
        return ""

    try:
        parsed = json.loads(prompt_messages)
    except json.JSONDecodeError:
        return compact_preview(prompt_messages)

    if isinstance(parsed, list):
        for message in reversed(parsed):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return compact_preview(content)
            if isinstance(content, list):
                chunks: list[str] = []
                for chunk in content:
                    if not isinstance(chunk, dict):
                        continue
                    text = chunk.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
                if chunks:
                    return compact_preview(" ".join(chunks))

    return compact_preview(parsed)


def build_runtime_title(card: dict[str, Any]) -> str:
    card_type = str(card.get("type") or "runtime")
    agent = str(card.get("agent") or card.get("from_agent") or "system")

    if card_type == "llm_call":
        return f"{agent} -> LLM"
    if card_type == "tool_call":
        tool_name = str(card.get("tool") or "tool")
        return f"{agent} used {tool_name}"
    if card_type == "stage_started":
        return f"{agent} started {card.get('display_name') or card.get('stage') or 'stage'}"
    if card_type == "stage_completed":
        return f"{agent} finished {card.get('display_name') or card.get('stage') or 'stage'}"
    if card_type == "gate_blocked":
        return f"Gate blocked at {card.get('stage') or 'pipeline'}"
    if card_type == "gate_rejected":
        return f"Gate rejected by {agent}"
    if card_type == "gate_approved":
        return f"Gate approved by {agent}"
    if card_type == "skill_inject":
        return f"{agent} loaded skills"
    if card_type == "agent_message":
        to_agent = card.get("to_agent")
        if to_agent:
            return f"{agent} -> {to_agent}"
        return f"Agent message from {agent}"
    if card_type == "boss_instruction":
        return f"Boss instruction for {agent}"
    return f"{agent} / {card_type}"


def build_runtime_preview(card: dict[str, Any]) -> str:
    candidates = [
        card.get("response"),
        card.get("result"),
        card.get("content_preview"),
        card.get("content"),
        card.get("summary"),
        card.get("arguments"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            compact = " ".join(candidate.strip().split())
            return compact[:240]
    return ""


def resolve_chatroom_project(db: Session, chatroom: Chatroom | None) -> Project | None:
    current = chatroom
    visited_ids: set[int] = set()
    while current:
        if current.project_id:
            return db.query(Project).filter(Project.id == current.project_id).first()
        if not current.source_chatroom_id or current.source_chatroom_id in visited_ids:
            break
        visited_ids.add(current.source_chatroom_id)
        current = db.query(Chatroom).filter(Chatroom.id == current.source_chatroom_id).first()
    return None


def serialize_monitor_message_item(
    *,
    message_id: int,
    chatroom_id: int,
    chat_title: str,
    project_id: int | None,
    project_name: str | None,
    agent_name: str | None,
    content: str,
    message_type: str,
    created_at: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    normalized_content = content or ""
    normalized_metadata = metadata or {}
    return {
        "id": message_id,
        "chatroom_id": chatroom_id,
        "chat_title": chat_title,
        "project_id": project_id,
        "project_name": project_name,
        "agent_name": agent_name,
        "content": normalized_content,
        "content_preview": " ".join(normalized_content.split())[:220],
        "message_type": message_type,
        "created_at": created_value,
        "client_turn_id": metadata_client_turn_id(normalized_metadata),
    }


def serialize_monitor_runtime_item(
    *,
    runtime_message_id: int,
    chatroom_id: int,
    chat_title: str,
    project_id: int | None,
    project_name: str | None,
    card: dict[str, Any],
    created_at: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    normalized_metadata = metadata or {}
    from_entity, to_entity = runtime_entities(card)
    return {
        "id": runtime_message_id,
        "type": str(card.get("type") or "runtime"),
        "title": build_runtime_title(card),
        "preview": build_runtime_preview(card),
        "created_at": created_value,
        "chatroom_id": chatroom_id,
        "chat_title": chat_title,
        "project_id": project_id,
        "project_name": project_name,
        "agent": card.get("agent") or card.get("from_agent"),
        "from_entity": from_entity,
        "to_entity": to_entity,
        "model": card.get("model"),
        "tool_name": card.get("tool"),
        "success": card.get("success"),
        "tokens_in": int(card.get("tokens_in") or 0),
        "tokens_out": int(card.get("tokens_out") or 0),
        "duration_ms": int(card.get("duration_ms") or 0),
        "turn": int(card.get("turn") or 0) or None,
        "client_turn_id": metadata_client_turn_id(normalized_metadata),
        "prompt_preview": extract_prompt_preview(card),
        "response_preview": compact_preview(card.get("response") or card.get("result")),
        "arguments_preview": compact_preview(card.get("arguments")),
        "stage": card.get("stage") or card.get("display_name"),
    }

