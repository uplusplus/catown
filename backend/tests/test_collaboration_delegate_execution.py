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
            },
        )
    ]
    assert len(created_coroutines) == 1

    await created_coroutines[0]

    assert len(runner_calls) == 1
    assert runner_calls[0]["task"] is task
    assert runner_calls[0]["client_turn_id"] == "delegate-task-123"
    assert runner_calls[0]["task_metadata"] == {"task_id": "task-123"}
