from __future__ import annotations

from services.collaboration_delegate_runtime import (
    build_delegated_task_metadata,
    create_delegated_collaboration_task,
    register_delegated_collaboration_task,
)


class DummyCoordinator:
    def __init__(self):
        self.task_registry = {}


def test_build_delegated_task_metadata_returns_normalized_shape():
    metadata = build_delegated_task_metadata(
        task_id="task-123",
        task_title="Run tests",
        task_description="Run the backend test suite",
        context="Focus on flaky tests",
        delegator="assistant",
        target_agent_name="tester",
    )

    assert metadata == {
        "task_id": "task-123",
        "task_title": "Run tests",
        "task_description": "Run the backend test suite",
        "context": "Focus on flaky tests",
        "delegator": "assistant",
        "target_agent_name": "tester",
    }


def test_create_delegated_collaboration_task_preserves_context_fields():
    task = create_delegated_collaboration_task(
        task_title="Run tests",
        task_description="Run the backend test suite",
        chatroom_id=42,
        assigned_to_agent_id=9,
        created_by_agent_id=1,
        context="Focus on flaky tests",
        delegator="assistant",
    )

    assert task.title == "Run tests"
    assert task.description == "Run the backend test suite"
    assert task.chatroom_id == 42
    assert task.assigned_to_agent_id == 9
    assert task.created_by_agent_id == 1
    assert task.metadata == {
        "context": "Focus on flaky tests",
        "delegator": "assistant",
    }


def test_register_delegated_collaboration_task_stores_task():
    coordinator = DummyCoordinator()
    task = create_delegated_collaboration_task(
        task_title="Run tests",
        task_description="Run the backend test suite",
        chatroom_id=42,
        assigned_to_agent_id=9,
        created_by_agent_id=1,
    )

    returned = register_delegated_collaboration_task(task, coordinator=coordinator)

    assert returned is task
    assert coordinator.task_registry == {task.id: task}
