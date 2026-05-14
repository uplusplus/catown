from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import agent_action_runtime as action_module
from services.context_builder import ContextFragment


@pytest.mark.asyncio
async def test_run_broadcast_agent_action_delegates_to_lifecycle(monkeypatch):
    async def fake_broadcast(**kwargs):
        assert kwargs["chatroom_id"] == 100
        assert kwargs["coordinator"] is coordinator
        return "[Broadcast] sent"

    coordinator = SimpleNamespace()
    monkeypatch.setattr(action_module, "send_runtime_collaboration_broadcast", fake_broadcast)

    result = await action_module.run_broadcast_agent_action(
        coordinator=coordinator,
        message="hello",
        current_agent_id=1,
        current_agent_name="assistant",
        chatroom_id=100,
    )

    assert result == "[Broadcast] sent"


def test_run_check_task_status_action_handles_missing_task(monkeypatch):
    monkeypatch.setattr(
        action_module,
        "get_runtime_collaboration_task_status_text",
        lambda task_id, coordinator=None: None,
    )
    result = action_module.run_check_task_status_action(
        coordinator=SimpleNamespace(),
        task_id="missing",
    )

    assert "not found" in result.lower()


def test_run_list_collaborators_action_formats_summary(monkeypatch):
    monkeypatch.setattr(
        action_module,
        "_open_db_session",
        lambda: (SimpleNamespace(), lambda: None),
    )
    monkeypatch.setattr(
        action_module,
        "get_runtime_chatroom_collaborators",
        lambda **kwargs: {
            "source": "coordinator",
            "collaborators": [
                {
                    "agent_id": 2,
                    "agent_name": "coder",
                    "status": "active",
                    "pending_tasks": 1,
                }
            ],
        },
    )

    result = action_module.run_list_collaborators_action(
        coordinator=SimpleNamespace(),
        chatroom_id=100,
    )

    assert "coder" in result
    assert "Selection guide" in result


@pytest.mark.asyncio
async def test_run_direct_message_agent_action_handles_lookup_failure(monkeypatch):
    monkeypatch.setattr(
        action_module,
        "_open_db_session",
        lambda: (SimpleNamespace(), lambda: None),
    )

    async def fake_direct(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(action_module, "send_runtime_collaboration_direct_message", fake_direct)

    result = await action_module.run_direct_message_agent_action(
        coordinator=SimpleNamespace(),
        target_agent_name="ghost",
        message="hello",
        current_agent_id=1,
        current_agent_name="assistant",
        chatroom_id=100,
    )

    assert "not found" in result.lower()


def test_run_list_agents_action_formats_summary(monkeypatch):
    monkeypatch.setattr(
        action_module,
        "_open_db_session",
        lambda: (SimpleNamespace(), lambda: None),
    )
    monkeypatch.setattr(
        action_module,
        "get_runtime_invitable_agents",
        lambda **kwargs: {
            "error": None,
            "agents": [
                {
                    "agent_name": "security",
                    "display_name": "Security",
                    "role": "Security",
                }
            ],
        },
    )

    result = action_module.run_list_agents_action(chatroom_id=100)

    assert "security" in result.lower()


def test_run_invite_agent_action_returns_detail(monkeypatch):
    monkeypatch.setattr(
        action_module,
        "_open_db_session",
        lambda: (SimpleNamespace(), lambda: None),
    )
    monkeypatch.setattr(
        action_module,
        "invite_runtime_agent_to_chatroom",
        lambda **kwargs: {"detail": "[Invite] joined"},
    )

    result = action_module.run_invite_agent_action(agent_name="tester", chatroom_id=100)

    assert result == "[Invite] joined"


def test_run_consult_agent_preflight_action_rejects_self_query():
    result = action_module.run_consult_agent_preflight_action(
        target_agent_name="analyst",
        current_agent_name="analyst",
        chatroom_id=100,
    )

    assert result["ok"] is False
    assert "cannot consult yourself" in str(result["error"]).lower()


def test_run_consult_agent_preflight_action_returns_target_state(monkeypatch):
    monkeypatch.setattr(
        action_module,
        "_open_db_session",
        lambda: (SimpleNamespace(), lambda: None),
    )
    monkeypatch.setattr(
        action_module,
        "resolve_runtime_query_target",
        lambda db, **kwargs: {
            "target_db_agent": SimpleNamespace(id=2, role="Analyst"),
            "chatroom": SimpleNamespace(id=100, project_id=1),
            "room_agent_names": ["analyst", "developer"],
            "is_assigned": True,
        },
    )

    result = action_module.run_consult_agent_preflight_action(
        target_agent_name="analyst",
        current_agent_name="developer",
        chatroom_id=100,
    )

    assert result["ok"] is True
    assert result["target_agent_type"] == "analyst"
    assert result["current_agent_type"] == "developer"
    assert result["target_state"]["is_assigned"] is True


def test_run_consult_agent_response_action_handles_empty_response():
    result = action_module.run_consult_agent_response_action(
        db=SimpleNamespace(),
        chatroom=None,
        question="What is the risk?",
        response="",
        current_agent_type="developer",
        target_agent_type="analyst",
        target_agent_role="Analyst",
    )

    assert "returned an empty response" in result
    assert result.startswith("[consult_agent]")


def test_run_consult_agent_response_action_formats_response():
    result = action_module.run_consult_agent_response_action(
        db=SimpleNamespace(),
        chatroom=None,
        question="What is the risk?",
        response="The main risk is drift.",
        current_agent_type="developer",
        target_agent_type="analyst",
        target_agent_role="Analyst",
        consult_step_id="consult-1",
    )

    assert result == "[Response from analyst (Analyst)]:\nThe main risk is drift.\n[consult_step_id] consult-1"


@pytest.mark.asyncio
async def test_build_consult_agent_prompt_state_shapes_recent_context(monkeypatch):
    fake_db = SimpleNamespace(query=lambda model: SimpleNamespace(filter=lambda *args, **kwargs: SimpleNamespace(first=lambda: None)))

    class FakeMemoryQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, value):
            return self

        def all(self):
            return [SimpleNamespace(created_at=None, content="mem", importance=9)]

    fake_db.query = lambda model: FakeMemoryQuery()

    async def fake_recent_loader(chatroom_id, limit):
        return [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Older question"},
            {"role": "assistant", "content": "Older answer"},
            {"role": "user", "content": "Newest other"},
        ]

    state = await action_module.build_consult_agent_prompt_state(
        db=fake_db,
        target_db_agent=SimpleNamespace(id=2),
        chatroom=SimpleNamespace(id=100, project_id=None),
        current_agent_name="developer",
        question="What is the main risk?",
        include_context=False,
        recent_messages_loader=fake_recent_loader,
    )

    assert state["query_input"] == "[Query from developer]: What is the main risk?"
    assert state["current_input_messages"][-1]["content"] == state["query_input"]
    assert "Queried by agent: developer" in state["runtime_note"]
    assert state["task_fragments"]


def test_build_consult_agent_prompt_profile_returns_fragments():
    profile = action_module.build_consult_agent_prompt_profile(
        target_db_agent=SimpleNamespace(name="Analyst", role="Analyst", skills='["s1"]'),
        runtime_note="## Query Context\n- Queried by agent: developer",
        own_memories=[SimpleNamespace(created_at=None, content="mem", importance=9)],
        project=None,
        chatroom=None,
        history_summary=None,
        task_fragments=["task-fragment"],
        include_context=False,
        skill_id_resolver=lambda agent: ["s1"],
        memory_line_resolver=lambda memories: ["- mem"],
    )

    assert profile["developer_fragments"]
    assert profile["user_fragments"][0] == "task-fragment"


def test_build_consult_agent_messages_returns_layered_messages():
    messages = action_module.build_consult_agent_messages(
        target_db_agent=SimpleNamespace(name="Analyst", role="Analyst"),
        model_id="gpt-test",
        history_messages=[{"role": "assistant", "content": "Earlier answer"}],
        current_input_messages=[{"role": "user", "content": "Current question"}],
        developer_fragments=[
            ContextFragment(
                role="developer",
                content="dev-fragment",
                scope="turn",
                visibility="agent",
                source="test",
            )
        ],
        user_fragments=[
            ContextFragment(
                role="user",
                content="user-fragment",
                scope="turn",
                visibility="agent",
                source="test",
            )
        ],
        fallback_name_resolver=lambda agent: "Analyst",
    )

    assert messages[0]["role"] == "system"
    assert any(message["role"] == "assistant" for message in messages)
    assert any(message["role"] == "user" for message in messages)
