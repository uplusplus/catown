import pytest

from services.single_agent_stream_finalizer import (
    finalize_single_agent_stream_failure,
    finalize_single_agent_stream_success,
)


@pytest.mark.asyncio
async def test_finalize_single_agent_stream_success_persists_and_returns_done_payload():
    calls = []

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return type("Saved", (), {"id": 42, "created_at": None})()

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    result = await finalize_single_agent_stream_success(
        object(),
        None,
        chatroom_id=7,
        client_turn_id="turn-1",
        agent_id=9,
        agent_name="Analyst",
        final_content="Hello world",
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
        compact_summary=lambda content: content[:5],
        completion_summary="Analyst completed the streaming turn.",
        schedule_memory_extraction=lambda: calls.append(("memory", {})),
    )

    assert result.payload == {
        "type": "done",
        "agent_name": "Analyst",
        "message_id": 42,
        "client_turn_id": "turn-1",
    }
    assert [name for name, _ in calls] == ["save", "publish", "complete", "memory"]


@pytest.mark.asyncio
async def test_finalize_single_agent_stream_failure_returns_done_payload_with_persisted_fallback():
    async def persist_failure(db, **kwargs):
        return type("Saved", (), {"id": 99})()

    result = await finalize_single_agent_stream_failure(
        object(),
        None,
        chatroom_id=7,
        client_turn_id="turn-1",
        error=RuntimeError("boom"),
        agent_name="Analyst",
        agent_id=9,
        final_message_saved=False,
        persist_failure=persist_failure,
        failure_summary="Streaming execution failed: boom",
    )

    assert result.payload == {
        "type": "done",
        "agent_name": "Analyst",
        "message_id": 99,
        "client_turn_id": "turn-1",
    }
