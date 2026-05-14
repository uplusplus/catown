from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from services.collaboration_task_runtime import (
    CollaborationTaskRuntimeError,
    build_collaboration_task_payload,
    list_collaboration_task_payloads,
    render_collaboration_task_status_text,
    require_collaboration_task,
    resolve_collaboration_task,
)


class DummyCoordinator:
    def __init__(self, tasks=None):
        self.task_registry = tasks or {}

    def get_task_status(self, task_id: str):
        return self.task_registry.get(task_id)


def test_resolve_collaboration_task_rebuilds_refreshes_and_registers(monkeypatch):
    from agents import collaboration as collaboration_module

    coordinator = DummyCoordinator()
    rebuilt_task = SimpleNamespace(id="normalized-id", chatroom_id=7, marker="rebuilt")
    refreshed_task = SimpleNamespace(id="normalized-id", chatroom_id=7, marker="refreshed")

    monkeypatch.setattr(
        collaboration_module,
        "normalize_delegated_task_id",
        lambda task_id: "normalized-id",
    )
    monkeypatch.setattr(
        collaboration_module,
        "rebuild_collaboration_task_from_runtime",
        lambda task_id: rebuilt_task,
    )
    monkeypatch.setattr(
        collaboration_module,
        "refresh_collaboration_task_from_runtime",
        lambda task: refreshed_task if task is rebuilt_task else task,
    )

    resolved = resolve_collaboration_task("delegate-normalized-id", coordinator=coordinator)

    assert resolved is refreshed_task
    assert coordinator.task_registry == {"normalized-id": refreshed_task}


def test_require_collaboration_task_raises_when_missing(monkeypatch):
    from agents import collaboration as collaboration_module

    coordinator = DummyCoordinator()
    monkeypatch.setattr(
        collaboration_module,
        "normalize_delegated_task_id",
        lambda task_id: str(task_id),
    )
    monkeypatch.setattr(
        collaboration_module,
        "rebuild_collaboration_task_from_runtime",
        lambda task_id: None,
    )
    monkeypatch.setattr(
        collaboration_module,
        "refresh_collaboration_task_from_runtime",
        lambda task: task,
    )

    with pytest.raises(CollaborationTaskRuntimeError) as exc_info:
        require_collaboration_task("missing-task", coordinator=coordinator)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Task not found"


def test_list_collaboration_task_payloads_filters_chatroom_and_refreshes(monkeypatch):
    from agents import collaboration as collaboration_module

    first = SimpleNamespace(id="task-1", chatroom_id=1)
    second = SimpleNamespace(id="task-2", chatroom_id=2)
    coordinator = DummyCoordinator({"task-1": first, "task-2": second})

    monkeypatch.setattr(
        collaboration_module,
        "refresh_collaboration_task_from_runtime",
        lambda task: task,
    )
    monkeypatch.setattr(
        collaboration_module,
        "collaboration_task_response_payload",
        lambda task: {"id": task.id, "chatroom_id": task.chatroom_id},
    )

    payloads = list_collaboration_task_payloads(chatroom_id=2, coordinator=coordinator)

    assert payloads == [{"id": "task-2", "chatroom_id": 2}]
    assert coordinator.task_registry == {"task-1": first, "task-2": second}


def test_build_collaboration_task_payload_uses_shared_projection(monkeypatch):
    from agents import collaboration as collaboration_module

    task = SimpleNamespace(id="task-1")
    monkeypatch.setattr(
        collaboration_module,
        "collaboration_task_response_payload",
        lambda payload_task: {"id": payload_task.id, "status": "completed"},
    )

    payload = build_collaboration_task_payload(task)

    assert payload == {"id": "task-1", "status": "completed"}


def test_render_collaboration_task_status_text_uses_payload_details(monkeypatch):
    from agents import collaboration as collaboration_module

    task = SimpleNamespace(id="task-1")
    monkeypatch.setattr(
        collaboration_module,
        "collaboration_task_response_payload",
        lambda payload_task: {
            "id": payload_task.id,
            "title": "Run tests",
            "status": "completed",
            "assigned_to": 3,
            "result": "pytest finished",
            "result_details": {
                "command": "pytest backend/tests -q",
                "phase": "completed",
                "duration_ms": 6400,
                "tail_output": "12 passed, 1 failed",
                "passed_count": 12,
                "failed_count": 1,
                "next_step": "Inspect failed tests and logs.",
            },
            "created_at": datetime(2026, 5, 12, 9, 0, 0).isoformat(),
            "completed_at": datetime(2026, 5, 12, 9, 1, 0).isoformat(),
        },
    )

    rendered = render_collaboration_task_status_text(task)

    assert "[Check Task] Task: Run tests" in rendered
    assert "Status: [done] completed" in rendered
    assert "Assigned to: Agent #3" in rendered
    assert "Command: pytest backend/tests -q" in rendered
    assert "Test summary: 12 passed / 1 failed" in rendered
    assert "Next step: Inspect failed tests and logs." in rendered
    assert "Completed at: 2026-05-12T09:01:00" in rendered
