from types import SimpleNamespace

import pytest

from services.chat_runtime import (
    assemble_runtime_chat_messages,
    build_runtime_environment_context,
    prepare_chat_turn_runtime,
    resolve_agent_tool_names,
)


def test_assemble_runtime_chat_messages_adds_tool_guidance(monkeypatch):
    captured = {}

    def fake_shared_assemble_chat_messages(**kwargs):
        captured.update(kwargs)
        return [{"role": "system", "content": "ok"}]

    monkeypatch.setattr("services.chat_runtime.shared_assemble_chat_messages", fake_shared_assemble_chat_messages)

    result = assemble_runtime_chat_messages(
        db=object(),
        agent=None,
        agent_name="Developer",
        user_message="Please use skill_manager to install a skill and maybe run pytest from the shell",
        available_tools=["skill_manager", "read_file"],
        tool_policy_pack={
            "tool_policies": [
                {"name": "skill_manager", "description": "Install or manage skills.", "risk_level": "high", "approval": {"kind": "conditional"}},
                {"name": "read_file", "description": "Read file contents.", "risk_level": "low", "approval": {"kind": "auto"}},
            ]
        },
    )

    assert result == [{"role": "system", "content": "ok"}]
    assert "skill_manager" in captured["tool_guidance"]
    assert "## Tool Hints" in captured["tool_guidance"]
    assert "## Active Tool Guides" in captured["tool_guidance"]
    assert "## Relevant Tool Details" in captured["tool_guidance"]
    assert "When you need to use a tool" in captured["tool_guidance"]


def test_tool_guidance_distinguishes_delegate_from_consult(monkeypatch):
    captured = {}

    def fake_shared_assemble_chat_messages(**kwargs):
        captured.update(kwargs)
        return [{"role": "system", "content": "ok"}]

    monkeypatch.setattr("services.chat_runtime.shared_assemble_chat_messages", fake_shared_assemble_chat_messages)

    assemble_runtime_chat_messages(
        db=object(),
        agent=None,
        agent_name="Valet",
        user_message="Coordinate work with Tester",
        available_tools=["delegate_task", "consult_agent"],
        tool_policy_pack={
            "tool_policies": [
                {"name": "delegate_task", "description": "Delegate tracked async work."},
                {"name": "consult_agent", "description": "Consult another agent."},
            ]
        },
    )

    guidance = captured["tool_guidance"]
    assert "delegate_task: use this for specialized work" in guidance
    assert "consult_agent: use this only for synchronous advice" in guidance
    assert "Do not use consult_agent as a substitute for durable task dispatch" in guidance


def test_runtime_environment_context_prefers_current_python(monkeypatch):
    monkeypatch.setattr("services.chat_runtime.sys.executable", "/opt/catown/venv/bin/python3")
    monkeypatch.setattr(
        "services.chat_runtime.shutil.which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )

    context = build_runtime_environment_context(SimpleNamespace(workspace_path="/workspace/catown"))

    assert "## Runtime Environment" in context
    assert "Workspace path: /workspace/catown" in context
    assert "Recommended Python command for this session: /opt/catown/venv/bin/python3" in context
    assert "/opt/catown/venv/bin/python3 -m pytest backend/tests" in context
    assert "Do not assume `python` exists" in context


def test_assemble_runtime_chat_messages_passes_runtime_context(monkeypatch):
    captured = {}

    def fake_shared_assemble_chat_messages(**kwargs):
        captured.update(kwargs)
        return [{"role": "system", "content": "ok"}]

    monkeypatch.setattr("services.chat_runtime.shared_assemble_chat_messages", fake_shared_assemble_chat_messages)

    assemble_runtime_chat_messages(
        db=object(),
        agent=None,
        agent_name="Tester",
        user_message="run tests",
        runtime_context="## Runtime Environment\n- Recommended Python command for this session: python3",
    )

    assert captured["runtime_context"].startswith("## Runtime Environment")


def test_resolve_agent_tool_names_uses_agent_whitelist_from_json():
    resolved = resolve_agent_tool_names(
        SimpleNamespace(tools='["read_file", "consult_agent", "missing_tool"]'),
        ["read_file", "consult_agent", "run_shell"],
    )

    assert resolved == ["read_file", "consult_agent"]


def test_resolve_agent_tool_names_canonicalizes_legacy_query_agent():
    resolved = resolve_agent_tool_names(
        SimpleNamespace(tools='["read_file", "query_agent", "consult_agent"]'),
        ["read_file", "consult_agent", "run_shell"],
    )

    assert resolved == ["read_file", "consult_agent"]


def test_resolve_agent_tool_names_returns_empty_when_agent_has_no_tools():
    resolved = resolve_agent_tool_names(
        SimpleNamespace(tools=None),
        ["read_file", "consult_agent"],
    )

    assert resolved == []


@pytest.mark.asyncio
async def test_prepare_chat_turn_runtime_builds_shared_runtime(monkeypatch):
    llm_client = SimpleNamespace(model="test-model")

    async def fake_get_messages(chatroom_id, limit):
        return [SimpleNamespace(content="history")]

    monkeypatch.setattr("services.chat_runtime.get_llm_client_for_agent", lambda agent_type: llm_client)
    monkeypatch.setattr("services.chat_runtime.chatroom_manager.get_messages", fake_get_messages)

    from tools import tool_registry

    monkeypatch.setattr(tool_registry, "list_agent_tools", lambda: ["read_file", "run_shell"])
    monkeypatch.setattr(tool_registry, "get_schemas", lambda tool_names=None: [{"name": name} for name in (tool_names or [])])
    monkeypatch.setattr(tool_registry, "get_policy_pack", lambda tool_names: {"tool_names": tool_names, "tool_policies": [{"name": "read_file", "description": "Read file contents."}]})

    agent = SimpleNamespace(id=7, name="Developer", agent_type="developer", tools='["read_file"]')
    project = SimpleNamespace(id=3)

    runtime = await prepare_chat_turn_runtime(
        agent=agent,
        chatroom_id=11,
        project=project,
        previous_agent_work="Prior work",
        inter_agent_messages=[{"message_type": "handoff", "content": "Use this."}],
        recent_message_limit=6,
    )

    assert runtime.llm_client is llm_client
    assert runtime.agent_label == "Developer"
    assert runtime.available_tools == ["read_file"]
    assert runtime.tool_schemas == [{"name": "read_file"}]
    assert runtime.tool_policy_pack["tool_names"] == ["read_file"]
    assert runtime.runtime_kwargs == {
        "chatroom_id": 11,
        "agent_id": 7,
        "agent_name": "Developer",
        "project_id": 3,
    }
    assert runtime.turn_state.previous_agent_work == "Prior work"
    assert runtime.turn_state.inter_agent_messages == [{"message_type": "handoff", "content": "Use this."}]


@pytest.mark.asyncio
async def test_prepare_chat_turn_runtime_excludes_system_only_tools(monkeypatch):
    llm_client = SimpleNamespace(model="test-model")

    async def fake_get_messages(chatroom_id, limit):
        return []

    monkeypatch.setattr("services.chat_runtime.get_llm_client_for_agent", lambda agent_type: llm_client)
    monkeypatch.setattr("services.chat_runtime.chatroom_manager.get_messages", fake_get_messages)

    from tools import tool_registry

    monkeypatch.setattr(tool_registry, "list_agent_tools", lambda: ["read_file", "run_shell"])
    monkeypatch.setattr(tool_registry, "get_schemas", lambda tool_names=None: [{"name": name} for name in (tool_names or [])])
    monkeypatch.setattr(tool_registry, "get_policy_pack", lambda tool_names: {"tool_names": tool_names, "tool_policies": []})

    agent = SimpleNamespace(
        id=7,
        name="Developer",
        agent_type="developer",
        tools='["read_file", "send_direct_message"]',
    )

    runtime = await prepare_chat_turn_runtime(agent=agent, chatroom_id=11, project=None)

    assert runtime.available_tools == ["read_file"]
    assert runtime.tool_schemas == [{"name": "read_file"}]
