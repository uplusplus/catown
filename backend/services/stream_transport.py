# -*- coding: utf-8 -*-
"""Shared helpers for rendering streaming runtime events to SSE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict


SerializePayload = Callable[[Any], str]
StoreRuntimeCard = Callable[[int, Dict[str, Any]], Awaitable[Any]]
PublicRuntimeCardPayload = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class StreamTurnRenderResult:
    chunk: str | None = None
    turn_complete_content: str | None = None


def render_sse_payload(payload: Any, *, serialize_payload: SerializePayload) -> str:
    """Serialize a payload into one SSE data chunk."""

    return f"data: {serialize_payload(payload)}\n\n"


async def render_chatroom_runtime_card_sse(
    *,
    event_type: str,
    payload: Dict[str, Any],
    chatroom_id: int,
    client_turn_id: str | None,
    serialize_payload: SerializePayload,
    store_runtime_card: StoreRuntimeCard,
    public_runtime_card_payload: PublicRuntimeCardPayload,
    source: str = "chatroom",
) -> str:
    """Persist and render one runtime card SSE chunk."""

    resolved_payload = dict(payload)
    resolved_payload["type"] = event_type
    resolved_payload["source"] = source
    if client_turn_id:
        resolved_payload["client_turn_id"] = client_turn_id
    await store_runtime_card(chatroom_id, resolved_payload)
    public_payload = public_runtime_card_payload(resolved_payload)
    return render_sse_payload(public_payload, serialize_payload=serialize_payload)


async def render_stream_turn_event(
    event: Dict[str, Any],
    *,
    chatroom_id: int,
    client_turn_id: str | None,
    serialize_payload: SerializePayload,
    store_runtime_card: StoreRuntimeCard,
    public_runtime_card_payload: PublicRuntimeCardPayload,
    source: str = "chatroom",
) -> StreamTurnRenderResult:
    """Render one raw stream-turn event into SSE output or a turn-complete signal."""

    event_type = str(event.get("type") or "")
    if event_type == "runtime_card":
        return StreamTurnRenderResult(
            chunk=await render_chatroom_runtime_card_sse(
                event_type=str(event.get("card_type") or "runtime_card"),
                payload=dict(event.get("payload") or {}),
                chatroom_id=chatroom_id,
                client_turn_id=client_turn_id,
                serialize_payload=serialize_payload,
                store_runtime_card=store_runtime_card,
                public_runtime_card_payload=public_runtime_card_payload,
                source=source,
            )
        )
    if event_type == "turn_complete":
        return StreamTurnRenderResult(turn_complete_content=str(event.get("content") or ""))
    return StreamTurnRenderResult(chunk=render_sse_payload(event, serialize_payload=serialize_payload))
