# -*- coding: utf-8 -*-
"""Shared helpers for publishing persisted chat messages to room and monitor channels."""

from __future__ import annotations

from typing import Any, Dict, Optional

from models.database import Chatroom
from routes.websocket import websocket_manager
from services.monitor_projection import (
    resolve_chatroom_project as monitor_resolve_chatroom_project,
    serialize_monitor_message_item,
)


async def publish_saved_chat_message(
    db: Any,
    chatroom_id: int,
    *,
    message_id: int,
    content: str,
    agent_name: Optional[str],
    message_type: str,
    created_at: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Broadcast a persisted chat message to room and monitor subscribers."""

    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    room_payload = {
        "type": "chat_message",
        "chatroom_id": chatroom_id,
        "id": message_id,
        "content": content,
        "agent_name": agent_name,
        "message_type": message_type,
        "created_at": created_value,
        "client_turn_id": (metadata or {}).get("client_turn_id"),
    }
    await websocket_manager.broadcast_to_room(room_payload, chatroom_id)

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        return

    project = monitor_resolve_chatroom_project(db, chatroom)
    monitor_payload = serialize_monitor_message_item(
        message_id=message_id,
        chatroom_id=chatroom_id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        agent_name=agent_name,
        content=content,
        message_type=message_type,
        created_at=created_value,
        metadata=metadata,
    )
    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_message",
            "payload": monitor_payload,
        },
        "monitor",
    )
