from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Awaitable, Callable, Dict

from agents.identity import normalize_agent_type


_MAX_HANDOFF_DEPTH = 2
_MENTION_PATTERN = re.compile(r"^\s*@([a-zA-Z0-9_-]+)\b")
_LEADING_MENTIONS_PATTERN = re.compile(r"^\s*(?:@[a-zA-Z0-9_-]+\s*)+")


def handoff_targets(*, content: str, agent_name: str | None, metadata: dict[str, Any] | None) -> list[str]:
    normalized_agent = normalize_agent_type(agent_name or "")
    if not normalized_agent:
        return []
    metadata = metadata if isinstance(metadata, dict) else {}
    if bool(metadata.get("auto_handoff_consumed")):
        return []
    depth = int(metadata.get("handoff_depth") or 0)
    if depth >= _MAX_HANDOFF_DEPTH:
        return []
    match = _MENTION_PATTERN.match(str(content or ""))
    if not match:
        return []
    all_mentions = [normalize_agent_type(name) for name in re.findall(r"@(\w+)", str(content or ""))]
    unique_mentions: list[str] = []
    seen = set()
    for target_agent in all_mentions:
        if not target_agent or target_agent == normalized_agent or target_agent in seen:
            continue
        seen.add(target_agent)
        unique_mentions.append(target_agent)
    return unique_mentions


def build_handoff_metadata(base_metadata: dict[str, Any] | None, *, from_agent: str, to_agent: str, source_message_id: int | None) -> dict[str, Any]:
    base_metadata = dict(base_metadata or {})
    next_depth = int(base_metadata.get("handoff_depth") or 0) + 1
    return {
        **base_metadata,
        "handoff_depth": next_depth,
        "handoff_from_agent": from_agent,
        "handoff_to_agent": to_agent,
        "handoff_source_message_id": source_message_id,
        "auto_handoff_consumed": True,
    }


def build_handoff_trigger_content(content: str, *, target_agent: str) -> str:
    stripped = _LEADING_MENTIONS_PATTERN.sub("", str(content or "")).strip()
    if stripped:
        return f"@{target_agent} {stripped}"
    return f"@{target_agent}"


async def maybe_schedule_assistant_handoff(
    *,
    chatroom_id: int,
    content: str,
    agent_name: str | None,
    metadata: dict[str, Any] | None,
    saved_message_id: int | None,
    publish_agent_message_card: Callable[..., Awaitable[Any]],
    trigger_agent_response: Callable[..., Awaitable[Any]],
) -> str | None:
    targets = handoff_targets(content=content, agent_name=agent_name, metadata=metadata)
    if not targets:
        return None
    from_agent = str(agent_name or "").strip() or "agent"
    for target_agent in targets:
        handoff_turn_id = f"handoff-{uuid.uuid4()}"
        handoff_metadata = build_handoff_metadata(
            metadata,
            from_agent=from_agent,
            to_agent=target_agent,
            source_message_id=saved_message_id,
        )

        await publish_agent_message_card(
            chatroom_id=chatroom_id,
            from_agent=from_agent,
            to_agent=target_agent,
            content=content,
            client_turn_id=handoff_turn_id,
            extra_metadata=handoff_metadata,
        )

        asyncio.create_task(
            trigger_agent_response(
                chatroom_id,
                build_handoff_trigger_content(content, target_agent=target_agent),
                client_turn_id=handoff_turn_id,
                extra_context=(
                    f"Automatic handoff from {from_agent} to {target_agent}."
                ),
            )
        )
    return targets[0]
