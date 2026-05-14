from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.collaboration_dispatch_runtime import (
    ensure_collaboration_target,
    find_db_agent_by_type,
    route_broadcast_message,
    route_direct_message,
    route_delegated_task_request,
)


class DummyCoordinator:
    def __init__(self):
        self.collaborators = {}
        self.routed_messages = []

    def register_collaborator(self, collaborator):
        self.collaborators[collaborator.agent_id] = collaborator

    async def route_message(self, message):
        self.routed_messages.append(message)


def test_find_db_agent_by_type_resolves_agent_type_and_names(fresh_db):
    db = fresh_db.SessionLocal()
    try:
        agent = fresh_db.Agent(
            name="Tester",
            agent_type="tester",
            role="QA",
            is_active=True,
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        resolved = find_db_agent_by_type(db, "tester")

        assert resolved is not None
        assert resolved.id == agent.id
    finally:
        db.close()


def test_ensure_collaboration_target_registers_missing_collaborator(fresh_db):
    db = fresh_db.SessionLocal()
    coordinator = DummyCoordinator()
    try:
        agent = fresh_db.Agent(
            name="Coder",
            agent_type="coder",
            role="Developer",
            is_active=True,
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        target_agent_id, target_agent_type = ensure_collaboration_target(
            db=db,
            target_agent_name="coder",
            chatroom_id=42,
            coordinator=coordinator,
        )

        assert target_agent_id == agent.id
        assert target_agent_type == "coder"
        assert coordinator.collaborators[target_agent_id].agent_name == "coder"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_route_delegated_task_request_creates_task_request_message():
    coordinator = DummyCoordinator()
    task = SimpleNamespace(
        id="task-1",
        title="Run tests",
        description="Run the backend suite",
        chatroom_id=7,
        dict=lambda: {"id": "task-1", "title": "Run tests"},
    )

    message = await route_delegated_task_request(
        coordinator=coordinator,
        task=task,
        from_agent_id=1,
        from_agent_name="assistant",
        to_agent_id=2,
        to_agent_name="tester",
        context="Focus on flaky tests",
    )

    assert message.task_id == "task-1"
    assert message.message_type == "task_request"
    assert message.to_agent_id == 2
    assert "Focus on flaky tests" in message.content
    assert coordinator.routed_messages == [message]


@pytest.mark.asyncio
async def test_route_broadcast_message_creates_broadcast_message():
    coordinator = DummyCoordinator()

    message = await route_broadcast_message(
        coordinator=coordinator,
        from_agent_id=1,
        from_agent_name="assistant",
        chatroom_id=7,
        content="Heads up, team.",
    )

    assert message.message_type == "broadcast"
    assert message.content == "Heads up, team."
    assert coordinator.routed_messages == [message]


@pytest.mark.asyncio
async def test_route_direct_message_creates_direct_message():
    coordinator = DummyCoordinator()

    message = await route_direct_message(
        coordinator=coordinator,
        from_agent_id=1,
        from_agent_name="assistant",
        to_agent_id=2,
        to_agent_name="coder",
        chatroom_id=7,
        content="Need help with this diff.",
    )

    assert message.message_type == "direct"
    assert message.to_agent_id == 2
    assert message.to_agent_name == "coder"
    assert coordinator.routed_messages == [message]
