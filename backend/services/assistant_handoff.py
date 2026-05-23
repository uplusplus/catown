from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Awaitable, Callable, Dict

from agents.identity import normalize_agent_type


_MAX_HANDOFF_DEPTH = 2
_MENTION_PATTERN = re.compile(r"^\s*@([a-zA-Z0-9_-]+)\b")
_LEADING_MENTIONS_PATTERN = re.compile(r"^\s*(?:@[a-zA-Z0-9_-]+\s*)+")


def _normalize_newlines(content: str) -> str:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n")


def _last_nonempty_paragraph_outside_fences(content: str) -> str:
    normalized = _normalize_newlines(content)
    paragraphs: list[list[str]] = []
    current: list[str] = []
    in_fence = False

    for line in normalized.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if current:
                paragraphs.append(current)
                current = []
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line)

    if current:
        paragraphs.append(current)
    if not paragraphs:
        return ""
    return "\n".join(paragraphs[-1]).strip()


def _tail_handoff_paragraph(content: str) -> tuple[list[str], str] | None:
    paragraph = _last_nonempty_paragraph_outside_fences(content)
    if not paragraph:
        return None

    lines = [line for line in paragraph.split("\n") if line.strip()]
    if not lines:
        return None

    first_line = lines[0]
    if not _MENTION_PATTERN.match(first_line):
        return None

    mentioned = [normalize_agent_type(name) for name in re.findall(r"@(\w+)", first_line)]
    body_first_line = _LEADING_MENTIONS_PATTERN.sub("", first_line).strip()
    body_lines: list[str] = []
    if body_first_line:
        body_lines.append(body_first_line)
    body_lines.extend(lines[1:])
    body = "\n".join(body_lines).strip()
    return mentioned, body


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
    extracted = _tail_handoff_paragraph(str(content or ""))
    if extracted is None:
        return []
    all_mentions, _ = extracted
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
    extracted = _tail_handoff_paragraph(content)
    if extracted is not None:
        _, stripped = extracted
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
