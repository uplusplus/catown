import importlib
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _reload_query_agent():
    import tools.query_agent as query_agent_mod

    return importlib.reload(query_agent_mod)


def _reload_chatroom_manager():
    import chatrooms.manager as chatroom_manager_mod

    return importlib.reload(chatroom_manager_mod)


def test_query_agent_schema_uses_target_agent_parameter():
    query_agent_mod = _reload_query_agent()

    schema = query_agent_mod.QueryAgentTool().get_schema()
    params = schema["function"]["parameters"]

    assert "target_agent" in params["properties"]
    assert "agent_name" not in params["required"]
    assert "target_agent" in params["required"]


@pytest.mark.asyncio
async def test_query_agent_tool_uses_layered_prompt_context(fresh_db, monkeypatch):
    query_agent_mod = _reload_query_agent()

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        analyst = fresh_db.Agent(
            name="Analyst",
            agent_type="analyst",
            role="Analyst",
            soul=json.dumps({"identity": "Finds structure in messy requests."}),
            skills='["document-analysis"]',
            config=json.dumps(
                {
                    "role": {
                        "title": "Analyst",
                        "responsibilities": ["Analyze requirements"],
                        "rules": ["Stay concise"],
                    }
                },
                ensure_ascii=False,
            ),
            is_active=True,
        )
        developer = fresh_db.Agent(
            name="Developer",
            agent_type="developer",
            role="Developer",
            soul=json.dumps({"identity": "Builds software."}),
            config=json.dumps({"role": {"title": "Developer"}}, ensure_ascii=False),
            is_active=True,
        )
        db.add_all([analyst, developer])
        db.commit()
        db.refresh(analyst)
        db.refresh(developer)

        project = fresh_db.Project(name="Catown", description="Layered context project")
        db.add(project)
        db.commit()
        db.refresh(project)
        project.current_focus = "Provide focused architecture guidance"
        project.blocking_reason = "Pipeline refactor is not in scope for this turn"
        project.latest_summary = "The project already has layered prompt assembly on chat paths."
        db.commit()

        chatroom = fresh_db.Chatroom(project_id=project.id, title="Main Chat", session_type="project-bound")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        db.add_all(
            [
                fresh_db.AgentAssignment(project_id=project.id, agent_id=analyst.id),
                fresh_db.AgentAssignment(project_id=project.id, agent_id=developer.id),
                fresh_db.Memory(
                    agent_id=analyst.id,
                    memory_type="context",
                    content="The user cares about architecture clarity.",
                    importance=9,
                ),
                fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier kickoff question.", message_type="text"),
                fresh_db.Message(chatroom_id=chatroom.id, agent_id=analyst.id, content="Earlier architectural framing.", message_type="text"),
                fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier follow-up about tradeoffs.", message_type="text"),
                fresh_db.Message(chatroom_id=chatroom.id, agent_id=developer.id, content="Earlier narrowing question.", message_type="text"),
                fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Need a quick architecture read.", message_type="text"),
                fresh_db.Message(chatroom_id=chatroom.id, agent_id=developer.id, content="Asking analyst for a focused read.", message_type="text"),
            ]
        )
        db.commit()
        chatroom_id = chatroom.id
    finally:
        db.close()

    captured_messages = []
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(side_effect=lambda messages, **kwargs: captured_messages.append(messages) or "Layered reply")
    monkeypatch.setattr("llm.client.get_llm_client_for_agent", lambda agent_name: mock_llm)

    tool = query_agent_mod.QueryAgentTool()
    result = await tool.execute(
        target_agent="analyst",
        question="What is the main architectural risk?",
        include_context=True,
        chatroom_id=chatroom_id,
        caller_agent_name="developer",
    )

    assert "Layered reply" in result
    assert captured_messages
    messages = captured_messages[0]
    assert messages[0]["role"] == "system"
    assert any(message["role"] == "developer" and "Operating Contract" in message["content"] for message in messages)
    assert any(message["role"] == "developer" and "Tools are disabled for this query" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Active Task State" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Validation Checklist" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Earlier Conversation Summary" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Query Context" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Relevant Memories" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "[Query from developer]" in message["content"] for message in messages)

    captured_messages.clear()
    result = await tool.execute(
        target_agent="analyst",
        question="What is the main architectural risk?",
        include_context=True,
        chatroom_id=chatroom_id,
        agent_name="developer",
    )

    assert "Layered reply" in result
    runtime_messages = captured_messages[0]
    assert any(message["role"] == "user" and "[Query from developer]" in message["content"] for message in runtime_messages)


@pytest.mark.asyncio
async def test_chatroom_manager_fallback_uses_layered_context(fresh_db, monkeypatch):
    manager_mod = _reload_chatroom_manager()

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        developer = fresh_db.Agent(
            name="Developer",
            agent_type="developer",
            role="Developer",
            soul=json.dumps({"identity": "Builds software."}),
            tools='["read_file", "write_file"]',
            skills='["code-generation"]',
            config=json.dumps(
                {
                    "role": {
                        "title": "Developer",
                        "responsibilities": ["Implement features"],
                        "rules": ["Keep patches focused"],
                    }
                },
                ensure_ascii=False,
            ),
            is_active=True,
        )
        tester = fresh_db.Agent(
            name="Tester",
            agent_type="tester",
            role="Tester",
            soul=json.dumps({"identity": "Finds regressions."}),
            config=json.dumps({"role": {"title": "Tester"}}, ensure_ascii=False),
            is_active=True,
        )
        db.add_all([developer, tester])
        db.commit()
        db.refresh(developer)
        db.refresh(tester)

        project = fresh_db.Project(
            name="Fallback Project",
            description="manager fallback path",
            current_focus="Keep fallback chat aligned with shared orchestration",
            blocking_reason="Do not modify pipeline flows in this phase",
            latest_summary="Fallback chat already uses the shared chat prompt builder.",
        )
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id, title="Fallback Chat", session_type="project-bound")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        db.add_all(
                [
                    fresh_db.AgentAssignment(project_id=project.id, agent_id=developer.id),
                    fresh_db.AgentAssignment(project_id=project.id, agent_id=tester.id),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier report context.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=tester.id, content="Earlier validation note.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier reproduction clue.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=tester.id, content="Earlier suspected regression area.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier user environment detail.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=tester.id, content="Earlier test case idea.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier stack trace snippet.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=tester.id, content="Earlier note on flaky behavior.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Earlier workaround attempt.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=tester.id, content="Earlier fix hypothesis.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=None, content="Please inspect the bug report.", message_type="text"),
                    fresh_db.Message(chatroom_id=chatroom.id, agent_id=tester.id, content="I can help validate the fix.", message_type="text"),
                ]
            )
        db.commit()
        developer_id = developer.id
        chatroom_id = chatroom.id
    finally:
        db.close()

    captured_messages = []
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(side_effect=lambda messages, **kwargs: captured_messages.append(messages) or "Fallback layered reply")
    monkeypatch.setattr("llm.client.get_llm_client_for_agent", lambda agent_name: mock_llm)

    manager = manager_mod.ChatroomManager()
    db = fresh_db.SessionLocal()
    try:
        developer = db.query(fresh_db.Agent).filter(fresh_db.Agent.id == developer_id).first()
        response = await manager._call_agent_llm(developer, "Please inspect the bug report.", chatroom_id, db)
    finally:
        db.close()

    assert response == "Fallback layered reply"
    assert captured_messages
    messages = captured_messages[0]
    assert messages[0]["role"] == "system"
    assert any(message["role"] == "developer" and "Operating Contract" in message["content"] for message in messages)
    assert any(message["role"] == "developer" and "Available tools" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Active Task State" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Validation Checklist" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Earlier Conversation Summary" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Current Project" in message["content"] for message in messages)
    assert any(message["role"] == "user" and "## Current Chat" in message["content"] for message in messages)
