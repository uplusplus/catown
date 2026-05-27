from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import collaboration_delegate_execution as execution_module


def test_delegate_task_card_content_includes_context_and_task_id():
    content = execution_module.delegate_task_card_content(
        "Run tests",
        "Run the backend suite",
        "Focus on flaky cases",
        "task-123",
    )

    assert "**Task: Run tests**" in content
    assert "Run the backend suite" in content
    assert "Context: Focus on flaky cases" in content
    assert "Task ID: task-123" in content


@pytest.mark.asyncio
async def test_kick_off_delegated_task_execution_publishes_card_and_spawns_runner(monkeypatch):
    published_cards = []
    created_coroutines = []
    runner_calls = []

    async def fake_store_runtime_card(chatroom_id, payload):
        published_cards.append((chatroom_id, payload))

    async def fake_runner(**kwargs):
        runner_calls.append(kwargs)

    async def fake_send_message(**kwargs):
        return None

    async def fake_publish_saved_chat_message(*args, **kwargs):
        return None

    async def fake_trigger_agent_response(*args, **kwargs):
        return None

    monkeypatch.setattr(execution_module, "run_delegated_task_in_chat", fake_runner)

    task = SimpleNamespace(id="task-123", chatroom_id=42)
    client_turn_id = await execution_module.kick_off_delegated_task_execution(
        task=task,
        coordinator=SimpleNamespace(task_registry={}),
        target_agent_type="coder",
        task_title="Run tests",
        task_description="Run the backend suite",
        context="Focus on flaky cases",
        current_agent_name="assistant",
        task_metadata={"task_id": "task-123"},
        store_runtime_card_fn=fake_store_runtime_card,
        send_message_fn=fake_send_message,
        publish_saved_chat_message_fn=fake_publish_saved_chat_message,
        trigger_agent_response_fn=fake_trigger_agent_response,
        create_task_fn=lambda coro: created_coroutines.append(coro) or coro,
        mark_interrupted_fn=lambda **kwargs: None,
    )

    assert client_turn_id == "delegate-task-123"
    assert published_cards == [
        (
            42,
            {
                "type": "agent_message",
                "source": "chatroom",
                "from_agent": "assistant",
                "to_agent": "coder",
                "content": "**Task: Run tests**\n\nRun the backend suite\n\nContext: Focus on flaky cases\n\nTask ID: task-123",
                "client_turn_id": "delegate-task-123",
                "parent_task_run_id": None,
            },
        )
    ]
    assert len(created_coroutines) == 1

    await created_coroutines[0]

    assert len(runner_calls) == 1
    assert runner_calls[0]["task"] is task
    assert runner_calls[0]["client_turn_id"] == "delegate-task-123"
    assert runner_calls[0]["task_metadata"] == {"task_id": "task-123"}


@pytest.mark.asyncio
async def test_run_delegated_task_in_chat_passes_origin_message_id(monkeypatch):
    trigger_calls = []

    class DummyTask:
        id = "task-123"
        chatroom_id = 42
        created_by_agent_id = 7
        status = None
        result = None
        completed_at = None

    task = DummyTask()
    coordinator = SimpleNamespace(task_registry={})

    from agents.collaboration import TaskStatus
    task.status = TaskStatus.DELEGATED

    async def fake_send_message(**kwargs):
        return SimpleNamespace(
            id=99,
            content=kwargs["content"],
            message_type=kwargs["message_type"],
            agent_name=kwargs.get("agent_name"),
            created_at=None,
        )

    async def fake_publish_saved_chat_message(*args, **kwargs):
        return None

    async def fake_trigger_agent_response(*args, **kwargs):
        trigger_calls.append(kwargs)
        return {"completed": False, "awaiting_tool_approval": True, "task_run_id": 11}

    monkeypatch.setattr(execution_module, "SessionLocal", None, raising=False)

    import models.database as database_module

    class DummyDb:
        def close(self):
            return None

    monkeypatch.setattr(database_module, "SessionLocal", lambda: DummyDb())

    await execution_module.run_delegated_task_in_chat(
        task=task,
        coordinator=coordinator,
        target_agent_type="coder",
        task_description="Run the backend suite",
        context="Focus on flaky cases",
        current_agent_name="assistant",
        client_turn_id="delegate-task-123",
        task_metadata={"task_id": "task-123"},
        parent_task_run_id=55,
        send_message_fn=fake_send_message,
        publish_saved_chat_message_fn=fake_publish_saved_chat_message,
        trigger_agent_response_fn=fake_trigger_agent_response,
        mark_interrupted_fn=lambda **kwargs: None,
    )

    assert len(trigger_calls) == 1
    assert trigger_calls[0]["origin_message_id"] == 99
