from types import SimpleNamespace

import pytest

from services.chat_runtime import assemble_runtime_chat_messages, prepare_chat_turn_runtime


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
        available_tools=["skill_manager", "read_file"],
    )

    assert result == [{"role": "system", "content": "ok"}]
    assert "skill_manager" in captured["tool_guidance"]
    assert "When you need to use a tool" in captured["tool_guidance"]


@pytest.mark.asyncio
async def test_prepare_chat_turn_runtime_builds_shared_runtime(monkeypatch):
    llm_client = SimpleNamespace(model="test-model")

    async def fake_get_messages(chatroom_id, limit):
        return [SimpleNamespace(content="history")]

    monkeypatch.setattr("services.chat_runtime.get_llm_client_for_agent", lambda agent_type: llm_client)
    monkeypatch.setattr("services.chat_runtime.chatroom_manager.get_messages", fake_get_messages)

    from tools import tool_registry

    monkeypatch.setattr(tool_registry, "list_tools", lambda: ["read_file"])
    monkeypatch.setattr(tool_registry, "get_schemas", lambda: [{"name": "read_file"}])

    agent = SimpleNamespace(id=7, name="Developer", agent_type="developer")
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
    assert runtime.runtime_kwargs == {
        "chatroom_id": 11,
        "agent_id": 7,
        "agent_name": "Developer",
        "project_id": 3,
    }
    assert runtime.turn_state.previous_agent_work == "Prior work"
    assert runtime.turn_state.inter_agent_messages == [{"message_type": "handoff", "content": "Use this."}]
