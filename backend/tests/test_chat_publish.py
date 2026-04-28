import importlib

import pytest


@pytest.mark.asyncio
async def test_publish_saved_chat_message_broadcasts_room_and_monitor(monkeypatch, fresh_db):
    import services.chat_publish as publish_mod

    publish_mod = importlib.reload(publish_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    room_calls = []
    topic_calls = []
    try:
        project = fresh_db.Project(name="Publish Project", status="active")
        db.add(project)
        db.commit()
        db.refresh(project)
        chatroom = fresh_db.Chatroom(project_id=project.id, title="Publish Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        async def fake_broadcast_to_room(message, chatroom_id):
            room_calls.append((message, chatroom_id))

        async def fake_broadcast_to_topic(message, topic):
            topic_calls.append((message, topic))

        monkeypatch.setattr(publish_mod.websocket_manager, "broadcast_to_room", fake_broadcast_to_room)
        monkeypatch.setattr(publish_mod.websocket_manager, "broadcast_to_topic", fake_broadcast_to_topic)

        await publish_mod.publish_saved_chat_message(
            db,
            chatroom.id,
            message_id=7,
            content="Hello",
            agent_name="Analyst",
            message_type="text",
            created_at="2026-04-28T00:00:00",
            metadata={"client_turn_id": "turn-1"},
        )

        assert room_calls[0][1] == chatroom.id
        assert room_calls[0][0]["type"] == "chat_message"
        assert room_calls[0][0]["client_turn_id"] == "turn-1"
        assert topic_calls[0][1] == "monitor"
        assert topic_calls[0][0]["type"] == "monitor_message"
    finally:
        db.close()
