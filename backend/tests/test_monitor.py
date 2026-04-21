"""Monitor endpoint tests."""
import json
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_app(tmp_path):
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

    modules_to_clear = [
        "main",
        "config",
        "models.database",
        "agents.registry",
        "agents.collaboration",
        "tools",
        "llm.client",
        "chatrooms.manager",
        "routes.api",
        "routes.audit",
        "routes.monitor",
        "routes.websocket",
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    import llm.client as llm_mod

    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(return_value={"content": "Mocked agent response.", "tool_calls": None})

    async def mock_stream(messages, tools=None):
        yield {"type": "content", "delta": "Hello!"}
        yield {"type": "done", "full_content": "Hello!", "tool_calls": None}

    mock_llm.chat_stream = mock_stream
    llm_mod._llm_client = mock_llm

    import main as main_mod

    async def passthrough(self, request, call_next):
        return await call_next(request)

    main_mod.RateLimitMiddleware.dispatch = passthrough
    main_mod.RequestLoggingMiddleware.dispatch = passthrough
    return main_mod.app


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    return TestClient(_make_app(tmp_path), base_url="http://testserver")


class TestMonitorOverview:
    def test_monitor_page_route(self, client):
        response = client.get("/monitor")
        assert response.status_code == 200
        assert "Catown Monitor" in response.text

    def test_overview_returns_base_shape(self, client):
        response = client.get("/api/monitor/overview")
        assert response.status_code == 200
        data = response.json()
        assert "system" in data
        assert "usage_window" in data
        assert "recent_runtime" in data
        assert "recent_messages" in data

    def test_overview_aggregates_runtime_cards(self, client):
        from models.database import Agent, Chatroom, Message, Project, SessionLocal

        db = SessionLocal()
        try:
            assistant = db.query(Agent).filter(Agent.name == "assistant").first()
            assert assistant is not None

            project = Project(name="Monitor Project", status="active", workspace_path="/tmp/catown-monitor")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Monitor Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            llm_card = {
                "client_turn_id": "turn-monitor-1",
                "card": {
                    "type": "llm_call",
                    "agent": "assistant",
                    "model": "gpt-4.1-mini",
                    "turn": 1,
                    "system_prompt": "You are the monitor test assistant.",
                    "prompt_messages": json.dumps(
                        [
                            {"role": "system", "content": "You are the monitor test assistant."},
                            {"role": "user", "content": "Summarize the latest monitor status."},
                        ]
                    ),
                    "tokens_in": 120,
                    "tokens_out": 48,
                    "duration_ms": 640,
                    "response": "Generated answer",
                    "raw_response": json.dumps({"id": "resp_123", "content": "Generated answer"}),
                }
            }
            tool_card = {
                "client_turn_id": "turn-monitor-1",
                "card": {
                    "type": "tool_call",
                    "agent": "assistant",
                    "tool": "read_file",
                    "arguments": json.dumps({"path": "README.md"}),
                    "success": False,
                    "duration_ms": 85,
                    "result": "File not found",
                }
            }

            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=None,
                    content="llm_call",
                    message_type="runtime_card",
                    metadata_json=json.dumps(llm_card),
                )
            )
            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=None,
                    content="tool_call",
                    message_type="runtime_card",
                    metadata_json=json.dumps(tool_card),
                )
            )
            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=assistant.id,
                    content="Final answer to the user",
                    message_type="text",
                    metadata_json=json.dumps({"client_turn_id": "turn-monitor-1"}),
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get("/api/monitor/overview")
        assert response.status_code == 200
        data = response.json()

        assert data["usage_window"]["llm_calls"] >= 1
        assert data["usage_window"]["tool_calls"] >= 1
        assert data["usage_window"]["tool_errors"] >= 1
        assert data["usage_window"]["input_tokens"] >= 120
        assert data["usage_window"]["output_tokens"] >= 48
        assert any(item["type"] == "llm_call" for item in data["recent_runtime"])
        assert any(item["tool_name"] == "read_file" for item in data["recent_runtime"])
        assert any(item["tool_name"] == "read_file" for item in data["usage_window"]["top_tools"])
        assert any(item["content_preview"] == "Final answer to the user" for item in data["recent_messages"])
        assert any(item["content"] == "Final answer to the user" for item in data["recent_messages"])

        llm_runtime = next(item for item in data["recent_runtime"] if item["type"] == "llm_call")
        assert llm_runtime["from_entity"] == "assistant"
        assert llm_runtime["to_entity"] == "LLM"
        assert llm_runtime["turn"] == 1
        assert llm_runtime["client_turn_id"] == "turn-monitor-1"
        assert "Summarize the latest monitor status." in llm_runtime["prompt_preview"]
        assert "Generated answer" in llm_runtime["response_preview"]

        tool_runtime = next(item for item in data["recent_runtime"] if item["type"] == "tool_call")
        assert tool_runtime["from_entity"] == "assistant"
        assert tool_runtime["to_entity"] == "read_file"
        assert tool_runtime["client_turn_id"] == "turn-monitor-1"
        assert "README.md" in tool_runtime["arguments_preview"]

        text_message = next(item for item in data["recent_messages"] if item["content"] == "Final answer to the user")
        assert text_message["client_turn_id"] == "turn-monitor-1"

        detail_response = client.get(f"/api/monitor/runtime-cards/{llm_runtime['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["card"]["type"] == "llm_call"
        assert detail["card"]["model"] == "gpt-4.1-mini"

    def test_logs_endpoint_returns_real_backend_logs(self, client):
        from monitoring import monitor_log_buffer

        monitor_log_buffer.clear()
        logger = logging.getLogger("catown.tests.monitor")
        logger.warning("Monitor log endpoint smoke test")

        response = client.get("/api/monitor/logs?limit=20")
        assert response.status_code == 200
        data = response.json()

        assert data["latest_id"] >= 1
        assert any(entry["message"] == "Monitor log endpoint smoke test" for entry in data["entries"])

    def test_logs_stream_emits_entries_after_cursor(self, client):
        from monitoring import monitor_log_buffer

        monitor_log_buffer.clear()
        seed_logger = logging.getLogger("catown.tests.monitor")
        seed_logger.info("seed log")
        latest_id = monitor_log_buffer.latest_id()

        stream_logger = logging.getLogger("catown.tests.monitor")
        stream_logger.error("stream me")

        with client.stream("GET", f"/api/monitor/logs/stream?cursor={latest_id}&once=true") as response:
            assert response.status_code == 200
            line_iter = response.iter_lines()
            payload_line = next(line_iter)
            assert payload_line.startswith("data: ")
            payload = json.loads(payload_line.removeprefix("data: "))

        assert payload["message"] == "stream me"
        assert payload["level"] == "error"
