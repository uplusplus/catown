# -*- coding: utf-8 -*-
"""Shared helpers for runtime-card persistence and stream failure fallback."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from agents.identity import DEFAULT_AGENT_TYPE, default_agent_name
from chatrooms.manager import chatroom_manager
from models.database import Chatroom, SessionLocal
from routes.websocket import websocket_manager
from services.chat_publish import publish_saved_chat_message
from services.monitor_projection import (
    resolve_chatroom_project as monitor_resolve_chatroom_project,
    serialize_monitor_runtime_item,
)


_RUNTIME_CARD_PUBLIC_OMITTED_FIELDS = ("system_prompt", "prompt_messages", "raw_response")


def public_runtime_card_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the runtime-card payload that is safe for realtime/chat replay."""

    public_payload = dict(payload)
    if public_payload.get("type") == "llm_call":
        omitted = any(public_payload.get(field) for field in _RUNTIME_CARD_PUBLIC_OMITTED_FIELDS)
        for field in _RUNTIME_CARD_PUBLIC_OMITTED_FIELDS:
            public_payload.pop(field, None)
        if omitted:
            public_payload["debug_payload_omitted"] = True
    return public_payload


async def publish_runtime_card_event(
    db: Any,
    chatroom_id: int,
    *,
    runtime_message_id: int,
    created_at: Any,
    card_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Publish a persisted runtime card to room and monitor channels."""

    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    room_card_payload = dict(card_payload)
    room_card_payload.setdefault("created_at", created_value)
    room_card_payload.setdefault("runtime_message_id", runtime_message_id)

    await websocket_manager.broadcast_to_room(
        {
            "type": "runtime_card",
            "chatroom_id": chatroom_id,
            "card": room_card_payload,
        },
        chatroom_id,
    )

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        return

    project = monitor_resolve_chatroom_project(db, chatroom)
    monitor_payload = serialize_monitor_runtime_item(
        runtime_message_id=runtime_message_id,
        chatroom_id=chatroom_id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        card=room_card_payload,
        created_at=created_value,
        metadata=metadata,
    )
    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_runtime",
            "payload": monitor_payload,
        },
        "monitor",
    )


async def store_runtime_card(chatroom_id: int, payload: Dict[str, Any]) -> Any:
    """Persist a runtime card and publish its public payload."""

    card_payload = dict(payload)
    public_card = public_runtime_card_payload(card_payload)
    runtime_message = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,
        content=card_payload.get("type", "runtime_card"),
        message_type="runtime_card",
        metadata={"card": card_payload},
    )
    db = SessionLocal()
    try:
        await publish_runtime_card_event(
            db,
            chatroom_id,
            runtime_message_id=runtime_message.id,
            created_at=runtime_message.created_at,
            card_payload=public_card,
            metadata={"card": public_card, "client_turn_id": public_card.get("client_turn_id")},
        )
    finally:
        db.close()
    return runtime_message


def summarize_stream_error(error_message: str, limit: int = 240) -> str:
    """Compact a stream error for user-visible fallback messages."""

    text = str(error_message or "").strip()
    if not text:
        return "Unknown streaming error"
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


async def persist_stream_failure(
    db: Any,
    *,
    chatroom_id: int,
    client_turn_id: Optional[str],
    error_message: str,
    message_metadata: Callable[[Optional[str], Optional[Dict[str, Any]]], Dict[str, Any]],
    agent_name: Optional[str] = None,
    agent_id: Optional[int] = None,
    detail: Optional[str] = None,
) -> Any:
    """Persist a visible fallback message plus a runtime error card for failed SSE turns."""

    safe_agent_name = (agent_name or default_agent_name(DEFAULT_AGENT_TYPE)).strip() or default_agent_name(DEFAULT_AGENT_TYPE)
    error_summary = summarize_stream_error(error_message)
    detail_text = str(detail or error_message or "").strip() or error_summary
    failure_text = (
        "本轮执行中断，未生成最终答复。\n\n"
        f"错误摘要: {error_summary}"
    )

    try:
        db.rollback()
    except Exception:
        pass

    error_card = {
        "type": "agent_error",
        "source": "chatroom",
        "agent": safe_agent_name,
        "summary": "Stream failed before a final reply was saved.",
        "error": error_summary,
        "content": f"### Stream Failure\n\n- Agent: `{safe_agent_name}`\n- Error: `{error_summary}`\n\n```text\n{detail_text}\n```",
    }
    if client_turn_id:
        error_card["client_turn_id"] = client_turn_id

    await store_runtime_card(chatroom_id, error_card)

    saved = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content=failure_text,
        message_type="text",
        metadata=message_metadata(
            client_turn_id,
            {
                "stream_failure": {
                    "agent": safe_agent_name,
                    "error": error_summary,
                }
            },
        ),
        agent_name=safe_agent_name,
    )
    await publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=saved.id,
        content=failure_text,
        agent_name=safe_agent_name,
        message_type="text",
        created_at=saved.created_at,
        metadata=message_metadata(
            client_turn_id,
            {
                "stream_failure": {
                    "agent": safe_agent_name,
                    "error": error_summary,
                }
            },
        ),
    )
    return saved
