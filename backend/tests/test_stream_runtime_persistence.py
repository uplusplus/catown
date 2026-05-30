import importlib
from unittest.mock import AsyncMock

import pytest


def test_public_runtime_card_payload_omits_debug_fields():
    import services.stream_runtime_persistence as persistence_mod

    persistence_mod = importlib.reload(persistence_mod)
    public = persistence_mod.public_runtime_card_payload(
        {
            "type": "llm_call",
            "agent": "Analyst",
            "system_prompt": "hidden",
            "prompt_messages": [{"role": "user", "content": "hi"}],
            "raw_response": {"id": "x"},
        }
    )

    assert public["type"] == "llm_call"
    assert public["agent"] == "Analyst"
    assert public["debug_payload_omitted"] is True
    assert "system_prompt" not in public
    assert "prompt_messages" not in public
    assert "raw_response" not in public


@pytest.mark.asyncio
async def test_publish_runtime_card_event_notifies_process_projection(monkeypatch, fresh_db):
    import services.stream_runtime_persistence as persistence_mod

    persistence_mod = importlib.reload(persistence_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    room_calls = []
    topic_calls = []
    try:
        chatroom = fresh_db.Chatroom(title="Runtime Card Process Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        async def fake_broadcast_to_room(message, chatroom_id):
            room_calls.append((message, chatroom_id))

        async def fake_broadcast_to_topic(message, topic):
            topic_calls.append((message, topic))

        monkeypatch.setattr(persistence_mod.websocket_manager, "broadcast_to_room", fake_broadcast_to_room)
        monkeypatch.setattr(persistence_mod.websocket_manager, "broadcast_to_topic", fake_broadcast_to_topic)

        await persistence_mod.publish_runtime_card_event(
            db,
            chatroom.id,
            runtime_message_id=12,
            created_at="2026-05-12T10:00:00",
            card_payload={"type": "tool_call", "tool": "run_shell"},
        )

        room_messages = [call[0] for call in room_calls]
        assert any(message.get("type") == "runtime_card" for message in room_messages)
        process_message = next(message for message in room_messages if message.get("type") == "chat_processes_changed")
        assert process_message["chatroom_id"] == chatroom.id
        assert process_message["reason"] == "runtime_card"
        assert topic_calls
        monitor_payload = topic_calls[0][0]["payload"]
        assert topic_calls[0][0]["type"] == "monitor_runtime"
        assert monitor_payload["type"] == "tool_call"
        assert monitor_payload["operation_label"] == "run_shell"
        assert len(monitor_payload["brain_events"]) == 2
        assert monitor_payload["brain_events"][0]["phase"] == "outbound"
        assert monitor_payload["brain_events"][1]["phase"] == "inbound"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_persist_stream_failure_creates_visible_fallback_and_uses_shared_store(monkeypatch):
    import services.stream_runtime_persistence as persistence_mod

    persistence_mod = importlib.reload(persistence_mod)
    stored_cards = []
    published_messages = []

    async def fake_store_runtime_card(chatroom_id, payload):
        stored_cards.append((chatroom_id, payload))

    saved_message = type("Saved", (), {"id": 55, "created_at": None})()

    async def fake_send_message(**kwargs):
        return saved_message

    async def fake_publish_saved_chat_message(*args, **kwargs):
        published_messages.append(kwargs)

    monkeypatch.setattr(persistence_mod, "store_runtime_card", fake_store_runtime_card)
    monkeypatch.setattr(persistence_mod.chatroom_manager, "send_message", fake_send_message)
    monkeypatch.setattr(persistence_mod, "publish_saved_chat_message", fake_publish_saved_chat_message)

    result = await persistence_mod.persist_stream_failure(
        type("DB", (), {"rollback": lambda self: None})(),
        chatroom_id=7,
        client_turn_id="turn-1",
        error_message="boom",
        message_metadata=lambda client_turn_id, extra=None: {"client_turn_id": client_turn_id, **(extra or {})},
        agent_name="Analyst",
        agent_id=9,
        detail="trace",
    )

    assert result.id == 55
    assert stored_cards[0][0] == 7
    assert stored_cards[0][1]["type"] == "agent_error"
    assert "本轮执行中断" in published_messages[0]["content"]


@pytest.mark.asyncio
async def test_store_runtime_card_persists_projection(monkeypatch, fresh_db):
    import services.stream_runtime_persistence as persistence_mod

    persistence_mod = importlib.reload(persistence_mod)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Runtime Store Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
    finally:
        db.close()

    publish_mock = AsyncMock()
    monkeypatch.setattr(persistence_mod, "publish_runtime_card_event", publish_mock)

    message = await persistence_mod.store_runtime_card(
        chatroom.id,
        {
            "type": "tool_call",
            "agent": "Developer",
            "tool": "write_file",
            "arguments": "{\"file_path\":\"src/app.py\",\"content\":\"print('ok')\"}",
            "success": True,
            "result": "ok",
            "run_id": 77,
        },
    )

    db = fresh_db.SessionLocal()
    try:
        projection = (
            db.query(fresh_db.RuntimeCardProjection)
            .filter(fresh_db.RuntimeCardProjection.message_id == message.id)
            .first()
        )
        assert projection is not None
        assert projection.tool_name == "write_file"
        assert projection.task_run_id == 77
        assert projection.success is True
    finally:
        db.close()
