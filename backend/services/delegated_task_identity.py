"""Shared identity helpers for delegated child task reconstruction."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


def load_task_run_origin_metadata(db: Session, task_run: Any) -> tuple[bool, dict[str, Any]]:
    """Load the origin message metadata for one task run.

    Returns ``(False, {})`` when the task run has no origin message reference.
    Returns ``(True, {})`` when an origin message was expected but cannot be
    loaded or parsed into a dict, so callers can fail closed.
    """

    origin_message = getattr(task_run, "origin_message", None)
    origin_message_id = getattr(task_run, "origin_message_id", None)
    if origin_message is None and origin_message_id is None:
        return False, {}

    if origin_message is None and origin_message_id is not None:
        from models.database import Message

        origin_message = (
            db.query(Message)
            .filter(Message.id == origin_message_id)
            .first()
        )

    if origin_message is None:
        return True, {}

    metadata = _load_json_dict(getattr(origin_message, "metadata_json", None))
    if not metadata:
        return True, {}

    message_chatroom_public_id = str(getattr(origin_message, "chatroom_public_id", "") or "").strip()
    if message_chatroom_public_id:
        metadata["_origin_message_chatroom_public_id"] = message_chatroom_public_id
    return True, metadata


def delegated_metadata_matches(
    metadata: dict[str, Any],
    *,
    child_client_turn_id: str,
    parent_task_run_public_id: str | None = None,
    parent_chatroom_public_id: str | None = None,
) -> bool:
    """Return whether delegated metadata matches the expected child/parent identity."""

    normalized_child_turn_id = str(child_client_turn_id or "").strip()
    if not normalized_child_turn_id:
        return False
    if str(metadata.get("client_turn_id") or "").strip() != normalized_child_turn_id:
        return False

    delegated_task = metadata.get("delegated_task") if isinstance(metadata.get("delegated_task"), dict) else {}
    actual_parent_task_run_public_id = str(
        metadata.get("parent_task_run_public_id")
        or delegated_task.get("parent_task_run_public_id")
        or ""
    ).strip()
    actual_parent_chatroom_public_id = str(
        metadata.get("parent_chatroom_public_id")
        or delegated_task.get("parent_chatroom_public_id")
        or ""
    ).strip()
    origin_message_chatroom_public_id = str(metadata.get("_origin_message_chatroom_public_id") or "").strip()

    if parent_task_run_public_id and actual_parent_task_run_public_id != str(parent_task_run_public_id).strip():
        return False
    if parent_chatroom_public_id and actual_parent_chatroom_public_id != str(parent_chatroom_public_id).strip():
        return False
    if (
        parent_chatroom_public_id
        and origin_message_chatroom_public_id
        and origin_message_chatroom_public_id != str(parent_chatroom_public_id).strip()
    ):
        return False
    return True


def find_matching_delegated_message_metadata(
    db: Session,
    *,
    chatroom_id: int | None,
    child_client_turn_id: str,
    parent_task_run_public_id: str | None = None,
    parent_chatroom_public_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any] | None:
    """Scan persisted messages for one delegated-task metadata match."""

    if chatroom_id is None:
        return None

    from models.database import Message

    rows = (
        db.query(Message)
        .filter(Message.chatroom_id == chatroom_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
        .all()
    )
    for row in rows:
        metadata = _load_json_dict(getattr(row, "metadata_json", None))
        if not metadata:
            continue
        message_chatroom_public_id = str(getattr(row, "chatroom_public_id", "") or "").strip()
        if message_chatroom_public_id:
            metadata["_origin_message_chatroom_public_id"] = message_chatroom_public_id
        if delegated_metadata_matches(
            metadata,
            child_client_turn_id=child_client_turn_id,
            parent_task_run_public_id=parent_task_run_public_id,
            parent_chatroom_public_id=parent_chatroom_public_id,
        ):
            return metadata
    return None


def _load_json_dict(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
