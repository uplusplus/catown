from types import SimpleNamespace

import pytest

from services import agent_lifecycle_runtime as runtime_module


def test_get_runtime_collaboration_status_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace(collaborators={1: object()}, chatroom_agents={100: {1}})
    coordinator.pending_task_count = lambda: 2

    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    payload = runtime_module.get_runtime_collaboration_status()

    assert payload == {
        "active_collaborators": 1,
        "chatrooms": 1,
        "pending_tasks": 2,
    }


def test_require_task_run_raises_for_missing_task_run():
    fake_query = SimpleNamespace(filter=lambda *args, **kwargs: SimpleNamespace(first=lambda: None))
    fake_db = SimpleNamespace(query=lambda model: fake_query)

    with pytest.raises(runtime_module.SubagentRuntimeControlError) as excinfo:
        runtime_module.require_task_run(fake_db, 123)

    assert excinfo.value.status_code == 404


def test_list_runtime_task_run_subagents_delegates_to_projection(monkeypatch):
    task_run = SimpleNamespace(id=7, status="running")
    monkeypatch.setattr(runtime_module, "require_task_run", lambda db, task_run_id: task_run)
    monkeypatch.setattr(
        runtime_module,
        "build_task_run_subagent_projection",
        lambda current_task_run: {"task_run_id": current_task_run.id, "status": current_task_run.status},
    )

    payload = runtime_module.list_runtime_task_run_subagents(SimpleNamespace(), 7)

    assert payload == {"task_run_id": 7, "status": "running"}


@pytest.mark.asyncio
async def test_delegate_runtime_collaboration_task_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace()
    delegated_task = SimpleNamespace(id="task-1")

    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    async def fake_delegate(**kwargs):
        assert kwargs["coordinator"] is coordinator
        assert kwargs["target_agent_name"] == "coder"
        assert kwargs["context"] == "ctx"
        assert kwargs["start_execution"] is True
        return delegated_task, "delegated"

    monkeypatch.setattr(runtime_module, "delegate_collaboration_task", fake_delegate)

    task, result_text = await runtime_module.delegate_runtime_collaboration_task(
        SimpleNamespace(),
        target_agent_name="coder",
        task_title="Task",
        task_description="Desc",
        chatroom_id=100,
        created_by_agent_id=0,
        current_agent_name="user",
        context="ctx",
        start_execution=True,
        store_runtime_card_fn=lambda *args, **kwargs: None,
        send_message_fn=lambda *args, **kwargs: None,
        publish_saved_chat_message_fn=lambda *args, **kwargs: None,
        trigger_agent_response_fn=lambda *args, **kwargs: None,
        create_task_fn=lambda coro: coro,
    )

    assert task is delegated_task
    assert result_text == "delegated"


def test_get_runtime_collaboration_task_status_text_delegates_to_runtime(monkeypatch):
    monkeypatch.setattr(
        runtime_module,
        "get_collaboration_task_status_text",
        lambda task_id, coordinator=None: f"status:{task_id}",
    )
    assert runtime_module.get_runtime_collaboration_task_status_text("task-7") == "status:task-7"


@pytest.mark.asyncio
async def test_send_runtime_collaboration_broadcast_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace()
    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    async def fake_send_broadcast(**kwargs):
        assert kwargs["coordinator"] is coordinator
        assert kwargs["chatroom_id"] == 100
        return "broadcasted"

    monkeypatch.setattr(runtime_module, "send_collaboration_broadcast", fake_send_broadcast)

    result = await runtime_module.send_runtime_collaboration_broadcast(
        from_agent_id=1,
        from_agent_name="assistant",
        chatroom_id=100,
        content="hello",
    )

    assert result == "broadcasted"


@pytest.mark.asyncio
async def test_send_runtime_collaboration_direct_message_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace()
    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    async def fake_send_direct(**kwargs):
        assert kwargs["coordinator"] is coordinator
        assert kwargs["target_agent_name"] == "coder"
        return "sent"

    monkeypatch.setattr(runtime_module, "send_collaboration_direct_message", fake_send_direct)

    result = await runtime_module.send_runtime_collaboration_direct_message(
        SimpleNamespace(),
        target_agent_name="coder",
        from_agent_id=1,
        from_agent_name="assistant",
        chatroom_id=100,
        content="hello",
    )

    assert result == "sent"


def test_get_runtime_chatroom_collaborators_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace()
    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    def fake_get_summary(**kwargs):
        assert kwargs["coordinator"] is coordinator
        assert kwargs["chatroom_id"] == 100
        return {"collaborators": []}

    monkeypatch.setattr(runtime_module, "get_chatroom_collaborator_summary", fake_get_summary)

    payload = runtime_module.get_runtime_chatroom_collaborators(SimpleNamespace(), chatroom_id=100)

    assert payload == {"collaborators": []}


def test_get_runtime_invitable_agents_delegates(monkeypatch):
    monkeypatch.setattr(
        runtime_module,
        "get_invitable_agents_summary",
        lambda **kwargs: {"agents": [{"agent_name": "tester"}]},
    )

    payload = runtime_module.get_runtime_invitable_agents(SimpleNamespace(), chatroom_id=100)

    assert payload == {"agents": [{"agent_name": "tester"}]}


def test_invite_runtime_agent_to_chatroom_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace()
    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    def fake_invite(**kwargs):
        assert kwargs["coordinator"] is coordinator
        assert kwargs["agent_name"] == "tester"
        return {"ok": True}

    monkeypatch.setattr(runtime_module, "invite_agent_to_chatroom", fake_invite)

    payload = runtime_module.invite_runtime_agent_to_chatroom(
        SimpleNamespace(),
        chatroom_id=100,
        agent_name="tester",
    )

    assert payload == {"ok": True}


def test_ensure_runtime_chatroom_collaborators_uses_default_coordinator(monkeypatch):
    coordinator = SimpleNamespace()
    monkeypatch.setattr(runtime_module, "_default_collaboration_coordinator", lambda: coordinator)

    def fake_ensure(**kwargs):
        assert kwargs["coordinator"] is coordinator
        assert kwargs["chatroom_id"] == 100
        return [{"agent_name": "coder"}]

    monkeypatch.setattr(runtime_module, "ensure_chatroom_collaborators", fake_ensure)

    payload = runtime_module.ensure_runtime_chatroom_collaborators(
        agents=[SimpleNamespace(id=1)],
        chatroom_id=100,
        agent_name_resolver=lambda agent: "coder",
    )

    assert payload == [{"agent_name": "coder"}]


def test_resolve_runtime_query_target_returns_room_membership(fresh_db):
    db = fresh_db.SessionLocal()
    try:
        analyst = fresh_db.Agent(
            name="Analyst",
            agent_type="analyst",
            role="Analyst",
            is_active=True,
        )
        developer = fresh_db.Agent(
            name="Developer",
            agent_type="developer",
            role="Developer",
            is_active=True,
        )
        db.add_all([analyst, developer])
        db.commit()
        db.refresh(analyst)
        db.refresh(developer)

        project = fresh_db.Project(name="Proj", status="active")
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id, title="Main", session_type="project-bound")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        db.add(fresh_db.AgentAssignment(project_id=project.id, agent_id=analyst.id))
        db.commit()

        payload = runtime_module.resolve_runtime_query_target(
            db,
            target_agent_name="analyst",
            chatroom_id=chatroom.id,
        )

        assert payload["target_agent_type"] == "analyst"
        assert payload["target_db_agent"].id == analyst.id
        assert payload["chatroom"].id == chatroom.id
        assert payload["is_assigned"] is True
        assert payload["room_agent_names"] == ["analyst"]
    finally:
        db.close()
