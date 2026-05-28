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
    os.environ["CATOWN_HOME"] = str(tmp_path / "catown-home")
    os.environ["CATOWN_STATE_DIR"] = str(tmp_path / "catown-state")
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
        "services.approval_audit",
        "services.approval_queue",
        "services.approval_replay",
        "services.monitor_projection",
        "services.run_shell_processes",
        "services.tool_execution_preferences",
    ]
    from tests.conftest import reset_app_modules
    reset_app_modules(modules_to_clear)

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
        assert "tasks" in data
        assert "llm" in data
        assert "approvals" in data
        assert "compactions" in data
        assert "recent_runtime" not in data
        assert "recent_messages" not in data
        assert "recent_compactions" not in data

    def test_overview_activity_returns_base_shape(self, client):
        response = client.get("/api/monitor/overview/activity")
        assert response.status_code == 200
        data = response.json()
        assert "recent_runtime" in data
        assert "recent_messages" in data
        assert "recent_compactions" in data

    def test_overview_includes_collaboration_summary(self, client):
        from agents.collaboration import CollaborationTask, TaskStatus, collaboration_coordinator

        collaboration_coordinator.task_registry.clear()
        task = CollaborationTask(
            id="monitor-task-1",
            title="Monitor task",
            description="Pending collaboration work",
            status=TaskStatus.IN_PROGRESS,
            created_by_agent_id=1,
            assigned_to_agent_id=2,
            chatroom_id=100,
        )
        collaboration_coordinator.task_registry[task.id] = task

        response = client.get("/api/monitor/overview")
        assert response.status_code == 200
        data = response.json()

        assert "collaboration" in data["system"]
        assert data["system"]["collaboration"]["pending_tasks"] >= 1
        assert data["system"]["collaboration"]["status"] == "active"

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
                    "tool_call_id": "call-monitor-readme",
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
        activity_response = client.get("/api/monitor/overview/activity")
        assert activity_response.status_code == 200
        activity = activity_response.json()

        assert data["usage_window"]["llm_calls"] >= 1
        assert data["usage_window"]["tool_calls"] >= 1
        assert data["usage_window"]["tool_errors"] >= 1
        assert data["usage_window"]["input_tokens"] >= 120
        assert data["usage_window"]["output_tokens"] >= 48
        assert any(item["type"] == "llm_call" for item in activity["recent_runtime"])
        assert any(item["tool_name"] == "read_file" for item in activity["recent_runtime"])
        assert any(item["tool_name"] == "read_file" for item in data["usage_window"]["top_tools"])
        assert any(item["content_preview"] == "Final answer to the user" for item in activity["recent_messages"])
        assert any(item["content"] == "Final answer to the user" for item in activity["recent_messages"])

        llm_runtime = next(item for item in activity["recent_runtime"] if item["type"] == "llm_call")
        assert llm_runtime["from_entity"] == agent_name
        assert llm_runtime["to_entity"] == "LLM"
        assert llm_runtime["operation_label"] == "llm"
        assert len(llm_runtime["brain_events"]) == 2
        assert llm_runtime["brain_events"][0]["phase"] == "outbound"
        assert llm_runtime["turn"] == 1
        assert llm_runtime["client_turn_id"] == "turn-monitor-1"
        assert "Summarize the latest monitor status." in llm_runtime["prompt_preview"]
        assert "Generated answer" in llm_runtime["response_preview"]

        tool_runtime = next(item for item in activity["recent_runtime"] if item["type"] == "tool_call")
        assert tool_runtime["from_entity"] == agent_name
        assert tool_runtime["to_entity"] == "read_file"
        assert tool_runtime["operation_label"] == "read_file"
        assert len(tool_runtime["brain_events"]) == 2
        assert tool_runtime["brain_events"][1]["phase"] == "inbound"
        assert tool_runtime["client_turn_id"] == "turn-monitor-1"
        assert tool_runtime["tool_call_id"] == "call-monitor-readme"
        assert "README.md" in tool_runtime["arguments_preview"]

        text_message = next(item for item in activity["recent_messages"] if item["content"] == "Final answer to the user")
        assert text_message["client_turn_id"] == "turn-monitor-1"

        detail_response = client.get(f"/api/monitor/runtime-cards/{llm_runtime['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["card"]["type"] == "llm_call"
        assert detail["card"]["model"] == "gpt-4.1-mini"
        assert detail["operation_label"] == "llm"
        assert len(detail["brain_events"]) == 2
        assert any(section["phase"] == "outbound" for section in detail["detail_sections"])
        assert any(section["label"] == "System Prompt" for section in detail["detail_sections"])

    def test_runtime_detail_sections_cover_consult_and_stage_cards(self, client):
        from models.database import Chatroom, Message, Project, SessionLocal

        db = SessionLocal()
        try:
            project = Project(name="Runtime Detail Sections", status="active", workspace_path="/tmp/catown-runtime-sections")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Runtime Detail Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            consult_message = Message(
                chatroom_id=chatroom.id,
                agent_id=None,
                content="consult_call",
                message_type="runtime_card",
                metadata_json=json.dumps(
                    {
                        "client_turn_id": "turn-runtime-sections",
                        "card": {
                            "type": "consult_call",
                            "agent": "Planner",
                            "target_agent": "Developer",
                            "question": "How should we split the migration?",
                            "response_preview": "Split schema first, then runtime wiring.",
                            "summary_text": "Planner consulted Developer about migration sequencing.",
                        },
                    }
                ),
            )
            stage_message = Message(
                chatroom_id=chatroom.id,
                agent_id=None,
                content="stage_completed",
                message_type="runtime_card",
                metadata_json=json.dumps(
                    {
                        "client_turn_id": "turn-runtime-sections",
                        "card": {
                            "type": "stage_completed",
                            "agent": "Planner",
                            "stage": "design_review",
                            "display_name": "Design Review",
                            "summary": "Design review completed with one follow-up note.",
                        },
                    }
                ),
            )
            db.add_all([consult_message, stage_message])
            db.commit()
            db.refresh(consult_message)
            db.refresh(stage_message)
            consult_id = consult_message.id
            stage_id = stage_message.id
        finally:
            db.close()

        consult_detail = client.get(f"/api/monitor/runtime-cards/{consult_id}").json()
        assert any(section["label"] == "Consult Request" for section in consult_detail["detail_sections"])
        assert any(section["label"] == "Consult Response" for section in consult_detail["detail_sections"])

        stage_detail = client.get(f"/api/monitor/runtime-cards/{stage_id}").json()
        assert any(section["label"] == "Stage Status" for section in stage_detail["detail_sections"])
        assert any(section["label"] == "Exchange Meta" for section in stage_detail["detail_sections"])

    def test_files_endpoint_extracts_file_tool_runtime_cards(self, client):
        from models.database import Chatroom, Message, Project, SessionLocal

        db = SessionLocal()
        try:
            project = Project(name="Files Project", status="active", workspace_path="/tmp/catown-files")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Files Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=None,
                    content="tool_call",
                    message_type="runtime_card",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "turn-files-1",
                            "card": {
                                "type": "tool_call",
                                "agent": "Developer",
                                "tool": "write_file",
                                "arguments": json.dumps({"file_path": "src/app.py", "content": "print('ok')"}),
                                "success": True,
                                "status": "completed",
                                "result": "[Write File] Wrote to 'src/app.py' successfully (11 characters)",
                                "duration_ms": 42,
                                "turn": 2,
                            },
                        }
                    ),
                )
            )
            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=None,
                    content="tool_call",
                    message_type="runtime_card",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "turn-files-1",
                            "card": {
                                "type": "tool_call",
                                "agent": "Developer",
                                "tool": "run_shell",
                                "arguments": json.dumps({"command": "ls"}),
                                "success": True,
                                "result": "ignored",
                            },
                        }
                    ),
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get("/api/monitor/files?tool=write_file&query=app.py")
        assert response.status_code == 200
        data = response.json()
        assert data["counts"]["total"] == 1
        assert data["counts"]["writes"] == 1
        assert data["counts"]["unique_paths"] == 1
        assert data["by_agent"] == [{"agent": "Developer", "count": 1}]
        entry = data["entries"][0]
        assert entry["tool_name"] == "write_file"
        assert entry["action"] == "write"
        assert entry["file_path"] == "src/app.py"
        assert entry["project_name"] == "Files Project"
        assert entry["client_turn_id"] == "turn-files-1"
        assert entry["arguments"]["content"] == "print('ok')"

    def test_overview_returns_recent_context_compactions(self, client):
        from models.database import Chatroom, Project, SessionLocal, TaskRun, TaskRunEvent

        db = SessionLocal()
        try:
            project = Project(name="Compaction Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Compaction Chat",
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
                title="Compaction run",
                user_request="Large context request",
                initiator="user",
                target_agent_name="analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="context_compaction",
                    agent_name="analyst",
                    summary="analyst compacted context (dropped=2, truncated=1).",
                    payload_json=json.dumps(
                        {
                            "compacted": True,
                            "selector_diagnostics": {
                                "compacted": True,
                                "selector": {
                                    "max_fragments": 12,
                                    "max_tokens": 3200,
                                    "max_tokens_by_role": {"developer": 1200, "user": 2000},
                                    "max_tokens_by_scope": {"run": 1800, "turn": 400},
                                },
                                "summary": {
                                    "candidate_count": 9,
                                    "selected_count": 7,
                                    "dropped_count": 2,
                                    "truncated_count": 1,
                                    "candidate_tokens": 4200,
                                    "selected_tokens": 3100,
                                    "by_scope": {
                                        "run": {
                                            "candidate_count": 3,
                                            "selected_count": 2,
                                            "candidate_tokens": 2200,
                                            "selected_tokens": 1400,
                                        },
                                        "turn": {
                                            "candidate_count": 2,
                                            "selected_count": 2,
                                            "candidate_tokens": 600,
                                            "selected_tokens": 400,
                                        },
                                    },
                                },
                                "developer": {"dropped_count": 0, "truncated_count": 0},
                                "user": {"dropped_count": 2, "truncated_count": 1},
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get("/api/monitor/overview")
        assert response.status_code == 200
        data = response.json()
        assert data["system"]["stats"]["context_compactions"] >= 1
        activity_response = client.get("/api/monitor/overview/activity")
        assert activity_response.status_code == 200
        activity = activity_response.json()

        entry = next(
            item
            for item in activity["recent_compactions"]
            if item["summary"] == "analyst compacted context (dropped=2, truncated=1)."
        )
        assert entry["chat_title"] == "Compaction Chat"
        assert entry["project_name"] == "Compaction Project"
        assert entry["dropped_count"] == 2
        assert entry["truncated_count"] == 1
        assert entry["max_tokens"] == 3200
        assert entry["max_tokens_by_role"] == {"developer": 1200, "user": 2000}
        assert entry["max_tokens_by_scope"] == {"run": 1800, "turn": 400}
        assert entry["scope_usage"]["run"]["selected_tokens"] == 1400
        assert "roles developer 1200 / user 2000" in entry["budget_summary"]
        assert "run 2/3 fragments, 1400/2200 tokens" in entry["scope_usage_summary"]

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
        assert entry["continuation_cursor"]["next_action"] == "none"
        assert entry["continuation_cursor_summary"] is None
        assert entry["latest_scheduler_runtime"] is None
        assert entry["scheduler_runtime_summary"] is None
        assert entry["checkpoint_snapshot"]["event_count"] == 2
        assert entry["checkpoint_snapshot"]["latest_event_type"] == "handoff_created"
        assert entry["checkpoint_snapshot"]["continuation_cursor_summary"] is None

    def test_monitor_processes_returns_newest_tracked_processes_first(self, client):
        from services import run_shell_processes

        first = run_shell_processes.create_tracked_run_shell_handle(
            command="python old_task.py",
            cwd="/tmp",
            timeout_seconds=20,
            task_run_id=101,
            agent_name="Builder",
        )
        second = run_shell_processes.create_tracked_run_shell_handle(
            command="python new_task.py",
            cwd="/tmp",
            timeout_seconds=20,
            task_run_id=102,
            agent_name="Tester",
        )

        response = client.get("/api/monitor/processes?limit=20&tail_chars=0")
        assert response.status_code == 200
        data = response.json()

        ids = [entry["id"] for entry in data["entries"]]
        assert ids.index(second["token"]) < ids.index(first["token"])
        newest = next(entry for entry in data["entries"] if entry["id"] == second["token"])
        assert newest["command"] == "python new_task.py"
        assert newest["status"] in {"created", "starting", "running"}
        assert newest["is_terminal"] is False
        assert data["counts"]["total"] >= 2

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
                        "followup_attempted": True,
                        "followup_status": "continued",
                        "followup_message_id": 88,
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
        assert resolved_entry["followup_attempted"] is True
        assert resolved_entry["followup_status"] == "continued"
        assert resolved_entry["followup_message_id"] == 88

    def test_monitor_approval_audit_returns_persisted_records(self, client):
        from models.database import ApprovalAuditLog, Chatroom, Project, SessionLocal

        db = SessionLocal()
        try:
            project = Project(name="Approval Audit Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Approval Audit Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            row = ApprovalAuditLog(
                event_kind="authorization_rule_matched",
                decision="approve",
                source="remembered_rule",
                resolved_by="system",
                chatroom_id=chatroom.id,
                project_id=project.id,
                agent_name="Analyst",
                target_kind="tool",
                target_name="run_shell",
                tool_name="run_shell",
                scope="project",
                matcher_type="command_fingerprint",
                matcher_value="audit-fingerprint",
                command_preview="touch audit.txt @ .",
                reason="Matched saved authorization rule.",
                request_payload_json=json.dumps({"arguments": {"command": "touch audit.txt", "cwd": "."}}, ensure_ascii=False),
                resolution_payload_json=json.dumps({"preference_id": 7}, ensure_ascii=False),
            )
            db.add(row)
            db.commit()
            row_id = row.id
        finally:
            db.close()

        response = client.get("/api/monitor/approval-audit?decision=approve&limit=20")
        assert response.status_code == 200
        data = response.json()
        assert data["counts"]["automatic"] >= 1
        entry = next(item for item in data["entries"] if item["id"] == row_id)
        assert entry["event_kind"] == "authorization_rule_matched"
        assert entry["decision"] == "approve"
        assert entry["tool_name"] == "run_shell"
        assert entry["command_preview"] == "touch audit.txt @ ."
        assert entry["preview"] == "Matched saved authorization rule."
        assert entry["approval_fingerprint"] == "audit-fingerprint"
        assert entry["approval_fingerprint_kind"] == "command_fingerprint"
        assert entry["approval_fingerprint_input"]["command_preview"] == "touch audit.txt @ ."

    def test_monitor_task_runs_exposes_continuation_cursor(self, client):
        from models.database import ApprovalQueueItem, Chatroom, Project, SessionLocal, TaskRun, TaskRunEvent

        db = SessionLocal()
        try:
            project = Project(name="Continuation Cursor Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Continuation Cursor Chat",
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
                title="Blocked tool continuation",
                user_request="Blocked tool continuation",
                initiator="user",
                target_agent_name="Analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="tool_round_recorded",
                    agent_name="Analyst",
                    summary="Analyst completed a tool round.",
                    payload_json=json.dumps(
                        {
                            "turn": 1,
                            "tool_names": ["list_files"],
                            "tool_count": 1,
                            "tool_status_counts": {"succeeded": 1},
                            "blocked_tool_count": 0,
                            "turn_local_state": {
                                "assistant_content": "Inspect the repository layout first.",
                                "tool_results": [
                                    {
                                        "tool_call_id": "call_1",
                                        "tool_name": "list_files",
                                        "arguments": "{\"path\": \".\"}",
                                        "result": "README.md\\nbackend/",
                                        "success": True,
                                        "status": "succeeded",
                                        "blocked": False,
                                        "blocked_kind": None,
                                        "blocked_reason": None,
                                    }
                                ],
                                "protocol_messages": [
                                    {
                                        "role": "assistant",
                                        "content": "Inspect the repository layout first.",
                                        "tool_calls": [
                                            {
                                                "id": "call_1",
                                                "type": "function",
                                                "function": {"name": "list_files", "arguments": "{\"path\": \".\"}"},
                                            }
                                        ],
                                    },
                                    {
                                        "role": "tool",
                                        "tool_call_id": "call_1",
                                        "name": "list_files",
                                        "content": "README.md\\nbackend/",
                                    },
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="tool_round_recorded",
                    agent_name="Analyst",
                    summary="Analyst completed a second tool round.",
                    payload_json=json.dumps(
                        {
                            "turn": 3,
                            "tool_names": ["read_file"],
                            "tool_count": 1,
                            "tool_status_counts": {"succeeded": 1},
                            "blocked_tool_count": 0,
                            "turn_local_state": {
                                "assistant_content": "Open the API route file next.",
                                "tool_results": [
                                    {
                                        "tool_call_id": "call_2",
                                        "tool_name": "read_file",
                                        "arguments": "{\"file_path\": \"backend/routes/api.py\"}",
                                        "result": "async def send_message(...",
                                        "success": True,
                                        "status": "succeeded",
                                        "blocked": False,
                                        "blocked_kind": None,
                                        "blocked_reason": None,
                                    }
                                ],
                                "protocol_messages": [
                                    {
                                        "role": "assistant",
                                        "content": "Open the API route file next.",
                                        "tool_calls": [
                                            {
                                                "id": "call_2",
                                                "type": "function",
                                                "function": {"name": "read_file", "arguments": "{\"file_path\": \"backend/routes/api.py\"}"},
                                            }
                                        ],
                                    },
                                    {
                                        "role": "tool",
                                        "tool_call_id": "call_2",
                                        "name": "read_file",
                                        "content": "async def send_message(...",
                                    },
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=3,
                    event_type="tool_round_recorded",
                    agent_name="Analyst",
                    summary="Analyst completed a tool round.",
                    payload_json=json.dumps(
                        {
                            "turn": 3,
                            "tool_names": ["delete_file"],
                            "tool_count": 1,
                            "tool_status_counts": {"approval_blocked": 1},
                            "blocked_tool_count": 1,
                            "blocked_tools": [
                                {
                                    "tool_name": "delete_file",
                                    "arguments": "{\"file_path\": \"danger.txt\"}",
                                    "status": "approval_blocked",
                                    "blocked_kind": "approval",
                                    "blocked_reason": "delete_file requires approval",
                                }
                            ],
                            "turn_local_state": {
                                "assistant_content": "Delete the dangerous file next.",
                                "tool_results": [
                                    {
                                        "tool_call_id": "call_2",
                                        "tool_name": "delete_file",
                                        "arguments": "{\"file_path\": \"danger.txt\"}",
                                        "result": "delete_file requires approval",
                                        "success": False,
                                        "status": "approval_blocked",
                                        "blocked": True,
                                        "blocked_kind": "approval",
                                        "blocked_reason": "delete_file requires approval",
                                    }
                                ],
                                "protocol_messages": [
                                    {
                                        "role": "assistant",
                                        "content": "Delete the dangerous file next.",
                                        "tool_calls": [
                                            {
                                                "id": "call_2",
                                                "type": "function",
                                                "function": {"name": "delete_file", "arguments": "{\"file_path\": \"danger.txt\"}"},
                                            }
                                        ],
                                    },
                                    {
                                        "role": "tool",
                                        "tool_call_id": "call_2",
                                        "name": "delete_file",
                                        "content": "delete_file requires approval",
                                    },
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=4,
                    event_type="task_run_recovery_started",
                    agent_name="Analyst",
                    summary="Recovery checkpoint captured scheduler runtime.",
                    payload_json=json.dumps(
                        {
                            "runtime": {
                                "step_count": 4,
                                "completed_step_count": 1,
                                "ready_step_count": 2,
                                "running_step_count": 1,
                                "waiting_step_count": 0,
                            }
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=5,
                    event_type="tool_call_blocked",
                    agent_name="Analyst",
                    summary="delete_file was blocked.",
                    payload_json=json.dumps(
                        {
                            "turn": 2,
                            "tool_name": "delete_file",
                            "status": "approval_blocked",
                            "blocked_kind": "approval",
                            "blocked_reason": "delete_file requires approval",
                            "queue_item_id": 1,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()

            queue_item = ApprovalQueueItem(
                task_run_id=task_run.id,
                chatroom_id=chatroom.id,
                project_id=project.id,
                queue_kind="approval",
                status="pending",
                source="tool_call_blocked",
                title="Approval needed for delete_file",
                summary="delete_file requires approval",
                agent_name="Analyst",
                target_kind="tool",
                target_name="delete_file",
                request_payload_json=json.dumps(
                    {
                        "turn": 3,
                        "tool_name": "delete_file",
                        "arguments": "{\"file_path\": \"danger.txt\"}",
                        "blocked_kind": "approval",
                        "resume_supported": True,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(queue_item)
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        response = client.get("/api/monitor/task-runs?range=24h&limit=20")
        assert response.status_code == 200
        data = response.json()
        entry = next(item for item in data["entries"] if item["id"] == task_run_id)
        cursor = entry["checkpoint_snapshot"]["continuation_cursor"]
        assert cursor["next_action"] == "await_approval"
        assert cursor["resume_strategy"] == "resume_original_tool_call"
        assert cursor["tool_name"] == "delete_file"
        assert cursor["turn"] == 3
        assert entry["continuation_cursor"]["next_action"] == "await_approval"
        assert entry["continuation_cursor_summary"] == "await approval · via resume_original_tool_call · tool delete_file · turn 3"
        assert entry["continuation_state"]["consumed"] is True
        assert entry["continuation_state_summary"] == "await approval · via resume_original_tool_call · 4 tail messages · 1 prior summaries · protocol_tail, prior_round_summaries"
        assert entry["latest_scheduler_runtime"]["completed_step_count"] == 1
        assert entry["latest_scheduler_runtime"]["ready_step_count"] == 2
        assert entry["scheduler_runtime_summary"] == "1 completed · 2 ready · 1 running · 0 waiting · 4 total"
        continuation_state = entry["checkpoint_snapshot"]["continuation_state"]
        assert continuation_state["consumed"] is True
        assert continuation_state["next_action"] == "await_approval"
        assert continuation_state["protocol_tail_message_count"] == 4
        assert continuation_state["prior_round_summary_count"] == 1
        assert "protocol_tail" in continuation_state["consumed_layers"]
        assert entry["checkpoint_snapshot"]["continuation_cursor_summary"] == "await approval · via resume_original_tool_call · tool delete_file · turn 3"
        assert entry["checkpoint_snapshot"]["continuation_state_summary"] == "await approval · via resume_original_tool_call · 4 tail messages · 1 prior summaries · protocol_tail, prior_round_summaries"
        turn_local_state = entry["checkpoint_snapshot"]["turn_local_state"]
        assert turn_local_state["turn"] == 3
        assert turn_local_state["tool_names"] == ["delete_file"]
        assert turn_local_state["blocked_tool"]["tool_name"] == "delete_file"
        assert len(turn_local_state["protocol_messages"]) == 2
        assert len(turn_local_state["protocol_tail_messages"]) == 4
        assert len(turn_local_state["prior_round_summaries"]) == 1
        assert turn_local_state["prior_round_summaries"][0]["tool_names"] == ["list_files"]

    def test_monitor_task_runs_surface_consult_subagent_checkpoint_state(self, client):
        from models.database import Chatroom, Project, SessionLocal, TaskRun, TaskRunEvent

        db = SessionLocal()
        try:
            project = Project(name="Consult Monitor Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Consult Monitor Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            task_run = TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                run_kind="project_single_agent",
                status="running",
                title="Consult monitor state",
                user_request="Ask analyst for help.",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add_all(
                [
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=1,
                        event_type="scheduler_step_dispatched",
                        agent_name="Analyst",
                        summary="Consultation dispatched to Analyst.",
                        payload_json=json.dumps(
                            {
                                "step_id": "consult-1",
                                "position": 0,
                                "agent_name": "Analyst",
                                "agent_type": "analyst",
                                "dispatch_kind": "consult",
                                "source": "consult_agent",
                                "step_state": {
                                    "status": "running",
                                    "dispatch_count": 1,
                                    "completion_count": 0,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=2,
                        event_type="scheduler_step_completed",
                        agent_name="Analyst",
                        summary="Consultation completed by Analyst.",
                        payload_json=json.dumps(
                            {
                                "step_id": "consult-1",
                                "position": 0,
                                "agent_name": "Analyst",
                                "agent_type": "analyst",
                                "dispatch_kind": "consult",
                                "source": "consult_agent",
                                "response_preview": "Short consult answer",
                                "step_state": {
                                    "status": "completed",
                                    "dispatch_count": 1,
                                    "completion_count": 1,
                                },
                            },
                            ensure_ascii=False,
                        ),
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
        entry = next(item for item in data["entries"] if item["id"] == task_run_id)
        assert entry["latest_subagent_step"]["dispatch_kind"] == "consult"
        latest_subagent_step = entry["checkpoint_snapshot"]["latest_subagent_step"]
        assert latest_subagent_step["dispatch_kind"] == "consult"
        assert latest_subagent_step["status"] == "completed"
        assert latest_subagent_step["response_preview"] == "Short consult answer"
        assert entry["continuation_state"]["latest_subagent_dispatch_kind"] == "consult"
        assert entry["continuation_state"]["latest_subagent_status"] == "completed"
        assert "consult_subagent" in entry["continuation_state"]["consumed_layers"]

    def test_monitor_task_run_steps_merges_runtime_cards_and_events(self, client):
        from models.database import Chatroom, Message, Project, SessionLocal, TaskRun, TaskRunEvent

        db = SessionLocal()
        try:
            project = Project(name="Task Steps Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Task Steps Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            task_run = TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                client_turn_id="turn-task-steps",
                run_kind="project_single_agent",
                status="completed",
                title="Inspect runtime steps",
                user_request="Inspect runtime steps",
                initiator="user",
                target_agent_name="Analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=None,
                    content="llm_call",
                    message_type="runtime_card",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "turn-task-steps",
                            "card": {
                                "type": "llm_call",
                                "agent": "Analyst",
                                "model": "gpt-4.1-mini",
                                "turn": 1,
                                "tokens_in": 111,
                                "tokens_out": 37,
                                "duration_ms": 620,
                                "prompt_messages": json.dumps(
                                    [{"role": "user", "content": "Inspect runtime steps"}],
                                    ensure_ascii=False,
                                ),
                                "response": "Need to inspect README first.",
                                "tool_calls": [
                                    {
                                        "id": "tool-1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": "{\"file_path\": \"README.md\"}",
                                        },
                                    }
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                Message(
                    chatroom_id=chatroom.id,
                    agent_id=None,
                    content="tool_call",
                    message_type="runtime_card",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "turn-task-steps",
                            "card": {
                                "type": "tool_call",
                                "agent": "Analyst",
                                "tool": "read_file",
                                "arguments": "{\"file_path\": \"README.md\"}",
                                "success": True,
                                "status": "succeeded",
                                "result": "README contents",
                                "duration_ms": 91,
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add_all(
                [
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=1,
                        event_type="agent_turn_started",
                        agent_name="Analyst",
                        summary="Analyst started.",
                        payload_json=json.dumps({"client_turn_id": "turn-task-steps"}, ensure_ascii=False),
                    ),
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=2,
                        event_type="agent_turn_completed",
                        agent_name="Analyst",
                        summary="Analyst completed.",
                        payload_json=json.dumps({"response_preview": "Finished inspection."}, ensure_ascii=False),
                    ),
                ]
            )
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        response = client.get(f"/api/monitor/task-runs/{task_run_id}/steps")
        assert response.status_code == 200
        data = response.json()
        assert data["task_run_id"] == task_run_id
        assert data["counts"]["llm"] >= 1
        assert data["counts"]["tool"] >= 1
        assert data["counts"]["event"] >= 1
        assert data["counts"]["tokens_in"] >= 111
        assert data["counts"]["tokens_out"] >= 37

        llm_step = next(item for item in data["steps"] if item["step_kind"] == "llm")
        assert llm_step["agent_name"] == "Analyst"
        assert llm_step["model"] == "gpt-4.1-mini"
        assert llm_step["planned_tools"] == ["read_file"]
        assert "Inspect runtime steps" in (llm_step["prompt_preview"] or "")

        tool_step = next(item for item in data["steps"] if item["step_kind"] == "tool")
        assert tool_step["tool_name"] == "read_file"
        assert tool_step["success"] is True
        assert "README.md" in (tool_step["arguments"] or "")

        event_step = next(item for item in data["steps"] if item["step_kind"] == "event")
        assert event_step["event_type"] == "agent_turn_started"

    def test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn(self, client):
        from models.database import Chatroom, Project, SessionLocal, TaskRun, TaskRunEvent

        db = SessionLocal()
        try:
            project = Project(name="Turn Scope Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = Chatroom(
                project_id=project.id,
                title="Turn Scope Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            task_run = TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                run_kind="multi_agent_orchestration",
                status="running",
                title="Scoped checkpoint state",
                user_request="Resume work with fresh turn state",
                initiator="user",
                target_agent_name="Developer",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            for event_index, event_type, agent_name, payload in [
                (1, "agent_turn_started", "Analyst", {"client_turn_id": "turn-scope", "inter_agent_message_count": 0}),
                (
                    2,
                    "tool_round_recorded",
                    "Analyst",
                    {
                        "turn": 1,
                        "tool_names": ["read_file"],
                        "tool_count": 1,
                        "tool_status_counts": {"succeeded": 1},
                        "blocked_tool_count": 0,
                        "turn_local_state": {
                            "assistant_content": "Open the design doc before continuing.",
                            "tool_results": [
                                {
                                    "tool_call_id": "scope_call_1",
                                    "tool_name": "read_file",
                                    "arguments": "{\"file_path\": \"docs/design.md\"}",
                                    "result": "Design checkpoint contents",
                                    "success": True,
                                    "status": "succeeded",
                                    "blocked": False,
                                    "blocked_kind": None,
                                    "blocked_reason": None,
                                }
                            ],
                            "protocol_messages": [
                                {
                                    "role": "assistant",
                                    "content": "Open the design doc before continuing.",
                                    "tool_calls": [
                                        {
                                            "id": "scope_call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "read_file",
                                                "arguments": "{\"file_path\": \"docs/design.md\"}",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "role": "tool",
                                    "tool_call_id": "scope_call_1",
                                    "name": "read_file",
                                    "content": "Design checkpoint contents",
                                },
                            ],
                        },
                    },
                ),
                (
                    3,
                    "agent_turn_completed",
                    "Analyst",
                    {"response_preview": "Analyst checkpoint before handoff.", "message_id": 11},
                ),
                (4, "agent_turn_started", "Developer", {"client_turn_id": "turn-scope", "inter_agent_message_count": 1}),
                (
                    5,
                    "agent_turn_completed",
                    "Developer",
                    {"response_preview": "Developer resumed from analyst handoff.", "message_id": 12},
                ),
            ]:
                db.add(
                    TaskRunEvent(
                        task_run_id=task_run.id,
                        event_index=event_index,
                        event_type=event_type,
                        agent_name=agent_name,
                        summary=f"{agent_name} {event_type}",
                        payload_json=json.dumps(payload, ensure_ascii=False),
                    )
                )
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        response = client.get("/api/monitor/task-runs?range=24h&limit=20")
        assert response.status_code == 200
        data = response.json()
        entry = next(item for item in data["entries"] if item["id"] == task_run_id)
        assert entry["checkpoint_snapshot"]["continuation_cursor"]["next_action"] == "none"
        assert entry["continuation_cursor_summary"] is None
        assert entry["continuation_state"]["consumed"] is False
        assert entry["continuation_state_summary"] is None
        assert entry["checkpoint_snapshot"]["continuation_cursor_summary"] is None
        assert entry["checkpoint_snapshot"]["continuation_state"]["consumed"] is False
        assert entry["checkpoint_snapshot"]["continuation_state_summary"] is None
        turn_local_state = entry["checkpoint_snapshot"]["turn_local_state"]
        assert turn_local_state["turn"] is None
        assert turn_local_state["tool_names"] is None
        assert turn_local_state["protocol_messages"] == []
        assert turn_local_state["protocol_tail_messages"] == []
        assert turn_local_state["prior_round_summaries"] == []

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

    def test_network_install_trims_persisted_rows_to_one_week(self, tmp_path):
        from datetime import datetime, timedelta

        _make_app(tmp_path)
        from models.audit import MonitorNetworkRecord
        from models.database import SessionLocal
        from monitoring import monitor_network_buffer

        monitor_network_buffer.clear()
        previous_hours = os.environ.get("MONITOR_NETWORK_RETENTION_HOURS")
        os.environ["MONITOR_NETWORK_RETENTION_HOURS"] = "168"
        try:
            db = SessionLocal()
            try:
                db.add(
                    MonitorNetworkRecord(
                        created_at=datetime.now() - timedelta(days=8),
                        category="backend_other",
                        source="test",
                        protocol="HTTPS",
                        from_entity="Backend",
                        to_entity="WWW",
                        method="GET",
                        url="https://old.example.com/stale",
                        host="old.example.com",
                        path="/stale",
                        status_code=200,
                        success=True,
                    )
                )
                db.add(
                    MonitorNetworkRecord(
                        created_at=datetime.now() - timedelta(days=6),
                        category="backend_other",
                        source="test",
                        protocol="HTTPS",
                        from_entity="Backend",
                        to_entity="WWW",
                        method="GET",
                        url="https://fresh.example.com/kept",
                        host="fresh.example.com",
                        path="/kept",
                        status_code=200,
                        success=True,
                    )
                )
                db.commit()
            finally:
                db.close()

            assert monitor_network_buffer.install() is True
            entries = monitor_network_buffer.list_entries(limit=10)
        finally:
            if previous_hours is None:
                os.environ.pop("MONITOR_NETWORK_RETENTION_HOURS", None)
            else:
                os.environ["MONITOR_NETWORK_RETENTION_HOURS"] = previous_hours

        urls = [entry["url"] for entry in entries]
        assert "https://old.example.com/stale" not in urls
        assert "https://fresh.example.com/kept" in urls

    def test_network_api_skips_internal_traffic_before_limit(self, client):
        from monitoring import monitor_network_buffer

        monitor_network_buffer.clear()
        monitor_network_buffer.append(
            {
                "category": "backend_llm",
                "source": "backend",
                "protocol": "HTTPS",
                "from_entity": "valet",
                "to_entity": "LLM (example.com)",
                "method": "POST",
                "url": "https://example.com/v1/chat/completions",
                "host": "example.com",
                "path": "/v1/chat/completions",
                "status_code": 200,
                "success": True,
                "flow_id": "llm-http-visible",
                "flow_kind": "llm_http",
                "flow_seq": 3,
                "aggregated": False,
                "metadata": {"frame_type": "response_chunk"},
                "raw_response": "{\"id\":\"visible\"}",
                "preview": "{\"id\":\"visible\"}",
            }
        )
        for index in range(40):
            monitor_network_buffer.append(
                {
                    "category": "frontend_backend",
                    "source": "frontend",
                    "protocol": "HTTP",
                    "from_entity": "Frontend (monitor)",
                    "to_entity": "Backend",
                    "method": "GET",
                    "url": f"http://localhost:8000/api/monitor/network?i={index}",
                    "host": "localhost",
                    "path": "/api/monitor/network",
                    "status_code": 200,
                    "success": True,
                    "preview": f"internal {index}",
                }
            )

        response = client.get("/api/monitor/network?limit=20")
        assert response.status_code == 200
        payload = response.json()
        urls = [entry["url"] for entry in payload["entries"]]
        assert "https://example.com/v1/chat/completions" in urls
        assert all(entry["category"] != "frontend_backend" for entry in payload["entries"])
