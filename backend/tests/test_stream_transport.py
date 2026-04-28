import pytest

from services.stream_transport import (
    render_chatroom_runtime_card_sse,
    render_sse_payload,
    render_stream_turn_event,
)


def test_render_sse_payload_formats_chunk():
    assert render_sse_payload({"type": "done"}, serialize_payload=lambda payload: '{"type":"done"}') == 'data: {"type":"done"}\n\n'


@pytest.mark.asyncio
async def test_render_chatroom_runtime_card_sse_persists_and_returns_public_payload():
    stored = []

    async def store_runtime_card(chatroom_id, payload):
        stored.append((chatroom_id, payload))

    chunk = await render_chatroom_runtime_card_sse(
        event_type="llm_call",
        payload={"agent": "Analyst", "system_prompt": "hidden"},
        chatroom_id=7,
        client_turn_id="turn-1",
        serialize_payload=lambda payload: '{"type":"llm_call","agent":"Analyst"}',
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=lambda payload: {"type": payload["type"], "agent": payload["agent"]},
    )

    assert stored[0][0] == 7
    assert stored[0][1]["type"] == "llm_call"
    assert stored[0][1]["source"] == "chatroom"
    assert stored[0][1]["client_turn_id"] == "turn-1"
    assert chunk == 'data: {"type":"llm_call","agent":"Analyst"}\n\n'


@pytest.mark.asyncio
async def test_render_stream_turn_event_handles_runtime_card_turn_complete_and_plain_event():
    stored = []

    async def store_runtime_card(chatroom_id, payload):
        stored.append(payload)

    runtime_card = await render_stream_turn_event(
        {"type": "runtime_card", "card_type": "llm_call", "payload": {"agent": "Analyst"}},
        chatroom_id=9,
        client_turn_id="turn-1",
        serialize_payload=lambda payload: '{"type":"llm_call","agent":"Analyst"}',
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=lambda payload: {"type": payload["type"], "agent": payload["agent"]},
    )
    turn_complete = await render_stream_turn_event(
        {"type": "turn_complete", "content": "Hello"},
        chatroom_id=9,
        client_turn_id="turn-1",
        serialize_payload=lambda payload: "{}",
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=lambda payload: payload,
    )
    plain = await render_stream_turn_event(
        {"type": "content", "delta": "Hi"},
        chatroom_id=9,
        client_turn_id="turn-1",
        serialize_payload=lambda payload: '{"type":"content","delta":"Hi"}',
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=lambda payload: payload,
    )

    assert runtime_card.chunk == 'data: {"type":"llm_call","agent":"Analyst"}\n\n'
    assert turn_complete.turn_complete_content == "Hello"
    assert plain.chunk == 'data: {"type":"content","delta":"Hi"}\n\n'
