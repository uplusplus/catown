from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import collaboration_runtime as runtime_module


class DummyCoordinator:
    def __init__(self):
        self.collaborators = {}
        self.task_registry = {}
        self.chatroom_agents = {100: {1, 2, 3}}

    def get_task_status(self, task_id: str):
        return self.task_registry.get(task_id)

    def pending_task_count(self):
        return 0


@pytest.mark.asyncio
async def test_delegate_collaboration_task_returns_error_when_target_missing(monkeypatch):
    coordinator = DummyCoordinator()
    monkeypatch.setattr(
        runtime_module,
        "ensure_collaboration_target",
        lambda **kwargs: (None, "ghost"),
    )

    task, result_text = await runtime_module.delegate_collaboration_task(
        coordinator=coordinator,
        db=SimpleNamespace(),
        target_agent_name="ghost",
        task_title="Task",
        task_description="Desc",
        chatroom_id=100,
        created_by_agent_id=1,
        current_agent_name="assistant",
    )

    assert task is None
    assert "not found" in result_text.lower()


@pytest.mark.asyncio
async def test_delegate_collaboration_task_registers_and_optionally_kicks_off(monkeypatch):
    coordinator = DummyCoordinator()
    task = SimpleNamespace(id="task-1", metadata={})
    kicked_off = []

    monkeypatch.setattr(
        runtime_module,
        "ensure_collaboration_target",
        lambda **kwargs: (2, "coder"),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_delegated_collaboration_task",
        lambda **kwargs: task,
    )
    monkeypatch.setattr(
        runtime_module,
        "register_delegated_collaboration_task",
        lambda delegated_task, **kwargs: delegated_task,
    )

    async def fake_route_request(**kwargs):
        return None

    async def fake_kick_off(**kwargs):
        kicked_off.append(kwargs)
        return "delegate-task-1"

    monkeypatch.setattr(runtime_module, "route_delegated_task_request", fake_route_request)
    monkeypatch.setattr(runtime_module, "kick_off_delegated_task_execution", fake_kick_off)

    delegated_task, result_text = await runtime_module.delegate_collaboration_task(
        coordinator=coordinator,
        db=SimpleNamespace(),
        target_agent_name="coder",
        task_title="Task",
        task_description="Desc",
        chatroom_id=100,
        created_by_agent_id=1,
        current_agent_name="assistant",
        context="ctx",
        start_execution=True,
        store_runtime_card_fn=lambda *args, **kwargs: None,
        send_message_fn=lambda *args, **kwargs: None,
        publish_saved_chat_message_fn=lambda *args, **kwargs: None,
        trigger_agent_response_fn=lambda *args, **kwargs: None,
        create_task_fn=lambda coro: coro,
    )

    assert delegated_task is task
    assert "delegated" in result_text.lower()
    assert len(kicked_off) == 1
    assert kicked_off[0]["task"] is task
    assert kicked_off[0]["target_agent_type"] == "coder"


@pytest.mark.asyncio
async def test_send_collaboration_broadcast_returns_status(monkeypatch):
    coordinator = DummyCoordinator()
    calls = []

    async def fake_route(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(runtime_module, "route_broadcast_message", fake_route)

    result_text = await runtime_module.send_collaboration_broadcast(
        coordinator=coordinator,
        from_agent_id=1,
        from_agent_name="assistant",
        chatroom_id=100,
        content="Heads up",
    )

    assert "2 other agent" in result_text
    assert calls[0]["content"] == "Heads up"


@pytest.mark.asyncio
async def test_send_collaboration_direct_message_routes_when_target_exists(monkeypatch):
    coordinator = DummyCoordinator()
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "ensure_collaboration_target",
        lambda **kwargs: (2, "coder"),
    )

    async def fake_route(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(runtime_module, "route_direct_message", fake_route)

    result_text = await runtime_module.send_collaboration_direct_message(
        coordinator=coordinator,
        db=SimpleNamespace(),
        target_agent_name="coder",
        from_agent_id=1,
        from_agent_name="assistant",
        chatroom_id=100,
        content="Need help",
    )

    assert result_text == "[Direct Message] Sent to coder"
    assert calls[0]["to_agent_id"] == 2


def test_get_collaboration_task_status_text_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(runtime_module, "resolve_collaboration_task", lambda *args, **kwargs: None)
    assert runtime_module.get_collaboration_task_status_text("missing") is None


def test_get_collaboration_status_payload_summarizes_coordinator():
    coordinator = DummyCoordinator()

    payload = runtime_module.get_collaboration_status_payload(coordinator=coordinator)

    assert payload == {
        "active_collaborators": 0,
        "chatrooms": 1,
        "pending_tasks": 0,
    }


def test_get_chatroom_collaboration_status_payload_reads_one_chatroom_status():
    coordinator = DummyCoordinator()
    coordinator.get_chatroom_status = lambda chatroom_id: {
        "chatroom_id": chatroom_id,
        "agent_count": 2,
        "agents": [],
        "active_tasks": 1,
    }

    payload = runtime_module.get_chatroom_collaboration_status_payload(
        coordinator=coordinator,
        chatroom_id=100,
    )

    assert payload == {
        "chatroom_id": 100,
        "agent_count": 2,
        "agents": [],
        "active_tasks": 1,
    }


def test_ensure_chatroom_collaborators_registers_missing_agents(monkeypatch):
    registered = []

    class CoordinatorWithRegister(DummyCoordinator):
        def register_collaborator(self, collaborator):
            registered.append(collaborator)
            self.collaborators[collaborator.agent_id] = collaborator

    coordinator = CoordinatorWithRegister()
    agents = [SimpleNamespace(id=1, agent_type="analyst"), SimpleNamespace(id=2, agent_type="developer")]

    payload = __import__(
        "services.collaboration_membership_runtime",
        fromlist=["ensure_chatroom_collaborators"],
    ).ensure_chatroom_collaborators(
        agents=agents,
        chatroom_id=100,
        coordinator=coordinator,
        agent_name_resolver=lambda agent: agent.agent_type,
    )

    assert len(payload) == 2
    assert payload[0]["agent_name"] == "analyst"
    assert payload[1]["agent_name"] == "developer"
    assert len(registered) == 2
