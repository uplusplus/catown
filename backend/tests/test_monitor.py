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
    os.environ["MONITOR_NETWORK_RETENTION_HOURS"] = "24"
    os.environ["MONITOR_NETWORK_MAX_PERSISTED"] = "100"

    modules_to_clear = [
        "main",
        "config",
        "models.database",
        "models.audit",
        "monitoring.network_buffer",
        "agents.registry",
        "agents.collaboration",
        "tools",
        "llm.client",
        "chatrooms.manager",
        "routes.api",
        "routes.audit",
        "routes.monitor",
        "routes.websocket",
        "pipeline.engine",
        "routes.pipeline",
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

    return TestClient(_make_app(tmp_path), base_url="http://testserver", headers={"X-Catown-Client": "test"})


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
        from agents.identity import DEFAULT_AGENT_TYPE, default_agent_name
        from models.database import Agent, Chatroom, Message, Project, SessionLocal

        db = SessionLocal()
        try:
            default_agent = (
                db.query(Agent)
                .filter(Agent.agent_type == DEFAULT_AGENT_TYPE)
                .first()
            ) or db.query(Agent).filter(Agent.name == default_agent_name(DEFAULT_AGENT_TYPE)).first()
            assert default_agent is not None
            agent_name = default_agent.name

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
                    "agent": agent_name,
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
                    "agent": agent_name,
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
                    agent_id=default_agent.id,
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
        assert llm_runtime["from_entity"] == agent_name
        assert llm_runtime["to_entity"] == "LLM"
        assert llm_runtime["turn"] == 1
        assert llm_runtime["client_turn_id"] == "turn-monitor-1"
        assert "Summarize the latest monitor status." in llm_runtime["prompt_preview"]
        assert "Generated answer" in llm_runtime["response_preview"]

        tool_runtime = next(item for item in data["recent_runtime"] if item["type"] == "tool_call")
        assert tool_runtime["from_entity"] == agent_name
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

    def test_monitor_task_runs_returns_global_run_history(self, client):
        from models.database import Chatroom, Message, Project, SessionLocal, TaskRun, TaskRunEvent

        db = SessionLocal()
        try:
            project = Project(name="Run Ledger Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Run Ledger Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            origin_message = Message(
                chatroom_id=chatroom.id,
                agent_id=None,
                content="Inspect the latest architecture delta",
                message_type="text",
                metadata_json=json.dumps({"client_turn_id": "turn-monitor-run-1"}),
            )
            db.add(origin_message)
            db.commit()
            db.refresh(origin_message)

            task_run = TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                origin_message_id=origin_message.id,
                client_turn_id="turn-monitor-run-1",
                run_kind="multi_agent_orchestration_stream",
                status="completed",
                title="Inspect the latest architecture delta",
                user_request="Inspect the latest architecture delta",
                initiator="user",
                target_agent_name="Analyst",
                summary="Developer received the handoff and completed the turn.",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add_all(
                [
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=1,
                        event_type="user_message_saved",
                        summary="User message saved.",
                        message_id=origin_message.id,
                        payload_json=json.dumps({"client_turn_id": "turn-monitor-run-1"}),
                    ),
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=2,
                        event_type="handoff_created",
                        agent_name="Analyst",
                        summary="Handoff created for Developer.",
                        payload_json=json.dumps({"from_agent": "Analyst", "to_agent": "Developer"}),
                    ),
                ]
            )
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        response = client.get("/api/monitor/task-runs?range=24h&limit=20")
        assert response.status_code == 200
        data = response.json()
        assert data["range"] == "24h"
        assert data["entries"]

        entry = next(item for item in data["entries"] if item["id"] == task_run_id)
        assert entry["chat_title"] == "Run Ledger Chat"
        assert entry["project_name"] == "Run Ledger Project"
        assert entry["run_kind"] == "multi_agent_orchestration_stream"
        assert entry["status"] == "completed"
        assert entry["event_count"] == 2
        assert entry["latest_event_type"] == "handoff_created"

    def test_monitor_approval_queue_returns_enriched_items(self, client):
        from models.database import ApprovalQueueItem, Chatroom, Project, SessionLocal, TaskRun

        db = SessionLocal()
        try:
            project = Project(name="Approval Queue Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Approval Queue Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            task_run = TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                run_kind="chat_turn",
                status="running",
                title="Inspect blocked action",
                user_request="Inspect blocked action",
                initiator="user",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            queue_item = ApprovalQueueItem(
                task_run_id=task_run.id,
                chatroom_id=chatroom.id,
                project_id=project.id,
                queue_kind="approval",
                status="pending",
                source="runtime",
                title="Approve delete_file",
                summary="delete_file blocked by policy",
                agent_name="Analyst",
                target_kind="tool",
                target_name="delete_file",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "delete_file",
                        "arguments": "{\"file_path\": \"danger.txt\"}",
                        "resume_supported": True,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(queue_item)
            db.commit()

            resolved_item = ApprovalQueueItem(
                task_run_id=task_run.id,
                chatroom_id=chatroom.id,
                project_id=project.id,
                queue_kind="approval",
                status="approved",
                source="runtime",
                title="Approve delete_file",
                summary="delete_file blocked by policy",
                agent_name="Analyst",
                target_kind="tool",
                target_name="delete_file",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "delete_file",
                        "arguments": "{\"file_path\": \"danger.txt\"}",
                        "resume_supported": True,
                    },
                    ensure_ascii=False,
                ),
                resolution_note="Approved and replayed.",
                resolution_payload_json=json.dumps(
                    {
                        "action_taken": "tool_replayed",
                        "replay_status": "succeeded",
                        "replay_success": True,
                        "replay_result_preview": "Deleted file",
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(resolved_item)
            db.commit()
        finally:
            db.close()

        response = client.get("/api/monitor/approval-queue?status=all&limit=20")
        assert response.status_code == 200
        data = response.json()
        assert data["counts"]["pending"] >= 1
        assert data["counts"]["approved"] >= 1

        pending_entry = next(item for item in data["entries"] if item["status"] == "pending")
        assert pending_entry["chat_title"] == "Approval Queue Chat"
        assert pending_entry["project_name"] == "Approval Queue Project"
        assert pending_entry["task_run_title"] == "Inspect blocked action"
        assert pending_entry["request_preview"] == "delete_file blocked by policy"
        assert pending_entry["resume_supported"] is True

        resolved_entry = next(item for item in data["entries"] if item["status"] == "approved")
        assert resolved_entry["resolution_preview"] == "Approved and replayed."
        assert resolved_entry["action_taken"] == "tool_replayed"
        assert resolved_entry["replay_status"] == "succeeded"

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

    def test_network_events_survive_app_restart(self, tmp_path):
        from fastapi.testclient import TestClient

        first_client = TestClient(_make_app(tmp_path), base_url="http://testserver", headers={"X-Catown-Client": "test"})
        post_response = first_client.post(
            "/api/monitor/network/ingest",
            json={
                "category": "backend_other",
                "source": "test",
                "protocol": "HTTPS",
                "from_entity": "Backend",
                "to_entity": "WWW",
                "method": "GET",
                "url": "https://example.com/health",
                "host": "example.com",
                "path": "/health",
                "status_code": 200,
                "success": True,
                "preview": "GET /health",
            },
        )
        assert post_response.status_code == 200
        event_id = post_response.json()["event_id"]

        restarted_client = TestClient(_make_app(tmp_path), base_url="http://testserver", headers={"X-Catown-Client": "test"})
        get_response = restarted_client.get("/api/monitor/network?limit=20")
        assert get_response.status_code == 200
        payload = get_response.json()
        assert any(entry["id"] == event_id and entry["host"] == "example.com" for entry in payload["entries"])

    def test_network_append_recreates_missing_table(self, tmp_path):
        _make_app(tmp_path)
        from models.audit import MonitorNetworkRecord
        from models.database import engine
        from monitoring import monitor_network_buffer

        monitor_network_buffer.clear()
        MonitorNetworkRecord.__table__.drop(bind=engine, checkfirst=True)

        event = monitor_network_buffer.append(
            {
                "category": "backend_other",
                "source": "test",
                "protocol": "HTTPS",
                "from_entity": "Backend",
                "to_entity": "WWW",
                "method": "GET",
                "url": "https://example.com/recreated",
                "host": "example.com",
                "path": "/recreated",
                "status_code": 200,
                "success": True,
                "preview": "GET /recreated",
            }
        )

        entries = monitor_network_buffer.list_entries(limit=20)
        assert event["id"] >= 1
        assert any(entry["id"] == event["id"] and entry["path"] == "/recreated" for entry in entries)

    def test_network_cleanup_prefers_age_then_count(self, tmp_path):
        from datetime import datetime, timedelta

        _make_app(tmp_path)
        from monitoring import monitor_network_buffer

        monitor_network_buffer.clear()
        previous_max = os.environ.get("MONITOR_NETWORK_MAX_PERSISTED")
        previous_hours = os.environ.get("MONITOR_NETWORK_RETENTION_HOURS")
        os.environ["MONITOR_NETWORK_MAX_PERSISTED"] = "2"
        os.environ["MONITOR_NETWORK_RETENTION_HOURS"] = "1"
        try:
            monitor_network_buffer.append(
                {
                    "category": "backend_other",
                    "source": "test",
                    "protocol": "HTTPS",
                    "from_entity": "Backend",
                    "to_entity": "WWW",
                    "created_at": (datetime.now() - timedelta(hours=2)).isoformat(),
                    "url": "https://old.example.com/a",
                }
            )
            monitor_network_buffer.append(
                {
                    "category": "backend_other",
                    "source": "test",
                    "protocol": "HTTPS",
                    "from_entity": "Backend",
                    "to_entity": "WWW",
                    "url": "https://new.example.com/b",
                }
            )
            monitor_network_buffer.append(
                {
                    "category": "backend_other",
                    "source": "test",
                    "protocol": "HTTPS",
                    "from_entity": "Backend",
                    "to_entity": "WWW",
                    "url": "https://new.example.com/c",
                }
            )
            monitor_network_buffer.append(
                {
                    "category": "backend_other",
                    "source": "test",
                    "protocol": "HTTPS",
                    "from_entity": "Backend",
                    "to_entity": "WWW",
                    "url": "https://new.example.com/d",
                }
            )
            monitor_network_buffer._cleanup_persisted()
            entries = monitor_network_buffer.list_entries(limit=10)
        finally:
            if previous_max is None:
                os.environ.pop("MONITOR_NETWORK_MAX_PERSISTED", None)
            else:
                os.environ["MONITOR_NETWORK_MAX_PERSISTED"] = previous_max
            if previous_hours is None:
                os.environ.pop("MONITOR_NETWORK_RETENTION_HOURS", None)
            else:
                os.environ["MONITOR_NETWORK_RETENTION_HOURS"] = previous_hours

        urls = [entry["url"] for entry in entries]
        assert "https://old.example.com/a" not in urls
        assert len(entries) <= 2
        assert "https://new.example.com/d" in urls
