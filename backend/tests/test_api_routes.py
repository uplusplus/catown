"""
API 路由测试

使用 FastAPI TestClient 测试 REST 端点（mock LLM）
"""
import pytest
import asyncio
import sys
import os
import json
import threading
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_app(tmp_path):
    """在指定临时目录下创建全新的 FastAPI app（隔离测试）"""
    import importlib

    # 设置环境变量
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

    # 清理缓存模块，强制重新加载
    modules_to_clear = [
        'main', 'config', 'models.database', 'agents.registry',
        'agents.collaboration', 'tools', 'llm.client', 'chatrooms.manager',
        'routes.api', 'routes.monitor', 'routes.websocket', 'pipeline.engine', 'routes.pipeline',
        'services.approval_queue',
        'services.approval_replay',
        'services.runtime_lifecycle',
        'services.monitor_projection',
        'services.run_ledger',
        'services.task_activity_projection',
        'services.agent_lifecycle_runtime',
        'services.agent_action_runtime',
        'services.subagent_runtime_control',
        'services.collaboration_dispatch_runtime',
        'services.collaboration_membership_runtime',
        'services.collaboration_runtime',
        'services.chat_publish',
        'services.chat_runtime',
        'services.orchestration_events',
        'services.orchestration_handoffs',
        'services.orchestration_inbox',
        'services.orchestration_agent_turn',
        'services.orchestration_step_state',
        'services.orchestration_step_completion',
        'services.orchestration_step_runner',
        'services.orchestration_runtime_runner',
        'services.orchestration_stream_runner',
        'services.orchestration_recovery_prepare',
        'services.orchestration_recovery_runner',
        'services.orchestration_guards',
        'services.orchestration_recovery_lease',
        'services.stream_runtime_persistence',
        'services.single_agent_session_runner',
        'services.stream_transport',
        'services.single_agent_stream_session',
        'services.single_agent_stream_finalizer',
        'services.approval_audit',
        'services.tool_execution_preferences',
    ]
    from tests.conftest import reset_app_modules
    reset_app_modules(modules_to_clear)

    # Mock LLM client
    import llm.client as llm_mod
    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(return_value={
        "content": "Mocked agent response.",
        "tool_calls": None
    })

    async def mock_stream(messages, tools=None):
        yield {"type": "content", "delta": "Hello!"}
        yield {"type": "done", "full_content": "Hello!", "tool_calls": None}
    mock_llm.chat_stream = mock_stream
    llm_mod._llm_client = mock_llm

    # 导入 main（此时会用正确的环境变量初始化）
    import main as main_mod

    # 简化中间件（测试环境 request.client 为 None）
    async def passthrough(self, request, call_next):
        return await call_next(request)

    main_mod.RateLimitMiddleware.dispatch = passthrough
    main_mod.RequestLoggingMiddleware.dispatch = passthrough

    return main_mod.app


@pytest.fixture
def client(tmp_path):
    """创建 FastAPI TestClient（完全隔离）"""
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    return TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"})


def _seed_task_run_with_queue_item(*, chatroom_id: int, project_id: int | None = None) -> tuple[int, int]:
    import models.database as db_mod

    db = db_mod.SessionLocal()
    try:
        task_run = db_mod.TaskRun(
            chatroom_id=chatroom_id,
            project_id=project_id,
            run_kind="chat_turn",
            status="running",
            title="Cleanup Queue Test",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue_item = db_mod.ApprovalQueueItem(
            task_run_id=task_run.id,
            chatroom_id=chatroom_id,
            project_id=project_id,
            queue_kind="approval",
            status="pending",
            source="runtime",
            title="Need approval",
            summary="Cleanup validation",
            target_kind="tool",
            target_name="execute_code",
        )
        db.add(queue_item)
        db.commit()
        db.refresh(queue_item)
        return task_run.id, queue_item.id
    finally:
        db.close()


def _approval_queue_item_exists(item_id: int) -> bool:
    import models.database as db_mod

    db = db_mod.SessionLocal()
    try:
        return (
            db.query(db_mod.ApprovalQueueItem)
            .filter(db_mod.ApprovalQueueItem.id == item_id)
            .first()
            is not None
        )
    finally:
        db.close()


def _wait_for(predicate, timeout_seconds: float = 3.0, interval_seconds: float = 0.05):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval_seconds)
    return None


def _wait_for_task_run_event(client, task_run_id: int, event_type: str, *, payload_predicate=None, timeout_seconds: float = 3.0):
    def _poll():
        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        for event in detail.get("events", []):
            if event.get("event_type") != event_type:
                continue
            if payload_predicate and not payload_predicate(event.get("payload") or {}):
                continue
            return detail
        return None

    return _wait_for(_poll, timeout_seconds=timeout_seconds)


# ==================== 健康检查 ====================

class TestHealthEndpoints:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_api_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ==================== 系统状态 ====================

class TestStatusEndpoint:
    def test_status(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert "stats" in data
        assert "features" in data
        assert data["features"]["tools_enabled"] is True
        assert data["features"]["memory_enabled"] is True


# ==================== Agent API ====================

class TestAgentEndpoints:
    def test_list_agents(self, client):
        r = client.get("/api/agents")
        assert r.status_code == 200
        agents = r.json()
        assert len(agents) >= 4
        names = [a["name"] for a in agents]
        for expected in ["Analyst", "Developer", "Tester", "Architect"]:
            assert expected in names, f"Missing agent: {expected}"

    def test_get_agent_by_id(self, client):
        r = client.get("/api/agents")
        agent_id = r.json()[0]["id"]
        r2 = client.get(f"/api/agents/{agent_id}")
        assert r2.status_code == 200
        assert r2.json()["id"] == agent_id

    def test_get_agent_not_found(self, client):
        r = client.get("/api/agents/99999")
        assert r.status_code == 404

    def test_get_agent_memory(self, client):
        r = client.get("/api/agents")
        agent_id = r.json()[0]["id"]
        r2 = client.get(f"/api/agents/{agent_id}/memory")
        assert r2.status_code == 200
        assert "memory_count" in r2.json()

    def test_get_agent_memory_returns_full_content(self, client):
        from models.database import Agent, Memory, SessionLocal

        long_content = "remember " + ("full content " * 30)
        db = SessionLocal()
        try:
            agent = Agent(name="memory-agent", role="assistant", is_active=True)
            db.add(agent)
            db.commit()
            db.refresh(agent)
            agent_id = agent.id
            db.add(Memory(
                agent_id=agent_id,
                memory_type="context",
                content=long_content,
                importance=9,
            ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/agents/{agent_id}/memory")

        assert response.status_code == 200
        contents = [memory["content"] for memory in response.json()["memories"]]
        assert long_content in contents


# ==================== 工具 API ====================

class TestToolsEndpoint:
    def test_list_tools(self, client):
        r = client.get("/api/tools")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 14
        tool_names = [t["name"] for t in data["tools"]]
        for t in ["web_search", "execute_code", "run_shell", "delegate_task", "save_memory", "read_file"]:
            assert t in tool_names, f"Missing tool: {t}"
        assert "send_direct_message" not in tool_names


# ==================== 配置 API ====================

class TestConfigEndpoint:
    def test_get_config(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "llm" in data
        assert "server" in data

    def test_get_config_includes_agent_scoped_tool_policies(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        assert "agent_tools" in data
        assert isinstance(data["agent_tools"], dict)
        developer_tools = data["agent_tools"].get("developer", {})
        assert "tool_policies" in developer_tools
        developer_tool_names = [policy.get("name") for policy in developer_tools.get("tool_policies", [])]
        assert "read_file" in developer_tool_names
        assert "send_direct_message" not in developer_tool_names
        assert "system_tools" in data
        system_tool_names = [policy.get("name") for policy in data["system_tools"].get("tool_policies", [])]
        assert "send_direct_message" in system_tool_names

    def test_update_permissions_config_includes_auto_approve_all(self, tmp_path):
        from fastapi.testclient import TestClient

        config_path = tmp_path / "agents.json"
        config_path.write_text(json.dumps({"agents": {}}, ensure_ascii=False), encoding="utf-8")
        previous_config_file = os.environ.get("AGENT_CONFIG_FILE")
        try:
            os.environ["AGENT_CONFIG_FILE"] = str(config_path)
            client = TestClient(
                _make_app(tmp_path),
                base_url="http://testserver",
                headers={"X-Catown-Client": "test"},
            )

            response = client.put(
                "/api/config/permissions",
                json={
                    "allow_read_only_tools_without_approval": False,
                    "auto_approve_all": True,
                },
            )

            assert response.status_code == 200
            refreshed = client.get("/api/config").json()
            assert refreshed["permissions"]["allow_read_only_tools_without_approval"] is False
            assert refreshed["permissions"]["auto_approve_all"] is True
        finally:
            if previous_config_file is None:
                os.environ.pop("AGENT_CONFIG_FILE", None)
            else:
                os.environ["AGENT_CONFIG_FILE"] = previous_config_file

    def test_llm_card_payload_includes_usage_context(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "global_llm": {
                        "provider": {
                            "baseUrl": "https://api.openai.com/v1",
                            "apiKey": "sk-test",
                            "models": [
                                {
                                    "id": "gpt-5.4-mini",
                                    "name": "GPT-5.4 Mini",
                                    "contextWindow": 128000,
                                    "maxTokens": 8192,
                                }
                            ],
                        },
                        "default_model": "gpt-5.4-mini",
                    },
                    "agents": {
                        "assistant": {
                            "name": "assistant",
                            "provider": {
                                "baseUrl": "https://api.openai.com/v1",
                                "apiKey": "sk-test",
                                "models": [
                                    {
                                        "id": "gpt-5.4-mini",
                                        "name": "GPT-5.4 Mini",
                                        "contextWindow": 128000,
                                        "maxTokens": 8192,
                                    }
                                ],
                            },
                            "default_model": "gpt-5.4-mini",
                            "role": {"title": "Assistant", "responsibilities": [], "rules": []},
                            "soul": {"identity": "Helpful", "values": [], "style": "", "quirks": ""},
                            "tools": [],
                            "skills": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        previous_config_file = os.environ.get("AGENT_CONFIG_FILE")
        try:
            os.environ["AGENT_CONFIG_FILE"] = str(config_path)
            _make_app(tmp_path)

            import routes.api as api_mod

            payload = api_mod._build_llm_card_payload(
                agent_name="assistant",
                llm_client=MagicMock(model="gpt-5.4-mini"),
                turn=2,
                duration_ms=321,
                system_prompt="system",
                prompt_messages=[{"role": "user", "content": "hello"}],
                response_content="world",
                tool_call_previews=[],
                usage={
                    "prompt_tokens": 64000,
                    "completion_tokens": 1200,
                    "total_tokens": 65200,
                },
            )

            assert payload["tokens_in"] == 64000
            assert payload["tokens_out"] == 1200
            assert payload["tokens_total"] == 65200
            assert payload["context_window"] == 128000
            assert payload["context_usage_ratio"] == 0.5
        finally:
            if previous_config_file is None:
                os.environ.pop("AGENT_CONFIG_FILE", None)
            else:
                os.environ["AGENT_CONFIG_FILE"] = previous_config_file

    def test_tool_authorization_rules_can_be_listed_and_revoked(self, client):
        import models.database as db_mod
        from services.tool_execution_preferences import (
            AUTH_DECISION_ALLOW,
            AUTH_MATCHER_COMMAND_FINGERPRINT,
            AUTH_PREFERENCE_KIND,
            build_run_shell_command_matcher_value,
            upsert_authorization_rule,
        )

        project = client.post("/api/projects", json={"name": "Rule API Project", "agent_names": ["analyst"]}).json()

        db = db_mod.SessionLocal()
        try:
            rule = upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="project",
                matcher_type=AUTH_MATCHER_COMMAND_FINGERPRINT,
                matcher_value=build_run_shell_command_matcher_value("touch created.txt", "."),
                decision_kind=AUTH_DECISION_ALLOW,
                project_id=project["id"],
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="granted",
                command_preview="touch created.txt @ .",
            )
            rule_id = rule.id
        finally:
            db.close()

        listed = client.get("/api/tool-authorization-rules", params={"project_id": project["id"]}).json()
        assert any(item["id"] == rule_id and item["decision_kind"] == "allow" for item in listed)

        revoked = client.delete(f"/api/tool-authorization-rules/{rule_id}").json()
        assert revoked["message"] == "Authorization rule revoked"
        assert revoked["rule"]["revoked_at"] is not None

        listed_after = client.get("/api/tool-authorization-rules", params={"project_id": project["id"]}).json()
        assert all(item["id"] != rule_id for item in listed_after)

    def test_approval_decision_can_remember_project_level_allow_and_deny_rules(self, client):
        import models.database as db_mod

        project = client.post("/api/projects", json={"name": "Remember Rule Project", "agent_names": ["analyst"]}).json()
        cid = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            allow_item = db_mod.ApprovalQueueItem(
                task_run_id=None,
                chatroom_id=cid,
                project_id=project["id"],
                queue_kind="approval",
                status="pending",
                source="tool_call_blocked",
                title="Approve run_shell",
                summary="run_shell blocked in project chat",
                agent_name="analyst",
                target_kind="tool",
                target_name="run_shell",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "run_shell",
                        "arguments": json.dumps({"command": "touch created.txt", "cwd": "."}, ensure_ascii=False),
                        "blocked_kind": "approval",
                        "resume_supported": True,
                        "turn": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            deny_item = db_mod.ApprovalQueueItem(
                task_run_id=None,
                chatroom_id=cid,
                project_id=project["id"],
                queue_kind="approval",
                status="pending",
                source="tool_call_blocked",
                title="Deny run_shell",
                summary="run_shell blocked in project chat",
                agent_name="analyst",
                target_kind="tool",
                target_name="run_shell",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "run_shell",
                        "arguments": json.dumps({"command": "pwd", "cwd": "."}, ensure_ascii=False),
                        "blocked_kind": "approval",
                        "resume_supported": True,
                        "turn": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add_all([allow_item, deny_item])
            db.commit()
            db.refresh(allow_item)
            db.refresh(deny_item)
            allow_item_id = allow_item.id
            deny_item_id = deny_item.id
        finally:
            db.close()

        approved = client.post(
            f"/api/approval-queue/{allow_item_id}/approve",
            json={"remember_scope": "project"},
        ).json()
        rejected = client.post(
            f"/api/approval-queue/{deny_item_id}/reject",
            json={"remember_scope": "project"},
        ).json()

        assert approved["status"] == "approved"
        assert rejected["status"] == "rejected"

        rules = client.get("/api/tool-authorization-rules", params={"project_id": project["id"]}).json()
        assert any(rule["decision_kind"] == "allow" and rule["command_preview"] == "touch created.txt @ ." for rule in rules)
        assert any(rule["decision_kind"] == "deny" and rule["command_preview"] == "pwd @ ." for rule in rules)

        audit_rows = client.get("/api/monitor/approval-audit?decision=all&limit=20").json()["entries"]
        assert any(row["event_kind"] == "queue_item_approved" and row["queue_item_id"] == allow_item_id for row in audit_rows)
        assert any(row["event_kind"] == "queue_item_rejected" and row["queue_item_id"] == deny_item_id for row in audit_rows)
        assert any(
            row["event_kind"] == "authorization_rule_saved"
            and row["decision"] == "allow"
            and row["command_preview"] == "touch created.txt @ ."
            for row in audit_rows
        )
        assert any(
            row["event_kind"] == "authorization_rule_saved"
            and row["decision"] == "deny"
            and row["command_preview"] == "pwd @ ."
            for row in audit_rows
        )

    def test_remembered_run_shell_approval_matches_workspace_cwd_alias(self, client):
        import models.database as db_mod
        from tools import tool_registry
        from tools.file_operations import reset_active_workspace, set_active_workspace

        project = client.post("/api/projects", json={"name": "Remember Shell Alias", "agent_names": ["analyst"]}).json()
        cid = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            queue_item = db_mod.ApprovalQueueItem(
                task_run_id=None,
                chatroom_id=cid,
                project_id=project["id"],
                queue_kind="approval",
                status="pending",
                source="tool_call_blocked",
                title="Approve run_shell",
                summary="run_shell blocked in project chat",
                agent_name="analyst",
                target_kind="tool",
                target_name="run_shell",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "run_shell",
                        "arguments": json.dumps(
                            {"command": "touch remembered-alias.txt", "cwd": project["workspace_path"]},
                            ensure_ascii=False,
                        ),
                        "blocked_kind": "approval",
                        "resume_supported": False,
                        "turn": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(queue_item)
            db.commit()
            db.refresh(queue_item)
            queue_item_id = queue_item.id
        finally:
            db.close()

        approved = client.post(
            f"/api/approval-queue/{queue_item_id}/approve",
            json={"remember_scope": "project"},
        ).json()

        assert approved["status"] == "approved"
        assert approved["resolution_payload"]["remembered_rule"]["decision_kind"] == "allow"

        workspace_token = set_active_workspace(project["workspace_path"])
        try:
            result = asyncio.run(
                tool_registry.execute(
                    "run_shell",
                    command="touch remembered-alias.txt",
                    cwd=".",
                    project_id=project["id"],
                    chatroom_id=cid,
                    __catown_workspace_path=project["workspace_path"],
                )
            )
        finally:
            reset_active_workspace(workspace_token)

        assert result["success"] is True
        assert Path(project["workspace_path"], "remembered-alias.txt").exists()

    def test_update_agent_full_config(self, tmp_path):
        from fastapi.testclient import TestClient

        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "global_llm": {
                        "provider": {
                            "baseUrl": "https://api.openai.com/v1",
                            "apiKey": "sk-test",
                            "models": [{"id": "gpt-4.1-mini"}],
                        },
                        "default_model": "gpt-4.1-mini",
                    },
                    "agents": {
                        "assistant": {
                            "provider": {},
                            "default_model": "",
                            "role": {"title": "Assistant", "responsibilities": [], "rules": []},
                            "soul": {"identity": "", "values": [], "style": "", "quirks": ""},
                            "tools": [],
                            "skills": [],
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        previous_config_file = os.environ.get("AGENT_CONFIG_FILE")
        try:
            os.environ["AGENT_CONFIG_FILE"] = str(config_path)
            client = TestClient(
                _make_app(tmp_path),
                base_url="http://testserver",
                headers={"X-Catown-Client": "test"},
            )

            response = client.put(
                "/api/config/agent/assistant",
                json={
                    "default_model": "gpt-4.1-mini",
                    "role": {
                        "title": "Lead Assistant",
                        "responsibilities": ["Coordinate", "Explain"],
                        "rules": ["Stay concise"],
                    },
                    "soul": {
                        "identity": "Helpful system operator",
                        "style": "Direct",
                        "values": ["clarity", "speed"],
                        "quirks": "Uses short bullets",
                    },
                    "tools": ["read_file", "web_search"],
                    "skills": ["openai-docs"],
                },
            )
            assert response.status_code == 200

            refreshed = client.get("/api/config").json()
            agent = refreshed["agents"]["valet"]
            assert agent["default_model"] == "gpt-4.1-mini"
            assert agent["role"]["title"] == "Lead Assistant"
            assert agent["role"]["responsibilities"] == ["Coordinate", "Explain"]
            assert agent["soul"]["identity"] == "Helpful system operator"
            assert agent["soul"]["values"] == ["clarity", "speed"]
            assert agent["tools"] == ["read_file", "web_search"]
            assert agent["skills"] == ["openai-docs"]
        finally:
            if previous_config_file is None:
                os.environ.pop("AGENT_CONFIG_FILE", None)
            else:
                os.environ["AGENT_CONFIG_FILE"] = previous_config_file

    def test_update_config_validation_empty_key(self, client):
        r = client.post("/api/config", json={
            "api_key": "", "base_url": "https://api.openai.com/v1", "model": "gpt-4"
        })
        assert r.status_code == 422

    def test_update_config_invalid_url(self, client):
        r = client.post("/api/config", json={
            "api_key": "sk-test", "base_url": "not-a-url", "model": "gpt-4"
        })
        assert r.status_code == 422

    def test_update_config_invalid_temperature(self, client):
        r = client.post("/api/config", json={
            "api_key": "sk-test", "temperature": 5.0
        })
        assert r.status_code == 422


# ==================== 项目 CRUD ====================

class TestProjectEndpoints:
    def test_list_projects_empty(self, client):
        r = client.get("/api/projects")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_project(self, client):
        r = client.post("/api/projects", json={
            "name": "Test Project", "description": "A test"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Test Project"
        assert data["display_order"] == 0
        assert data["chatroom_id"] is not None
        assert data["default_chatroom_id"] == data["chatroom_id"]
        assert len(data["agents"]) == 1
        assert data["agents"][0]["name"] == "Valet"
        assert data["workspace_path"]
        assert os.path.isdir(data["workspace_path"])

    def test_get_or_create_self_bootstrap_project(self, client):
        first = client.post("/api/projects/self-bootstrap")
        assert first.status_code == 200
        first_data = first.json()
        assert first_data["workspace_path"]
        assert first_data["default_chatroom_id"] == first_data["chatroom_id"]
        assert first_data["workspace_path"].endswith("catown")

        second = client.post("/api/projects/self-bootstrap")
        assert second.status_code == 200
        second_data = second.json()
        assert second_data["id"] == first_data["id"]
        assert second_data["default_chatroom_id"] == first_data["default_chatroom_id"]

    def test_create_project_from_chat(self, client):
        chat = client.post("/api/chats", json={"title": "Seed Chat"}).json()
        client.post(f"/api/chatrooms/{chat['id']}/messages", json={"content": "Carry this into the project"})

        r = client.post("/api/projects/from-chat", json={
            "source_chatroom_id": chat["id"],
            "name": "Chat Seed Project",
            "description": "created from standalone chat",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["created_from_chatroom_id"] == chat["id"]
        assert data["agents"][0]["name"] == "Valet"
        assert os.path.isdir(data["workspace_path"])

        listed_chats = client.get("/api/chats").json()
        assert any(row["id"] == chat["id"] for row in listed_chats)
        assert all(row["id"] != data["default_chatroom_id"] for row in listed_chats)

        messages = client.get(f"/api/chatrooms/{data['default_chatroom_id']}/messages").json()
        assert any("created from standalone chat" in message["content"] for message in messages)
        assert any(message["content"] == "Carry this into the project" for message in messages)

    def test_create_project_from_github(self, client):
        from services.session_service import SessionService

        def fake_clone(self, clone_url, destination, ref=None):
            assert clone_url == "https://github.com/octocat/Hello-World.git"
            assert ref == "main"
            destination.mkdir(parents=True, exist_ok=True)
            (destination / ".git").mkdir()
            (destination / "README.md").write_text("# Hello World\n", encoding="utf-8")

        with patch.object(SessionService, "_clone_github_repository", fake_clone):
            r = client.post(
                "/api/projects/from-github",
                json={
                    "repo_url": "octocat/Hello-World",
                    "description": "Imported test project",
                    "ref": "main",
                },
            )

        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Hello-World"
        assert data["source_type"] == "github"
        assert data["repo_url"] == "https://github.com/octocat/Hello-World"
        assert data["repo_full_name"] == "octocat/Hello-World"
        assert data["clone_ref"] == "main"
        assert os.path.isdir(data["workspace_path"])
        assert os.path.isfile(os.path.join(data["workspace_path"], "README.md"))

    def test_create_project_from_github_with_ref_runs_explicit_branch_checkout(self, client):
        from services.session_service import SessionService

        commands = []

        class CompletedProcess:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_git_prefix(self):
            return ["git"]

        def fake_run(command, capture_output, text, timeout, env):
            commands.append(command)
            if command[:2] == ["git", "clone"]:
                destination = command[-1]
                os.makedirs(destination, exist_ok=True)
                os.makedirs(os.path.join(destination, ".git"), exist_ok=True)
            if len(command) >= 7 and command[0] == "git" and command[1] == "-C" and command[3:6] == ["rev-parse", "--verify", "--quiet"]:
                ref = command[6]
                if ref == "refs/remotes/origin/mobile":
                    return CompletedProcess(stdout="deadbeef\n")
                return CompletedProcess(returncode=1)
            return CompletedProcess()

        with patch.object(SessionService, "_git_command_prefix", fake_git_prefix), patch(
            "services.session_service.subprocess.run",
            side_effect=fake_run,
        ):
            response = client.post(
                "/api/projects/from-github",
                json={
                    "repo_url": "octocat/Hello-World",
                    "description": "Imported test project",
                    "ref": "mobile",
                },
            )

        assert response.status_code == 200
        assert any(command[:2] == ["git", "clone"] for command in commands)
        assert any(
            len(command) >= 7
            and command[0] == "git"
            and command[1] == "-C"
            and command[3:] == ["checkout", "-B", "mobile", "refs/remotes/origin/mobile"]
            for command in commands
        )

    def test_sync_github_project(self, client):
        from services.session_service import SessionService

        def fake_clone(self, clone_url, destination, ref=None):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / ".git").mkdir()
            (destination / "README.md").write_text("# Hello World\n", encoding="utf-8")

        with patch.object(SessionService, "_clone_github_repository", fake_clone):
            project = client.post(
                "/api/projects/from-github",
                json={"repo_url": "octocat/Hello-World", "description": "Imported test project"},
            ).json()

        statuses = iter([
            {
                "branch": "main",
                "head_commit": "1111111122222222333333334444444455555555",
                "head_short": "11111111",
                "detached": False,
            },
            {
                "branch": "main",
                "head_commit": "aaaaaaaa22222222333333334444444455555555",
                "head_short": "aaaaaaaa",
                "detached": False,
            },
        ])

        def fake_status(self, workspace):
            return next(statuses)

        def fake_sync(self, workspace, ref=None):
            assert workspace.name.startswith("octocat-Hello-World")
            assert ref is None

        with patch.object(SessionService, "_read_git_workspace_status", fake_status), patch.object(SessionService, "_sync_git_workspace", fake_sync):
            synced = client.post(f"/api/projects/{project['id']}/sync")

        assert synced.status_code == 200
        data = synced.json()
        assert data["updated"] is True
        assert data["branch"] == "main"
        assert data["head_short"] == "aaaaaaaa"
        assert data["previous_head_commit"] == "1111111122222222333333334444444455555555"
        assert data["project"]["id"] == project["id"]
        assert "Pulled latest changes" in data["summary"]

    def test_create_project_subchat(self, client):
        project = client.post("/api/projects", json={"name": "Parent Project"}).json()

        created = client.post(f"/api/projects/{project['id']}/subchats", json={})
        assert created.status_code == 200
        data = created.json()
        assert data["project_id"] == project["id"]
        assert data["session_type"] == "project-bound"
        assert data["is_visible_in_chat_list"] is True

        listed_chats = client.get("/api/chats").json()
        assert any(chat["id"] == data["id"] and chat["project_id"] == project["id"] for chat in listed_chats)

    def test_reorder_projects(self, client):
        first = client.post("/api/projects", json={"name": "First"}).json()
        second = client.post("/api/projects", json={"name": "Second"}).json()
        third = client.post("/api/projects", json={"name": "Third"}).json()

        reordered = client.put(
            "/api/projects/reorder",
            json={"project_ids": [third["id"], first["id"], second["id"]]},
        )
        assert reordered.status_code == 200
        rows = reordered.json()
        assert [row["id"] for row in rows] == [third["id"], first["id"], second["id"]]
        assert [row["display_order"] for row in rows] == [0, 1, 2]

        listed = client.get("/api/projects").json()
        assert [row["id"] for row in listed] == [third["id"], first["id"], second["id"]]

    def test_open_project_workspace(self, client):
        project = client.post("/api/projects", json={"name": "Workspace Project"}).json()

        with patch("routes.api._open_workspace_path") as open_workspace:
            response = client.post(f"/api/projects/{project['id']}/open-workspace")

        assert response.status_code == 200
        open_workspace.assert_called_once_with(project["workspace_path"])

    def test_create_project_invalid_agent(self, client):
        r = client.post("/api/projects", json={
            "name": "Bad", "agent_names": ["nonexistent"]
        })
        assert r.status_code == 400

    def test_create_project_empty_agent_list_defaults_to_assistant(self, client):
        r = client.post("/api/projects", json={
            "name": "Fallback Agent",
            "agent_names": []
        })
        assert r.status_code == 200
        assert [agent["name"] for agent in r.json()["agents"]] == ["Valet"]

    def test_create_project_multiple_agents(self, client):
        r = client.post("/api/projects", json={
            "name": "Multi", "agent_names": ["analyst", "developer", "tester"]
        })
        assert r.status_code == 200
        assert len(r.json()["agents"]) == 3

    def test_get_project(self, client):
        r = client.post("/api/projects", json={
            "name": "GetTest", "agent_names": ["analyst"]
        })
        pid = r.json()["id"]
        r2 = client.get(f"/api/projects/{pid}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "GetTest"

    def test_get_project_browser_indexes_workspace_files_and_artifacts(self, client):
        project = client.post("/api/projects", json={"name": "BrowserTest"}).json()
        workspace = Path(project["workspace_path"])
        (workspace / "src").mkdir()
        (workspace / "docs").mkdir()
        (workspace / "node_modules").mkdir()
        (workspace / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (workspace / "docs" / "ADR-001-browser.md").write_text("# ADR\n", encoding="utf-8")
        (workspace / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")

        response = client.get(f"/api/projects/{project['id']}/browser")
        assert response.status_code == 200
        data = response.json()
        file_paths = {item["path"] for item in data["files"]}
        artifact_paths = {item["path"] for item in data["artifacts"]}

        assert "src/app.py" in file_paths
        assert "docs/ADR-001-browser.md" in file_paths
        assert "node_modules/ignored.js" not in file_paths
        assert "docs/ADR-001-browser.md" in artifact_paths

    def test_project_browser_watch_stream_reports_workspace_changes(self, client):
        project = client.post("/api/projects", json={"name": "BrowserWatch"}).json()
        workspace = Path(project["workspace_path"])
        (workspace / "docs").mkdir()
        watched = workspace / "docs" / "PRD.md"
        watched.write_text("# Initial\n", encoding="utf-8")

        def mutate_file():
            time.sleep(1.0)
            watched.write_text("# Updated\n", encoding="utf-8")

        worker = threading.Thread(target=mutate_file, daemon=True)
        worker.start()

        with client.stream("GET", f"/api/projects/{project['id']}/browser/watch?poll_interval=0.5&max_events=2") as response:
            assert response.status_code == 200
            payloads = [json.loads(line) for line in response.iter_lines() if line]

        worker.join(timeout=3.0)
        assert len(payloads) >= 2
        assert payloads[0]["type"] == "ready"
        assert payloads[0]["changed"] is False
        assert payloads[1]["type"] == "refresh_needed"
        assert payloads[1]["changed"] is True
        assert "docs/PRD.md" in payloads[1]["changed_paths"]

    def test_chat_processes_are_projected_by_backend(self, client, monkeypatch):
        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime

        monkeypatch.setattr(api_mod, "_pid_is_alive", lambda pid: int(pid) == 12345)
        project = client.post("/api/projects", json={"name": "Process Backend Test"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                run_kind="chat_turn",
                status="running",
                title="Background implementation",
                user_request="Implement the feature",
                summary="Working in the background",
                target_agent_name="Developer",
            )
            inline_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                client_turn_id="delegate-inline",
                run_kind="project_single_agent",
                status="running",
                title="Inline tester",
            )
            db.add_all([run, inline_run])
            db.flush()
            run_id = run.id
            inline_run_id = inline_run.id
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest", "cwd": "/tmp/work"}),
                        "status": "running",
                        "result": "collecting\nrunning tests",
                        "pid": 12345,
                        "run_id": run_id,
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/chatrooms/{chatroom_id}/processes")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == f"project:{project['id']}"
        chat_node = data["children"][0]
        assert chat_node["id"] == f"chat:{chatroom_id}"
        ids = {item["id"] for item in chat_node["children"]}
        task = next(item for item in chat_node["children"] if item["kind"] == "task")
        shell = next(item for item in task["children"] if item["kind"] == "command")
        assert shell["label"].startswith("python -m pytest")
        assert shell["pid"] == 12345
        assert shell["parent_id"] == f"task-run:{run_id}"
        assert "running tests" in shell["output"]
        assert task["label"] == "Background implementation"
        assert f"task-run:{run_id}" in ids
        assert f"task-run:{inline_run_id}" not in ids

    def test_chat_processes_surface_active_consult_subagent(self, client):
        import models.database as db_mod

        project = client.post("/api/projects", json={"name": "Consult Process Tree", "agent_names": ["analyst"]}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                run_kind="project_single_agent",
                status="running",
                title="Investigate risk",
                user_request="Check with analyst",
                summary="Consult in progress",
                client_turn_id="turn-consult-process-tree",
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            run_id = run.id
            db.add(
                db_mod.TaskRunEvent(
                    task_run_id=run.id,
                    event_index=1,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "consult-analyst-process-1",
                            "position": 0,
                            "requested_name": "analyst",
                            "agent_id": "analyst",
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "consult",
                            "wait_for_step_id": None,
                            "attached_to_step_id": None,
                            "source": "consult_agent",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/chatrooms/{chatroom_id}/processes")
        assert response.status_code == 200
        data = response.json()
        chat_node = data["children"][0]
        task = next(item for item in chat_node["children"] if item["id"] == f"task-run:{run_id}")
        subagent = next(item for item in task["children"] if item["kind"] == "subagent")
        assert subagent["label"] == "analyst (consult)"
        assert subagent["parent_id"] == f"task-run:{run_id}"
        assert subagent["status"] == "running"
        assert subagent["metadata"]["step_id"] == "consult-analyst-process-1"
        assert subagent["metadata"]["dispatch_kind"] == "consult"
        assert subagent["metadata"]["control_state"] == "await_completion"
        assert subagent["metadata"]["available_actions"] == ["wait", "cancel"]

    def test_chat_processes_keep_terminated_shell_cards(self, client):
        import models.database as db_mod
        from datetime import datetime

        project = client.post("/api/projects", json={"name": "Stale Process Test"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest"}),
                        "status": "running",
                        "result": "old output",
                        "pid": 99999999,
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/chatrooms/{chatroom_id}/processes")

        assert response.status_code == 200
        data = response.json()
        assert data["kind"] == "project"
        assert data["children"][0]["kind"] == "chat"
        entries = data["children"][0]["children"]
        assert len(entries) == 1
        assert entries[0]["kind"] == "command"
        assert entries[0]["status"] == "terminated"
        assert entries[0]["output"] == "old output"

    def test_chat_processes_ignore_bad_tracked_shell_state_file(self, tmp_path):
        from fastapi.testclient import TestClient

        app = _make_app(tmp_path)
        client = TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"})

        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime

        project = client.post("/api/projects", json={"name": "Bad Tracked State Test"}).json()
        chatroom_id = project["chatroom_id"]
        state_dir = api_mod.run_shell_process_state_dir()
        (state_dir / "bad-token.json").write_text("", encoding="utf-8")

        db = db_mod.SessionLocal()
        try:
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest"}),
                        "status": "running",
                        "result": "runtime card fallback output",
                        "tracked_process": {"token": "bad-token"},
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/chatrooms/{chatroom_id}/processes")

        assert response.status_code == 200
        entries = response.json()["children"][0]["children"]
        assert len(entries) == 1
        assert entries[0]["status"] == "terminated"
        assert entries[0]["output"] == "runtime card fallback output"

    def test_chat_processes_sort_running_before_terminated_shell_cards(self, client, monkeypatch):
        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime, timedelta

        monkeypatch.setattr(api_mod, "_pid_is_alive", lambda pid: int(pid) == 12345)
        project = client.post("/api/projects", json={"name": "Process Sort Test"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            for offset, command, pid in [
                (0, "old finished command", 99999999),
                (1, "active command", 12345),
            ]:
                db.add(db_mod.Message(
                    chatroom_id=chatroom_id,
                    content="runtime_card",
                    message_type="runtime_card",
                    metadata_json=json.dumps({
                        "card": {
                            "type": "tool_call",
                            "tool": "run_shell",
                            "arguments": json.dumps({"command": command}),
                            "status": "running",
                            "result": command,
                            "pid": pid,
                        }
                    }),
                    created_at=datetime.now() + timedelta(seconds=offset),
                ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/chatrooms/{chatroom_id}/processes")

        assert response.status_code == 200
        entries = response.json()["children"][0]["children"]
        assert [entry["status"] for entry in entries] == ["running", "terminated"]
        assert entries[0]["label"] == "active command"
        assert entries[1]["label"] == "old finished command"

    def test_chat_processes_dedupe_shell_progress_by_tracked_token(self, tmp_path, monkeypatch):
        _make_app(tmp_path)

        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime, timedelta

        monkeypatch.setattr(api_mod, "_running_shell_card_is_active", lambda card, observed_at=None: True)
        tracked = {"token": "same-process-token", "pid": 12345}

        db = db_mod.SessionLocal()
        try:
            project = db_mod.Project(name="Process Dedupe Test", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = db_mod.Chatroom(
                project_id=project.id,
                title="Process Dedupe Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            chatroom_id = chatroom.id

            for offset, output in enumerate(("line 1", "line 2")):
                db.add(db_mod.Message(
                    chatroom_id=chatroom_id,
                    content="runtime_card",
                    message_type="runtime_card",
                    metadata_json=json.dumps({
                        "card": {
                            "type": "tool_call",
                            "tool": "run_shell",
                            "arguments": json.dumps({"command": "python -m pytest"}),
                            "status": "running",
                            "result": output,
                            "pid": 12345,
                            "tracked_process": tracked,
                        }
                    }),
                    created_at=datetime.now() + timedelta(seconds=offset),
                ))
            db.commit()
            data = [entry.model_dump() for entry in api_mod._build_chat_process_entries(db, chatroom_id)]
        finally:
            db.close()

        assert len(data) == 1
        assert data[0]["id"] == "shell:tracked:same-process-token"
        assert data[0]["output"] == "line 2"

    def test_chat_processes_reads_latest_tracked_shell_tail(self, tmp_path, monkeypatch):
        _make_app(tmp_path)

        import models.database as db_mod
        import routes.api as api_mod
        from services.run_shell_processes import create_tracked_run_shell_handle
        from datetime import datetime

        tracked = create_tracked_run_shell_handle(
            command="python -m pytest backend/tests -q",
            cwd=str(tmp_path),
            timeout_seconds=60,
        )
        record = api_mod.load_tracked_run_shell_handle(tracked)
        assert record is not None
        state_path = Path(record["state_path"])
        record["pid"] = 97008
        record["status"] = "running"
        state_path.write_text(json.dumps(record), encoding="utf-8")
        Path(record["log_path"]).write_text("old line\nlatest pytest progress\n", encoding="utf-8")

        monkeypatch.setattr(api_mod, "tracked_run_shell_is_active", lambda record: True)

        db = db_mod.SessionLocal()
        try:
            project = db_mod.Project(name="Tracked Tail Test", status="active")
            db.add(project)
            db.flush()
            chatroom = db_mod.Chatroom(project_id=project.id, title="Tracked Tail Chat")
            db.add(chatroom)
            db.flush()
            db.add(db_mod.Message(
                chatroom_id=chatroom.id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest backend/tests -q"}),
                        "status": "running",
                        "result": "stale runtime card output",
                        "pid": 97008,
                        "tracked_process": tracked,
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
            data = [entry.model_dump() for entry in api_mod._build_chat_process_entries(db, chatroom.id)]
        finally:
            db.close()

        assert len(data) == 1
        assert data[0]["pid"] == 97008
        assert "latest pytest progress" in data[0]["output"]
        assert "stale runtime card output" not in data[0]["output"]

    def test_runtime_cards_reconcile_finished_tracked_run_shell(self, tmp_path, client):
        import models.database as db_mod
        import routes.api as api_mod
        from services.run_shell_processes import create_tracked_run_shell_handle
        from datetime import datetime

        project = client.post("/api/projects", json={"name": "Runtime Card Reconcile"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                run_kind="project_single_agent",
                status="running",
                title="Tracked shell task",
                user_request="Run command",
                target_agent_name="tester",
            )
            db.add(task_run)
            db.flush()
            handle = create_tracked_run_shell_handle(
                command="python -m pytest backend/tests -q",
                cwd=str(tmp_path),
                timeout_seconds=60,
                chatroom_id=chatroom_id,
                project_id=project["id"],
                task_run_id=task_run.id,
                client_turn_id="turn-reconcile",
                tool_call_id="call_reconcile",
                turn=1,
                agent_name="tester",
            )
            record = api_mod.load_tracked_run_shell_handle(handle)
            assert record is not None
            Path(record["log_path"]).write_text("tests passed\n", encoding="utf-8")
            Path(record["exit_path"]).write_text(
                json.dumps({"exit_code": 0, "finished_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
            record.update({"status": "completed", "exit_code": 0, "finished_at": datetime.now().isoformat()})
            Path(record["state_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest backend/tests -q"}),
                        "status": "running",
                        "success": None,
                        "result": "stale running output",
                        "pid": 97008,
                        "tracked_process": handle,
                        "run_id": task_run.id,
                        "tool_call_id": "call_reconcile",
                    }
                }),
                created_at=datetime.now(),
            ))
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest backend/tests -q"}),
                        "status": "running",
                        "success": None,
                        "result": "duplicate running output",
                        "pid": 97008,
                        "tracked_process": handle,
                        "run_id": task_run.id,
                        "tool_call_id": "call_reconcile",
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        followup_calls = []

        def fake_spawn_followup(task_run_id, next_card):
            followup_calls.append({"task_run_id": task_run_id, "next_card": next_card})

        with patch.object(api_mod, "_spawn_tracked_run_shell_followup", side_effect=fake_spawn_followup):
            cards = client.get(f"/api/chatrooms/{chatroom_id}/runtime-cards").json()
        shell_card = next(card for card in cards if card.get("tool") == "run_shell")
        assert shell_card["status"] == "succeeded"
        assert shell_card["success"] is True
        assert "tests passed" in shell_card["result"]
        assert "stale running output" not in shell_card["result"]
        assert len(followup_calls) == 1
        assert followup_calls[0]["task_run_id"] == task_run_id
        assert followup_calls[0]["next_card"]["tool"] == "run_shell"
        assert followup_calls[0]["next_card"]["status"] == "succeeded"
        assert followup_calls[0]["next_card"]["success"] is True
        assert "tests passed" in followup_calls[0]["next_card"]["result"]

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert detail["status"] == "running"
        assert detail["completed_at"] is None
        assert any(
            event["event_type"] == "tracked_run_shell_completed"
            for event in detail["events"]
        )

    def test_runtime_cards_do_not_refollow_terminal_tool_round_run_shell(self, tmp_path, client):
        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime
        from services.runner_lifecycle import record_tool_round
        from services.run_shell_processes import create_tracked_run_shell_handle
        from services.turn_state import build_tool_result_record

        project = client.post("/api/projects", json={"name": "Terminal Tool Round Reconcile"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                run_kind="project_single_agent",
                status="running",
                title="Tracked shell already consumed",
                user_request="Run command",
                target_agent_name="tester",
            )
            db.add(task_run)
            db.flush()
            handle = create_tracked_run_shell_handle(
                command="git status --short",
                cwd=str(tmp_path),
                timeout_seconds=60,
                chatroom_id=chatroom_id,
                project_id=project["id"],
                task_run_id=task_run.id,
                client_turn_id="turn-terminal-tool-round",
                tool_call_id="call_terminal_round",
                turn=1,
                agent_name="tester",
            )
            record = api_mod.load_tracked_run_shell_handle(handle)
            assert record is not None
            Path(record["log_path"]).write_text("clean\n", encoding="utf-8")
            Path(record["exit_path"]).write_text(
                json.dumps({"exit_code": 0, "finished_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
            record.update({"status": "completed", "exit_code": 0, "finished_at": datetime.now().isoformat()})
            Path(record["state_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            tool_result = build_tool_result_record(
                tool_call_id="call_terminal_round",
                tool_name="run_shell",
                arguments=json.dumps({"command": "git status --short"}),
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "run_shell",
                    "success": True,
                    "status": "succeeded",
                    "blocked": False,
                    "result": "clean\n",
                    "metadata": {"tracked_process": handle},
                },
                success=True,
            )
            record_tool_round(
                db,
                task_run,
                agent_name="tester",
                turn=1,
                tool_names=["run_shell"],
                tool_results=[tool_result],
                summary="tester completed a tool round.",
            )
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "git status --short"}),
                        "status": "running",
                        "success": None,
                        "result": "stale running output",
                        "pid": 97009,
                        "tracked_process": handle,
                        "run_id": task_run.id,
                        "tool_call_id": "call_terminal_round",
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        followup_calls = []

        def fake_spawn_followup(task_run_id, next_card):
            followup_calls.append({"task_run_id": task_run_id, "next_card": next_card})

        with patch.object(api_mod, "_spawn_tracked_run_shell_followup", side_effect=fake_spawn_followup):
            cards = client.get(f"/api/chatrooms/{chatroom_id}/runtime-cards").json()
        shell_card = next(card for card in cards if card.get("tool") == "run_shell")
        assert shell_card["status"] == "succeeded"
        assert shell_card["success"] is True
        assert "clean" in shell_card["result"]
        assert followup_calls == []

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert not any(
            event["event_type"] == "tracked_run_shell_completed"
            and event["payload"].get("tool_call_id") == "call_terminal_round"
            for event in detail["events"]
        )

    def test_runtime_cards_do_not_refollow_approval_continued_run_shell(self, tmp_path, client):
        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime
        from services.run_shell_processes import create_tracked_run_shell_handle

        project = client.post("/api/projects", json={"name": "Approval Continued Shell"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                client_turn_id="turn-approval-continued",
                run_kind="project_single_agent",
                status="running",
                title="Approval continued shell",
                user_request="Run approved shell",
                target_agent_name="Tester",
            )
            db.add(task_run)
            db.flush()
            handle = create_tracked_run_shell_handle(
                command="python -m pytest backend/tests -q",
                cwd=str(tmp_path),
                timeout_seconds=60,
                chatroom_id=chatroom_id,
                project_id=project["id"],
                task_run_id=task_run.id,
                client_turn_id="turn-approval-continued",
                tool_call_id="call_approved_continue",
                turn=1,
                agent_name="tester",
            )
            record = api_mod.load_tracked_run_shell_handle(handle)
            assert record is not None
            Path(record["log_path"]).write_text("tests failed\n", encoding="utf-8")
            Path(record["exit_path"]).write_text(
                json.dumps({"exit_code": 1, "finished_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
            record.update({"status": "failed", "exit_code": 1, "finished_at": datetime.now().isoformat()})
            Path(record["state_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            api_mod.append_task_event(
                db,
                task_run,
                "run_shell_continuation_claimed",
                agent_name="Tester",
                payload={
                    "queue_item_id": 123,
                    "tool_name": "run_shell",
                    "tool_call_id": "call_approved_continue",
                    "action_taken": "run_shell_continued_after_approval",
                    "status": "completed",
                    "tool_status": "failed",
                    "tracked_process": {"token": "different-token", "tool_call_id": "call_approved_continue"},
                },
            )
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest backend/tests -q"}),
                        "status": "running",
                        "success": None,
                        "result": "stale running output",
                        "tracked_process": handle,
                        "run_id": task_run.id,
                        "tool_call_id": "call_approved_continue",
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        followup_calls = []

        def fake_spawn_followup(task_run_id, next_card):
            followup_calls.append({"task_run_id": task_run_id, "next_card": next_card})

        with patch.object(api_mod, "_spawn_tracked_run_shell_followup", side_effect=fake_spawn_followup):
            cards = client.get(f"/api/chatrooms/{chatroom_id}/runtime-cards").json()
        shell_card = next(card for card in cards if card.get("tool") == "run_shell")
        assert shell_card["status"] == "failed"
        assert shell_card["success"] is False
        assert followup_calls == []

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert not any(
            event["event_type"] == "tracked_run_shell_completed"
            and event["payload"].get("tool_call_id") == "call_approved_continue"
            for event in detail["events"]
        )

    def test_runtime_cards_refollow_approval_continued_background_running_run_shell(self, tmp_path, client):
        import models.database as db_mod
        import routes.api as api_mod
        from datetime import datetime
        from services.run_shell_processes import create_tracked_run_shell_handle

        project = client.post("/api/projects", json={"name": "Approval Background Continued Shell"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                client_turn_id="turn-approval-background-continued",
                run_kind="project_single_agent",
                status="running",
                title="Approval continued shell to background",
                user_request="Run approved shell to background",
                target_agent_name="Tester",
            )
            db.add(task_run)
            db.flush()
            handle = create_tracked_run_shell_handle(
                command="python -m pytest backend/tests -q",
                cwd=str(tmp_path),
                timeout_seconds=60,
                chatroom_id=chatroom_id,
                project_id=project["id"],
                task_run_id=task_run.id,
                client_turn_id="turn-approval-background-continued",
                tool_call_id="call_approved_background",
                turn=1,
                agent_name="tester",
            )
            record = api_mod.load_tracked_run_shell_handle(handle)
            assert record is not None
            Path(record["log_path"]).write_text("tests failed\n", encoding="utf-8")
            Path(record["exit_path"]).write_text(
                json.dumps({"exit_code": 1, "finished_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
            record.update({"status": "failed", "exit_code": 1, "finished_at": datetime.now().isoformat()})
            Path(record["state_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            api_mod.append_task_event(
                db,
                task_run,
                "run_shell_continuation_claimed",
                agent_name="Tester",
                payload={
                    "queue_item_id": 124,
                    "tool_name": "run_shell",
                    "tool_call_id": "call_approved_background",
                    "action_taken": "run_shell_continued_after_approval",
                    "status": "completed",
                    "tool_status": "background_running",
                    "tool_success": False,
                    "tracked_process": {"token": "different-token", "tool_call_id": "call_approved_background"},
                },
            )
            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest backend/tests -q"}),
                        "status": "running",
                        "success": None,
                        "result": "stale running output",
                        "tracked_process": handle,
                        "run_id": task_run.id,
                        "tool_call_id": "call_approved_background",
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        followup_calls = []

        def fake_spawn_followup(task_run_id, next_card):
            followup_calls.append({"task_run_id": task_run_id, "next_card": next_card})

        with patch.object(api_mod, "_spawn_tracked_run_shell_followup", side_effect=fake_spawn_followup):
            cards = client.get(f"/api/chatrooms/{chatroom_id}/runtime-cards").json()
        shell_card = next(card for card in cards if card.get("tool") == "run_shell")
        assert shell_card["status"] == "failed"
        assert shell_card["success"] is False
        assert len(followup_calls) == 1
        assert followup_calls[0]["task_run_id"] == task_run_id
        assert followup_calls[0]["next_card"]["tool_call_id"] == "call_approved_background"

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert any(
            event["event_type"] == "tracked_run_shell_completed"
            and event["payload"].get("tool_call_id") == "call_approved_background"
            for event in detail["events"]
        )

    def test_tracked_run_shell_followup_calls_agent_with_result_context(self, tmp_path, client):
        import models.database as db_mod
        import routes.api as api_mod

        project = client.post("/api/projects", json={"name": "Tracked Shell Followup"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                client_turn_id="turn-followup",
                run_kind="project_single_agent",
                status="running",
                title="Tracked shell followup",
                user_request="@tester summarize results",
                target_agent_name="Tester",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)
            task_run_id = task_run.id
        finally:
            db.close()

        followup_calls = []

        async def fake_trigger_agent_response(
            chatroom_id,
            user_message,
            client_turn_id=None,
            task_run_id=None,
            extra_context="",
            checkpoint_snapshot=None,
        ):
            followup_calls.append(
                {
                    "chatroom_id": chatroom_id,
                    "user_message": user_message,
                    "client_turn_id": client_turn_id,
                    "task_run_id": task_run_id,
                    "extra_context": extra_context,
                    "checkpoint_snapshot": checkpoint_snapshot,
                }
            )
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": task_run_id}

        next_card = {
            "type": "tool_call",
            "agent": "Tester",
            "tool": "run_shell",
            "arguments": json.dumps({"command": "pytest"}),
            "status": "failed",
            "success": False,
            "result": "[Run Shell] Error:\n2 failed, 10 passed",
            "tool_call_id": "call_followup",
            "turn": 1,
            "tracked_process": {"token": "followup-token", "tool_call_id": "call_followup"},
        }
        with patch.object(api_mod, "trigger_agent_response", side_effect=fake_trigger_agent_response):
            asyncio.run(api_mod._continue_agent_after_tracked_run_shell_async(task_run_id, next_card))

        assert len(followup_calls) == 1
        assert followup_calls[0]["chatroom_id"] == chatroom_id
        assert followup_calls[0]["user_message"] == "@tester summarize results"
        assert followup_calls[0]["client_turn_id"] == "turn-followup"
        assert followup_calls[0]["task_run_id"] == task_run_id
        assert "run_shell" in followup_calls[0]["extra_context"]
        assert "2 failed, 10 passed" in followup_calls[0]["extra_context"]

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert detail["status"] == "running"
        assert any(event["event_type"] == "tracked_run_shell_followup_queued" for event in detail["events"])

    def test_get_project_not_found(self, client):
        r = client.get("/api/projects/99999")
        assert r.status_code == 404

    def test_delete_project(self, client):
        r = client.post("/api/projects", json={
            "name": "DelTest", "agent_names": ["analyst"]
        })
        pid = r.json()["id"]
        r2 = client.delete(f"/api/projects/{pid}")
        assert r2.status_code == 200
        r3 = client.get(f"/api/projects/{pid}")
        assert r3.status_code == 404

    def test_delete_project_removes_task_run_approval_queue_items(self, client):
        project = client.post("/api/projects", json={"name": "Queue Cleanup Project"}).json()
        _, queue_item_id = _seed_task_run_with_queue_item(
            chatroom_id=project["chatroom_id"],
            project_id=project["id"],
        )

        deleted = client.delete(f"/api/projects/{project['id']}")
        assert deleted.status_code == 200
        assert _approval_queue_item_exists(queue_item_id) is False

    def test_delete_project_removes_project_subchats(self, client):
        project = client.post("/api/projects", json={"name": "Delete Project Check"}).json()
        subchat = client.post(f"/api/projects/{project['id']}/subchats", json={}).json()
        client.post(
            f"/api/chatrooms/{subchat['id']}/messages",
            json={"content": "project-only context"},
        )

        deleted = client.delete(f"/api/projects/{project['id']}")
        assert deleted.status_code == 200

        listed_chats = client.get("/api/chats").json()
        assert all(chat["id"] != subchat["id"] for chat in listed_chats)

        subchat_messages = client.get(f"/api/chatrooms/{subchat['id']}/messages")
        assert subchat_messages.status_code == 200
        assert subchat_messages.json() == []

    def test_rename_project(self, client):
        project = client.post("/api/projects", json={
            "name": "Before", "agent_names": ["analyst"]
        }).json()

        r = client.put(f"/api/projects/{project['id']}", json={"name": "After"})
        assert r.status_code == 200
        assert r.json()["name"] == "After"

        project_chat = client.get(f"/api/projects/{project['id']}/chat")
        assert project_chat.status_code == 200
        assert project_chat.json()["title"] == "After"


# ==================== 聊天 API ====================

class TestChatEndpoints:
    def test_list_chats_empty(self, client):
        r = client.get("/api/chats")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_standalone_chat(self, client):
        r = client.post("/api/chats", json={"title": "Inbox"})
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Inbox"
        assert data["session_type"] == "standalone"
        assert data["is_visible_in_chat_list"] is True

    def test_delete_standalone_chat(self, client):
        chat = client.post("/api/chats", json={"title": "Delete Me"}).json()
        client.post(f"/api/chatrooms/{chat['id']}/messages", json={"content": "bye"})

        r = client.delete(f"/api/chats/{chat['id']}")
        assert r.status_code == 200
        assert r.json()["message"] == "Chat deleted successfully"

        listed = client.get("/api/chats")
        assert listed.status_code == 200
        assert all(row["id"] != chat["id"] for row in listed.json())

    def test_delete_standalone_chat_removes_task_run_approval_queue_items(self, client):
        chat = client.post("/api/chats", json={"title": "Delete Queue Chat"}).json()
        _, queue_item_id = _seed_task_run_with_queue_item(chatroom_id=chat["id"])

        deleted = client.delete(f"/api/chats/{chat['id']}")
        assert deleted.status_code == 200
        assert _approval_queue_item_exists(queue_item_id) is False

    def test_delete_standalone_chat_detaches_project_lineage(self, client):
        seed_chat = client.post("/api/chats", json={"title": "Seed Chat"}).json()
        created_project = client.post(
            "/api/projects/from-chat",
            json={
                "source_chatroom_id": seed_chat["id"],
                "name": "Detached Project",
                "description": "created from standalone chat",
            },
        ).json()

        deleted = client.delete(f"/api/chats/{seed_chat['id']}")
        assert deleted.status_code == 200

        project = client.get(f"/api/projects/{created_project['id']}")
        assert project.status_code == 200
        assert project.json()["created_from_chatroom_id"] is None

    def test_rename_standalone_chat(self, client):
        chat = client.post("/api/chats", json={"title": "Before"}).json()

        r = client.put(f"/api/chats/{chat['id']}", json={"title": "After"})
        assert r.status_code == 200
        assert r.json()["title"] == "After"

    def test_delete_project_chat_rejected(self, client):
        project = client.post("/api/projects", json={
            "name": "Protected Project",
            "agent_names": ["analyst"],
        }).json()

        r = client.delete(f"/api/chats/{project['default_chatroom_id']}")
        assert r.status_code == 400

    def test_send_message(self, client):
        r = client.post("/api/projects", json={
            "name": "ChatTest", "agent_names": ["analyst"]
        })
        cid = r.json()["chatroom_id"]
        r2 = client.post(
            f"/api/chatrooms/{cid}/messages",
            json={"content": "Hello!", "client_turn_id": "turn-sync-1"},
        )
        assert r2.status_code == 200
        assert r2.json()["client_turn_id"] == "turn-sync-1"

        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(
            message.get("client_turn_id") == "turn-sync-1" and not message.get("agent_name")
            for message in messages
        )

    def test_send_message_creates_task_run_ledger(self, client):
        r = client.post("/api/projects", json={
            "name": "TaskRun Sync", "agent_names": ["analyst"]
        })
        cid = r.json()["chatroom_id"]
        turn_id = "turn-task-run-sync"

        with patch("routes.websocket.websocket_manager.broadcast_to_topic", new=AsyncMock()) as mocked_broadcast:
            response = client.post(
                f"/api/chatrooms/{cid}/messages",
                json={"content": "Please inspect the architecture", "client_turn_id": turn_id},
            )

        assert response.status_code == 200

        task_run_pushes = [
            call.args[0]
            for call in mocked_broadcast.await_args_list
            if call.args and isinstance(call.args[0], dict) and call.args[0].get("type") == "monitor_task_run"
        ]
        assert task_run_pushes, "expected direct monitor_task_run websocket events"
        latest_push = task_run_pushes[-1]["payload"]
        assert latest_push["change_type"] == "upsert"
        assert latest_push["entry"]["client_turn_id"] == turn_id
        assert latest_push["entry"]["chat_title"]
        assert latest_push["detail"]["client_turn_id"] == turn_id

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()

        assert len(runs) == 1
        run = runs[0]
        assert run["run_kind"] == "project_single_agent"
        assert run["status"] == "completed"
        assert (run["target_agent_name"] or "").lower() == "analyst"
        assert run["summary"] == "Mocked agent response."
        assert run["event_count"] >= 4

        detail = client.get(f"/api/task-runs/{run['id']}").json()
        assert latest_push["entry"]["id"] == run["id"]
        assert latest_push["entry"]["checkpoint_snapshot"] == detail["checkpoint_snapshot"]
        assert latest_push["detail"] == detail
        event_types = [event["event_type"] for event in detail["events"]]
        mode_event = next(event for event in detail["events"] if event["event_type"] == "runtime_mode_selected")

        assert event_types[0] == "user_message_saved"
        assert "runtime_mode_selected" in event_types
        assert "agent_turn_started" in event_types
        assert event_types[-1] == "agent_turn_completed"
        assert mode_event["payload"]["runner_policy"]["mode"] == "project_single_agent"
        assert mode_event["payload"]["runner_policy"]["stage_count"] == 1
        assert (mode_event["payload"]["runner_policy"]["stages"][0]["agent_name"] or "").lower() == "analyst"
        assert mode_event["payload"]["runner_policy"]["metadata"]["project_bound"] is True
        assert mode_event["payload"]["runner_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1
        assert mode_event["payload"]["runner_policy"]["stages"][0]["metadata"]["tool_policy_summary"]["tool_count"] >= 1
        assert any(
            policy["name"] == "execute_code"
            for policy in mode_event["payload"]["runner_policy"]["metadata"]["tool_policies"]
        )

    def test_delegated_tester_valid_test_result_stops_before_second_turn(self, client):
        import llm.client as llm_mod
        import models.database as db_mod
        import routes.api as api_routes

        async def first_turn_returns_pytest_failure(messages, tools=None):
            return {
                "content": "Running backend tests.",
                "tool_calls": [
                    {
                        "id": "call_pytest_once",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps(
                                {
                                    "command": "python -m pytest backend/tests -q --tb=short --disable-warnings -r fE",
                                    "cwd": ".",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_with_tools = AsyncMock(side_effect=first_turn_returns_pytest_failure)
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        project = client.post(
            "/api/projects",
            json={"name": "Delegated Tester Contract", "agent_names": ["valet", "tester"]},
        ).json()
        cid = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            parent_run = db_mod.TaskRun(
                chatroom_id=cid,
                project_id=project["id"],
                client_turn_id="parent-test-contract",
                run_kind="project_single_agent",
                status="running",
                title="Test project",
                user_request="test the project",
                initiator="user",
                target_agent_name="Valet",
            )
            child_run = db_mod.TaskRun(
                chatroom_id=cid,
                project_id=project["id"],
                client_turn_id="delegate-test-contract",
                run_kind="project_single_agent",
                status="running",
                title="@tester Test project",
                user_request="@tester Test project",
                initiator="user",
                target_agent_name="Tester",
            )
            db.add_all([parent_run, child_run])
            db.commit()
            db.refresh(parent_run)
            db.refresh(child_run)
            api_routes.append_task_event(
                db,
                parent_run,
                "delegated_task_dispatched",
                agent_name="Valet",
                payload={
                    "task_id": "test-contract",
                    "task_title": "Test project",
                    "task_description": "Run tests and report results.",
                    "from_agent": "Valet",
                    "target_agent_name": "Tester",
                    "child_client_turn_id": "delegate-test-contract",
                    "required_outputs": ["test_report"],
                },
            )
            db.add(
                db_mod.Message(
                    chatroom_id=cid,
                    agent_id=None,
                    content="@tester Test project",
                    message_type="text",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "delegate-test-contract",
                            "parent_task_run_id": parent_run.id,
                            "delegated_task": {
                                "task_id": "test-contract",
                                "task_title": "Test project",
                                "task_description": "Run tests and report results.",
                                "delegator": "Valet",
                                "target_agent_name": "Tester",
                                "parent_task_run_id": parent_run.id,
                                "parent_client_turn_id": "parent-test-contract",
                                "required_outputs": ["test_report"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
            child_run_id = child_run.id
            parent_run_id = parent_run.id
        finally:
            db.close()

        async def fake_execute(tool_name, **kwargs):
            assert tool_name == "run_shell"
            return {
                "__catown_tool_result__": True,
                "tool_name": "run_shell",
                "success": False,
                "status": "failed",
                "blocked": False,
                "result": (
                    "...............................................................F........ [  9%]\n"
                    "........................................................................ [100%]\n"
                    "=================================== FAILURES ===================================\n"
                    "backend/tests/test_api_routes.py:537: in test_example\n"
                    "    assert approved[\"status\"] == \"approved\"\n"
                    "E   KeyError: 'status'\n"
                    "=========================== short test summary info ============================\n"
                    "FAILED backend/tests/test_api_routes.py::test_example\n"
                    "5 failed, 764 passed in 305.94s"
                ),
            }

        parent_followups = []

        async def fake_parent_followup(chatroom_id, user_message, client_turn_id=None, task_run_id=None, extra_context="", checkpoint_snapshot=None):
            parent_followups.append(
                {
                    "chatroom_id": chatroom_id,
                    "user_message": user_message,
                    "client_turn_id": client_turn_id,
                    "task_run_id": task_run_id,
                    "extra_context": extra_context,
                }
            )
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": task_run_id}

        original_trigger_agent_response = api_routes.trigger_agent_response

        async def wrapped_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, task_run_id=None, extra_context="", checkpoint_snapshot=None):
            if task_run_id == child_run_id:
                return await original_trigger_agent_response(
                    chatroom_id,
                    user_message,
                    client_turn_id=client_turn_id,
                    task_run_id=task_run_id,
                    extra_context=extra_context,
                    checkpoint_snapshot=checkpoint_snapshot,
                )
            return await fake_parent_followup(
                chatroom_id,
                user_message,
                client_turn_id=client_turn_id,
                task_run_id=task_run_id,
                extra_context=extra_context,
                checkpoint_snapshot=checkpoint_snapshot,
            )

        with patch("tools.tool_registry.execute", side_effect=fake_execute), patch.object(api_routes, "trigger_agent_response", side_effect=wrapped_trigger_agent_response):
            result = asyncio.run(
                api_routes.trigger_agent_response(
                    cid,
                    "@tester Test project",
                    client_turn_id="delegate-test-contract",
                    task_run_id=child_run_id,
                )
            )

        assert result["completed"] is True
        assert mock_llm.chat_with_tools.await_count == 1
        assert len(parent_followups) == 1
        assert parent_followups[0]["user_message"] == ""
        assert parent_followups[0]["client_turn_id"] == "parent-test-contract"
        assert parent_followups[0]["task_run_id"] == parent_run_id
        assert "agent handoff message from Tester to Valet" in parent_followups[0]["extra_context"]

        detail = client.get(f"/api/task-runs/{child_run_id}").json()
        assert detail["status"] == "completed"
        assert any(event["event_type"] == "test_report_produced" for event in detail["events"])
        assert not any(
            event["event_type"] == "tool_call_started"
            and event["payload"].get("tool_call_id") != "call_pytest_once"
            for event in detail["events"]
        )

    def test_delegated_tester_background_running_waits_for_tracked_shell_completion(self, client):
        import llm.client as llm_mod
        import models.database as db_mod
        import routes.api as api_routes

        async def first_turn_starts_pytest(messages, tools=None):
            return {
                "content": "Running backend tests.",
                "tool_calls": [
                    {
                        "id": "call_pytest_background",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps(
                                {
                                    "command": "python -m pytest backend/tests -q --tb=short --disable-warnings -r fE",
                                    "cwd": ".",
                                    "timeout_seconds": 60,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_with_tools = AsyncMock(side_effect=first_turn_starts_pytest)
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        project = client.post(
            "/api/projects",
            json={"name": "Delegated Tester Background Shell", "agent_names": ["valet", "tester"]},
        ).json()
        cid = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            parent_run = db_mod.TaskRun(
                chatroom_id=cid,
                project_id=project["id"],
                client_turn_id="parent-bg-contract",
                run_kind="project_single_agent",
                status="running",
                title="Test project",
                user_request="test the project",
                initiator="user",
                target_agent_name="Valet",
            )
            child_run = db_mod.TaskRun(
                chatroom_id=cid,
                project_id=project["id"],
                client_turn_id="delegate-bg-contract",
                run_kind="project_single_agent",
                status="running",
                title="@tester Test project",
                user_request="@tester Test project",
                initiator="user",
                target_agent_name="Tester",
            )
            db.add_all([parent_run, child_run])
            db.commit()
            db.refresh(parent_run)
            db.refresh(child_run)
            api_routes.append_task_event(
                db,
                parent_run,
                "delegated_task_dispatched",
                agent_name="Valet",
                payload={
                    "task_id": "test-background-contract",
                    "task_title": "Test project",
                    "task_description": "Run tests and report results.",
                    "from_agent": "Valet",
                    "target_agent_name": "Tester",
                    "child_client_turn_id": "delegate-bg-contract",
                    "required_outputs": ["test_report"],
                },
            )
            db.add(
                db_mod.Message(
                    chatroom_id=cid,
                    agent_id=None,
                    content="@tester Test project",
                    message_type="text",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "delegate-bg-contract",
                            "parent_task_run_id": parent_run.id,
                            "delegated_task": {
                                "task_id": "test-background-contract",
                                "task_title": "Test project",
                                "task_description": "Run tests and report results.",
                                "delegator": "Valet",
                                "target_agent_name": "Tester",
                                "parent_task_run_id": parent_run.id,
                                "parent_client_turn_id": "parent-bg-contract",
                                "required_outputs": ["test_report"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
            child_run_id = child_run.id
            parent_run_id = parent_run.id
        finally:
            db.close()

        async def fake_execute(tool_name, **kwargs):
            assert tool_name == "run_shell"
            return {
                "__catown_tool_result__": True,
                "tool_name": "run_shell",
                "success": False,
                "status": "background_running",
                "blocked": False,
                "result": "[Run Shell] Foreground wait elapsed after 60s. The process is still running in the background.",
                "metadata": {
                    "tracked_process": {
                        "token": "bg-shell-token",
                        "status": "running",
                        "task_run_id": child_run_id,
                        "client_turn_id": "delegate-bg-contract",
                        "tool_call_id": "call_pytest_background",
                    }
                },
            }

        parent_followups = []

        async def fake_parent_followup(chatroom_id, user_message, client_turn_id=None, task_run_id=None, extra_context="", checkpoint_snapshot=None):
            parent_followups.append(
                {
                    "chatroom_id": chatroom_id,
                    "user_message": user_message,
                    "client_turn_id": client_turn_id,
                    "task_run_id": task_run_id,
                    "extra_context": extra_context,
                }
            )
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": task_run_id}

        original_trigger_agent_response = api_routes.trigger_agent_response

        async def wrapped_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, task_run_id=None, extra_context="", checkpoint_snapshot=None):
            if task_run_id == child_run_id:
                return await original_trigger_agent_response(
                    chatroom_id,
                    user_message,
                    client_turn_id=client_turn_id,
                    task_run_id=task_run_id,
                    extra_context=extra_context,
                    checkpoint_snapshot=checkpoint_snapshot,
                )
            return await fake_parent_followup(
                chatroom_id,
                user_message,
                client_turn_id=client_turn_id,
                task_run_id=task_run_id,
                extra_context=extra_context,
                checkpoint_snapshot=checkpoint_snapshot,
            )

        spawned_background_watches = []

        def fake_spawn_background_watch(task_run_id, base_card):
            spawned_background_watches.append({"task_run_id": task_run_id, "base_card": base_card})

        with patch("tools.tool_registry.execute", side_effect=fake_execute), patch.object(api_routes, "trigger_agent_response", side_effect=wrapped_trigger_agent_response), patch.object(api_routes, "_spawn_background_tracked_run_shell_watch", side_effect=fake_spawn_background_watch):
            result = asyncio.run(
                api_routes.trigger_agent_response(
                    cid,
                    "@tester Test project",
                    client_turn_id="delegate-bg-contract",
                    task_run_id=child_run_id,
                )
            )

        assert result["completed"] is False
        assert result["awaiting_background_tool"] is True
        assert mock_llm.chat_with_tools.await_count == 1
        assert parent_followups == []
        assert len(spawned_background_watches) == 1
        assert spawned_background_watches[0]["task_run_id"] == child_run_id
        assert spawned_background_watches[0]["base_card"]["tool"] == "run_shell"
        assert spawned_background_watches[0]["base_card"]["tracked_process"]["token"] == "bg-shell-token"

        detail = client.get(f"/api/task-runs/{child_run_id}").json()
        assert detail["status"] == "running"
        assert any(
            event["event_type"] == "tool_round_recorded"
            and event["payload"].get("tool_status_counts", {}).get("background_running") == 1
            for event in detail["events"]
        )
        assert not any(event["event_type"] == "test_report_produced" for event in detail["events"])
        assert not any(
            event["event_type"] == "tool_call_started"
            and event["payload"].get("tool_call_id") != "call_pytest_background"
            for event in detail["events"]
        )

    def test_task_run_detail_reconciles_finished_tracked_run_shell(self, tmp_path, client):
        import models.database as db_mod
        import routes.api as api_mod
        from services.run_shell_processes import create_tracked_run_shell_handle
        from datetime import datetime

        project = client.post("/api/projects", json={"name": "Task Detail Reconcile"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                run_kind="project_single_agent",
                status="running",
                title="Tracked shell detail task",
                user_request="Run command",
                target_agent_name="tester",
            )
            db.add(task_run)
            db.flush()
            handle = create_tracked_run_shell_handle(
                command="python -m pytest backend/tests -q",
                cwd=str(tmp_path),
                timeout_seconds=60,
                chatroom_id=chatroom_id,
                project_id=project["id"],
                task_run_id=task_run.id,
                client_turn_id="turn-detail-reconcile",
                tool_call_id="call_detail_reconcile",
                turn=1,
                agent_name="tester",
            )
            record = api_mod.load_tracked_run_shell_handle(handle)
            assert record is not None
            Path(record["log_path"]).write_text("tests passed\n", encoding="utf-8")
            Path(record["exit_path"]).write_text(
                json.dumps({"exit_code": 0, "finished_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
            record.update({"status": "completed", "exit_code": 0, "finished_at": datetime.now().isoformat()})
            Path(record["state_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

            db.add(db_mod.Message(
                chatroom_id=chatroom_id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest backend/tests -q"}),
                        "status": "running",
                        "success": None,
                        "result": "stale running output",
                        "pid": 97008,
                        "tracked_process": handle,
                        "run_id": task_run.id,
                        "tool_call_id": "call_detail_reconcile",
                    }
                }),
                created_at=datetime.now(),
            ))
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        followup_calls = []

        def fake_spawn_followup(task_run_id, next_card):
            followup_calls.append({"task_run_id": task_run_id, "next_card": next_card})

        with patch.object(api_mod, "_spawn_tracked_run_shell_followup", side_effect=fake_spawn_followup):
            detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert detail["status"] == "running"
        assert any(event["event_type"] == "tracked_run_shell_completed" for event in detail["events"])
        assert len(followup_calls) == 1
        assert followup_calls[0]["task_run_id"] == task_run_id
        assert followup_calls[0]["next_card"]["status"] == "succeeded"

    def test_send_message_builds_blocking_and_sidecar_schedule(self, client):
        r = client.post("/api/projects", json={
            "name": "TaskRun Sync Sidecar", "agent_names": ["analyst", "developer", "tester", "release"]
        })
        cid = r.json()["chatroom_id"]
        turn_id = "turn-task-run-sync-sidecar"

        response = client.post(
            f"/api/chatrooms/{cid}/messages",
            json={"content": "@analyst @developer @tester @release inspect the runtime", "client_turn_id": turn_id},
        )

        assert response.status_code == 200

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()

        assert len(runs) == 1
        run = runs[0]
        assert run["run_kind"] == "multi_agent_orchestration"
        assert run["status"] == "completed"
        assert run["summary"] == "Mocked agent response."

        detail = client.get(f"/api/task-runs/{run['id']}").json()
        mode_event = next(event for event in detail["events"] if event["event_type"] == "runtime_mode_selected")
        schedule_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_plan_created")
        assert mode_event["payload"]["runner_policy"]["mode"] == "blocking_chain_with_sidecars"
        assert mode_event["payload"]["runner_policy"]["stage_count"] == 4
        assert mode_event["payload"]["runner_policy"]["metadata"]["sidecar_step_count"] == 1
        assert schedule_event["payload"]["mode"] == "blocking_chain_with_sidecars"
        assert schedule_event["payload"]["blocking_step_count"] == 3
        assert schedule_event["payload"]["sidecar_step_count"] == 1
        assert schedule_event["payload"]["runtime"]["ready_step_count"] == 1
        assert schedule_event["payload"]["runtime"]["waiting_step_count"] == 3
        assert schedule_event["payload"]["runner_policy"]["mode"] == "blocking_chain_with_sidecars"
        assert schedule_event["payload"]["runner_policy"]["metadata"]["sidecar_step_count"] == 1
        assert schedule_event["payload"]["runner_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1

        steps = {step["agent_type"]: step for step in schedule_event["payload"]["steps"]}
        policy_steps = {
            step["metadata"]["agent_type"]: step
            for step in schedule_event["payload"]["runner_policy"]["stages"]
        }
        assert steps["analyst"]["dispatch_kind"] == "blocking"
        assert steps["analyst"]["wait_for_step_id"] is None
        assert steps["developer"]["dispatch_kind"] == "blocking"
        assert steps["developer"]["wait_for_step_id"] == steps["analyst"]["step_id"]
        assert steps["tester"]["dispatch_kind"] == "sidecar"
        assert steps["tester"]["wait_for_step_id"] == steps["developer"]["step_id"]
        assert steps["tester"]["attached_to_step_id"] == steps["developer"]["step_id"]
        assert steps["release"]["dispatch_kind"] == "blocking"
        assert steps["release"]["wait_for_step_id"] == steps["developer"]["step_id"]
        assert policy_steps["tester"]["metadata"]["dispatch_kind"] == "sidecar"
        assert policy_steps["tester"]["metadata"]["attached_to_step_id"] == steps["developer"]["step_id"]
        assert policy_steps["tester"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1

        dispatch_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_dispatched"]
        dispatched_agent_types = [event["payload"]["agent_type"] for event in dispatch_events]
        assert dispatched_agent_types == ["analyst", "developer", "release", "tester"]
        assert dispatch_events[0]["payload"]["step_state"]["status"] == "running"

        resumed_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_resumed"]
        assert [event["payload"]["agent_type"] for event in resumed_events] == ["developer", "release", "tester"]

        completed_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_completed"]
        assert len(completed_events) == 4
        assert completed_events[-1]["payload"]["runtime"]["completed_step_count"] == 4
        assert completed_events[-1]["payload"]["runtime"]["waiting_step_count"] == 0

    def test_get_task_run_activity_projects_steps(self, client):
        r = client.post("/api/projects", json={
            "name": "Task Activity Projection", "agent_names": ["analyst", "developer"]
        })
        cid = r.json()["chatroom_id"]
        turn_id = "turn-task-activity-projection"

        response = client.post(
            f"/api/chatrooms/{cid}/messages",
            json={"content": "@analyst @developer inspect task activity", "client_turn_id": turn_id},
        )

        assert response.status_code == 200
        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()
        run = runs[0]

        activity = client.get(f"/api/task-runs/{run['id']}/activity").json()

        assert activity["task_run_id"] == run["id"]
        assert activity["timeline"]["task_run_id"] == run["id"]
        assert activity["timeline"]["version"] == activity["version"]
        assert activity["latest_event_index"] >= 1
        assert activity["current_step_id"]
        assert activity["current_step_id"] == activity["timeline"]["current_step_id"]
        assert len(activity["steps"]) >= 3
        assert [step["event_index"] for step in activity["steps"]] == [
            step["sequence"] for step in activity["timeline"]["steps"]
        ]
        assert any(step["event_type"] == "scheduler_plan_created" for step in activity["steps"])
        assert any(step["event_type"] == "agent_turn_completed" for step in activity["steps"])
        assert activity["steps"][-1]["state"] in {"done", "live", "error"}
        assert activity["summary"]
        assert activity["background"]["scheduler_runtime_summary"]
        assert activity["scheduler_runtime_summary"]
        live_steps = [step for step in activity["steps"] if step["state"] == "live"]
        assert len(live_steps) <= 1
        assert not [step for step in live_steps if step["event_type"].startswith("scheduler_step_")]
        detail = client.get(f"/api/task-runs/{run['id']}").json()
        assert any(event["event_type"] == "scheduler_step_dispatched" for event in detail["events"])

    def test_get_task_run_activity_surfaces_active_consult_handle(self, client):
        from models.database import SessionLocal, TaskRun, TaskRunEvent

        response = client.post("/api/projects", json={"name": "Consult Activity", "agent_names": ["analyst"]})
        assert response.status_code == 200
        chatroom_id = response.json()["chatroom_id"]

        db = SessionLocal()
        try:
            task_run = TaskRun(
                chatroom_id=chatroom_id,
                run_kind="multi_agent_orchestration",
                status="running",
                title="Consult foreground activity",
                user_request="Expose consult handle in activity.",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "consult-analyst-foreground",
                            "position": 0,
                            "requested_name": "analyst",
                            "agent_id": "analyst",
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "consult",
                            "wait_for_step_id": None,
                            "attached_to_step_id": None,
                            "source": "consult_agent",
                            "context": {
                                "requested_by": "boss",
                                "question_preview": "What is the main risk?",
                            },
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                )
            )
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        activity = client.get(f"/api/task-runs/{task_run_id}/activity")
        assert activity.status_code == 200
        payload = activity.json()

        handle = payload["active_subagent_handle"]
        assert handle["step_id"] == "consult-analyst-foreground"
        assert handle["dispatch_kind"] == "consult"
        assert handle["control_state"] == "await_completion"
        assert handle["available_actions"] == ["wait", "cancel"]
        assert handle["source"] == "consult_agent"

        consult_handle = payload["active_consult_handle"]
        assert consult_handle["step_id"] == "consult-analyst-foreground"
        assert consult_handle["dispatch_kind"] == "consult"
        assert consult_handle["source"] == "consult_agent"
        assert payload["background"]["active_consult_handle"]["step_id"] == "consult-analyst-foreground"
        assert payload["latest_agent_turn_preview"] is None

    def test_send_message_rebuilds_tool_loop_from_turn_state(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        seen_messages = []

        class DummyFunction:
            def __init__(self, name, arguments):
                self.name = name
                self.arguments = arguments

        class DummyToolCall:
            def __init__(self, call_id, name, arguments):
                self.id = call_id
                self.function = DummyFunction(name, arguments)

        async def scripted_chat_with_tools(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            call_no = len(seen_messages)
            if call_no == 1:
                return {
                    "content": "I will inspect the backend tree first.",
                    "tool_calls": [
                        DummyToolCall("call_1", "list_files", "{\"directory\": \"backend\", \"pattern\": \"*.py\", \"recursive\": false}")
                    ],
                }
            if call_no == 2:
                return {
                    "content": "Now I should open the API route file.",
                    "tool_calls": [
                        DummyToolCall("call_2", "read_file", "{\"file_path\": \"backend/routes/api.py\", \"encoding\": \"utf-8\"}")
                    ],
                }
            return {"content": "Done inspecting the backend.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.base_url = "http://localhost:9999/v1"
        mock_llm.model = "test-model"
        mock_llm.chat = AsyncMock(return_value="Mocked response.")
        mock_llm.chat_with_tools = AsyncMock(side_effect=scripted_chat_with_tools)
        llm_mod._llm_client = mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm

        r = client.post("/api/projects", json={"name": "Tool Loop Rebuild", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]

        response = client.post(
            f"/api/chatrooms/{cid}/messages",
            json={"content": "Inspect the backend implementation", "client_turn_id": "turn-rebuild-1"},
        )

        assert response.status_code == 200
        assert len(seen_messages) == 3

        second_call_messages = seen_messages[1]
        third_call_messages = seen_messages[2]

        assert any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in second_call_messages
        )
        assert any(
            message.get("role") == "tool" and message.get("name") == "read_file"
            for message in third_call_messages
        )
        assert not any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in third_call_messages
        )
        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(message.get("agent_name") == "analyst" for message in messages)

    def test_get_messages(self, client):
        r = client.post("/api/projects", json={
            "name": "GetMsg", "agent_names": ["analyst"]
        })
        cid = r.json()["chatroom_id"]
        client.post(f"/api/chatrooms/{cid}/messages", json={"content": "msg1"})
        client.post(f"/api/chatrooms/{cid}/messages", json={"content": "msg2"})
        r2 = client.get(f"/api/chatrooms/{cid}/messages")
        assert r2.status_code == 200
        assert len(r2.json()) >= 2
        assert "created_at" in r2.json()[0]

    def test_get_project_chat(self, client):
        project = client.post("/api/projects", json={
            "name": "ProjectChat",
            "agent_names": ["analyst"],
        }).json()
        r = client.get(f"/api/projects/{project['id']}/chat")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == project["default_chatroom_id"]
        assert data["session_type"] == "project-bound"

    def test_hidden_project_chat_not_listed_in_chats(self, client):
        project = client.post("/api/projects", json={
            "name": "HiddenChatProject",
            "agent_names": ["analyst"],
        }).json()
        chats = client.get("/api/chats").json()
        assert all(chat["id"] != project["default_chatroom_id"] for chat in chats)


# ==================== SSE 流式 ====================

class TestSSEStreaming:
    def test_stream_returns_sse(self, client):
        r = client.post("/api/projects", json={
            "name": "SSETest", "agent_names": ["analyst"]
        })
        cid = r.json()["chatroom_id"]
        r2 = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "Hello stream!"},
            headers={"Accept": "text/event-stream"}
        )
        assert r2.status_code == 200
        assert "text/event-stream" in r2.headers["content-type"]

    def test_stream_has_data_events(self, client):
        r = client.post("/api/projects", json={
            "name": "SSE2", "agent_names": ["analyst"]
        })
        cid = r.json()["chatroom_id"]
        r2 = client.post(f"/api/chatrooms/{cid}/messages/stream", json={"content": "test"})
        body = r2.text
        assert "data:" in body
        # 至少有 user_saved 事件
        assert "user_saved" in body

    def test_standalone_stream_has_done_event(self, client):
        r = client.post("/api/chats", json={"title": "Standalone SSE"})
        cid = r.json()["id"]
        r2 = client.post(f"/api/chatrooms/{cid}/messages/stream", json={"content": "hello standalone"})
        body = r2.text
        assert r2.status_code == 200
        assert "user_saved" in body
        assert '"type": "done"' in body or '"type":"done"' in body

    def test_standalone_stream_uses_mentioned_agent_name(self, client):
        r = client.post("/api/chats", json={"title": "Mention SSE"})
        cid = r.json()["id"]
        r2 = client.post(f"/api/chatrooms/{cid}/messages/stream", json={"content": "@analyst hello standalone"})
        body = r2.text

        assert r2.status_code == 200
        assert '"agent_name": "analyst"' in body or '"agent_name":"analyst"' in body

        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(message.get("agent_name") == "analyst" for message in messages)

    def test_standalone_stream_runtime_policy_tracks_mentioned_agent(self, client):
        r = client.post("/api/chats", json={"title": "Mention Policy SSE"})
        cid = r.json()["id"]
        turn_id = "turn-standalone-mentioned-policy"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst hello standalone", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()
        assert len(runs) == 1

        detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
        mode_event = next(event for event in detail["events"] if event["event_type"] == "runtime_mode_selected")
        assert mode_event["payload"]["runner_policy"]["mode"] == "standalone_assistant_stream"
        assert (mode_event["payload"]["runner_policy"]["stages"][0]["agent_name"] or "").lower() == "analyst"
        assert mode_event["payload"]["runner_policy"]["metadata"]["standalone"] is True
        assert mode_event["payload"]["runner_policy"]["metadata"]["tool_policy_summary"]["tool_count"] == 0

    def test_stream_records_user_and_target_agent_facts_before_llm(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def stream_without_request_sent(messages, tools=None):
            yield {"type": "content", "delta": "Hello!"}
            yield {"type": "done", "full_content": "Hello!", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = stream_without_request_sent
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/chats", json={"title": "Stream Facts"})
        cid = r.json()["id"]
        turn_id = "turn-stream-facts"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst inspect status", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert '"task_run_id"' in stream.text

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()
        assert len(runs) == 1

        detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
        event_types = [event["event_type"] for event in detail["events"]]
        user_event = next(event for event in detail["events"] if event["event_type"] == "user_message_saved")
        target_event = next(event for event in detail["events"] if event["event_type"] == "target_agent_selected")
        llm_event = next(event for event in detail["events"] if event["event_type"] == "llm_request_created")

        assert event_types.index("user_message_saved") < event_types.index("target_agent_selected")
        assert user_event["payload"]["content"] == "@analyst inspect status"
        assert user_event["payload"]["client_turn_id"] == turn_id
        assert target_event["payload"]["agent_name"] == "analyst"
        assert "model" in target_event["payload"]
        assert llm_event["payload"]["prompt_message_count"] >= 1
        assert "@analyst inspect status" in llm_event["payload"]["prompt_preview"]
        assert any(
            "@analyst inspect status" in str(message.get("content") or "")
            for message in llm_event["payload"]["prompt_messages"]
        )

        timeline = client.get(f"/api/task-runs/{runs[0]['id']}/timeline").json()
        llm_step = next(
            step
            for step in timeline["steps"]
            if any(event["event_type"] == "llm_request_created" for event in step.get("event_refs") or [])
        )
        assert "### Prompt Messages" in llm_step["detail_content"]
        assert "@analyst inspect status" in llm_step["detail_content"]

    def test_standalone_stream_persists_done_only_full_content(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes
        from agents.identity import DEFAULT_AGENT_TYPE

        async def done_only_stream(messages, tools=None):
            yield {
                "type": "done",
                "full_content": "Done-only standalone.",
                "tool_calls": None,
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
                "finish_reason": "stop",
                "timings": {"completed_ms": 17},
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = done_only_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/chats", json={"title": "Standalone Done Only"})
        cid = r.json()["id"]
        turn_id = "turn-standalone-done-only"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "hello standalone", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        body = stream.text
        assert '"type": "llm_call"' in body or '"type":"llm_call"' in body
        assert "system_prompt" not in body
        assert "prompt_messages" not in body
        assert "raw_response" not in body

        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(
            message.get("agent_name") == DEFAULT_AGENT_TYPE
            and message.get("content") == "Done-only standalone."
            and message.get("client_turn_id") == turn_id
            for message in messages
        )

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        llm_card = next(card for card in cards if card.get("type") == "llm_call")
        assert llm_card["response"] == "Done-only standalone."
        assert llm_card["client_turn_id"] == turn_id
        assert "system_prompt" not in llm_card
        assert "prompt_messages" not in llm_card
        assert "raw_response" not in llm_card
        assert llm_card["debug_payload_omitted"] is True

    def test_standalone_multi_mention_stream(self, client):
        r = client.post("/api/chats", json={"title": "Standalone Pipeline"})
        cid = r.json()["id"]
        r2 = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst @developer build hello"},
        )
        body = r2.text

        assert r2.status_code == 200
        assert "collab_start" in body
        assert '"agent": "analyst"' in body or '"agent":"analyst"' in body
        assert '"agent": "developer"' in body or '"agent":"developer"' in body

        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        agent_names = [message.get("agent_name") for message in messages if message.get("agent_name")]
        assert "analyst" in agent_names
        assert "developer" in agent_names

    def test_multi_agent_stream_creates_task_run_ledger(self, client):
        r = client.post("/api/projects", json={
            "name": "TaskRun Stream", "agent_names": ["analyst", "developer"]
        })
        cid = r.json()["chatroom_id"]
        turn_id = "turn-task-run-stream"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst @developer inspect the runtime", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert "collab_start" in stream.text

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()

        assert len(runs) == 1
        run = runs[0]
        assert run["run_kind"] == "multi_agent_orchestration_stream"
        assert run["status"] == "completed"

        detail = client.get(f"/api/task-runs/{run['id']}").json()
        mode_event = next(event for event in detail["events"] if event["event_type"] == "runtime_mode_selected")
        event_types = [event["event_type"] for event in detail["events"]]

        assert event_types[0] == "user_message_saved"
        assert "runtime_mode_selected" in event_types
        assert "orchestration_started" in event_types
        assert "scheduler_plan_created" in event_types
        assert event_types.count("scheduler_step_dispatched") == 2
        assert event_types.count("scheduler_step_completed") == 2
        assert event_types.count("scheduler_step_resumed") == 1
        assert event_types.count("agent_turn_started") == 2
        assert event_types.count("agent_turn_completed") == 2
        assert "handoff_created" in event_types

        assert mode_event["payload"]["runner_policy"]["mode"] == "linear_blocking_chain"
        assert mode_event["payload"]["runner_policy"]["stage_count"] == 2
        assert mode_event["payload"]["runner_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1
        schedule_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_plan_created")
        assert schedule_event["payload"]["mode"] == "linear_blocking_chain"
        assert schedule_event["payload"]["step_count"] == 2
        assert schedule_event["payload"]["runtime"]["ready_step_count"] == 1
        assert schedule_event["payload"]["runtime"]["waiting_step_count"] == 1
        assert schedule_event["payload"]["runner_policy"]["mode"] == "linear_blocking_chain"
        assert schedule_event["payload"]["runner_policy"]["stage_count"] == 2
        assert schedule_event["payload"]["runner_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1
        first_step, second_step = schedule_event["payload"]["steps"]
        assert first_step["dispatch_kind"] == "blocking"
        assert first_step["wait_for_step_id"] is None
        assert second_step["dispatch_kind"] == "blocking"
        assert second_step["wait_for_step_id"] == first_step["step_id"]
        assert schedule_event["payload"]["runner_policy"]["stages"][0]["metadata"]["tool_policy_summary"]["tool_count"] >= 1

        completed_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_completed"]
        assert completed_events[-1]["payload"]["runtime"]["completed_step_count"] == 2
        assert completed_events[-1]["payload"]["runtime"]["running_step_count"] == 0

        handoff_event = next(event for event in detail["events"] if event["event_type"] == "handoff_created")
        assert (handoff_event["payload"]["from_agent"] or "").lower() == "analyst"
        assert (handoff_event["payload"]["to_agent"] or "").lower() == "developer"
        assert handoff_event["payload"]["from_step_id"] == first_step["step_id"]
        assert handoff_event["payload"]["to_step_id"] == second_step["step_id"]
        assert handoff_event["payload"]["dispatch_kind"] == "blocking"

    def test_multi_agent_stream_builds_blocking_and_sidecar_schedule(self, client):
        r = client.post("/api/projects", json={
            "name": "TaskRun Stream Sidecar", "agent_names": ["analyst", "developer", "tester", "release"]
        })
        cid = r.json()["chatroom_id"]
        turn_id = "turn-task-run-stream-sidecar"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst @developer @tester @release inspect the runtime", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert "collab_start" in stream.text
        assert '"dispatch_kind": "sidecar"' in stream.text or '"dispatch_kind":"sidecar"' in stream.text

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()

        assert len(runs) == 1
        run = runs[0]
        assert run["run_kind"] == "multi_agent_orchestration_stream"
        assert run["status"] == "completed"
        assert run["summary"] == "Hello!"

        detail = client.get(f"/api/task-runs/{run['id']}").json()
        mode_event = next(event for event in detail["events"] if event["event_type"] == "runtime_mode_selected")
        schedule_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_plan_created")
        assert mode_event["payload"]["runner_policy"]["mode"] == "blocking_chain_with_sidecars"
        assert mode_event["payload"]["runner_policy"]["stage_count"] == 4
        assert mode_event["payload"]["runner_policy"]["metadata"]["sidecar_step_count"] == 1
        assert schedule_event["payload"]["mode"] == "blocking_chain_with_sidecars"
        assert schedule_event["payload"]["blocking_step_count"] == 3
        assert schedule_event["payload"]["sidecar_step_count"] == 1
        assert schedule_event["payload"]["runtime"]["ready_step_count"] == 1
        assert schedule_event["payload"]["runtime"]["waiting_step_count"] == 3
        assert schedule_event["payload"]["runner_policy"]["metadata"]["sidecar_step_count"] == 1
        assert schedule_event["payload"]["runner_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1

        steps = {step["agent_type"]: step for step in schedule_event["payload"]["steps"]}
        assert steps["tester"]["dispatch_kind"] == "sidecar"
        assert steps["tester"]["wait_for_step_id"] == steps["developer"]["step_id"]
        assert steps["tester"]["attached_to_step_id"] == steps["developer"]["step_id"]
        assert steps["release"]["dispatch_kind"] == "blocking"
        assert steps["release"]["wait_for_step_id"] == steps["developer"]["step_id"]

        dispatch_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_dispatched"]
        dispatched_agent_types = [event["payload"]["agent_type"] for event in dispatch_events]
        assert dispatched_agent_types == ["analyst", "developer", "release", "tester"]
        assert dispatch_events[0]["payload"]["step_state"]["status"] == "running"

        resumed_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_resumed"]
        assert [event["payload"]["agent_type"] for event in resumed_events] == ["developer", "release", "tester"]

        completed_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_completed"]
        assert len(completed_events) == 4
        assert completed_events[-1]["payload"]["runtime"]["completed_step_count"] == 4
        assert completed_events[-1]["payload"]["runtime"]["ready_step_count"] == 0

        handoffs_from_developer = [
            event for event in detail["events"]
            if event["event_type"] == "handoff_created" and (event["payload"]["from_agent"] or "").lower() == "developer"
        ]
        assert len(handoffs_from_developer) == 2
        handoffs_by_target = {
            (event["payload"]["to_agent"] or "").lower(): event["payload"]
            for event in handoffs_from_developer
        }
        assert handoffs_by_target["release"]["dispatch_kind"] == "blocking"
        assert handoffs_by_target["release"]["to_step_id"] == steps["release"]["step_id"]
        assert handoffs_by_target["tester"]["dispatch_kind"] == "sidecar"
        assert handoffs_by_target["tester"]["to_step_id"] == steps["tester"]["step_id"]
        assert handoffs_by_target["tester"]["attached_to_step_id"] == steps["developer"]["step_id"]

    def test_multi_agent_stream_can_disable_sidecars_via_orchestration_config(self, tmp_path):
        from fastapi.testclient import TestClient

        default_config_path = Path(__file__).resolve().parents[1] / "configs" / "agents.json"
        config_data = json.loads(default_config_path.read_text(encoding="utf-8"))
        config_data["orchestration"] = {"sidecar_agent_types": []}

        config_path = tmp_path / "agents.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        previous_config_file = os.environ.get("AGENT_CONFIG_FILE")
        try:
            os.environ["AGENT_CONFIG_FILE"] = str(config_path)
            client = TestClient(_make_app(tmp_path), base_url="http://testserver", headers={"X-Catown-Client": "test"})

            r = client.post("/api/projects", json={
                "name": "TaskRun Stream No Sidecar", "agent_names": ["analyst", "developer", "tester"]
            })
            cid = r.json()["chatroom_id"]
            turn_id = "turn-task-run-stream-no-sidecar"

            stream = client.post(
                f"/api/chatrooms/{cid}/messages/stream",
                json={"content": "@analyst @developer @tester inspect the runtime", "client_turn_id": turn_id},
            )

            assert stream.status_code == 200

            runs = client.get(
                f"/api/chatrooms/{cid}/task-runs",
                params={"client_turn_id": turn_id},
            ).json()

            detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
            schedule_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_plan_created")
            assert schedule_event["payload"]["mode"] == "linear_blocking_chain"
            assert schedule_event["payload"]["sidecar_step_count"] == 0
            assert schedule_event["payload"]["sidecar_agent_types"] == []
            assert schedule_event["payload"]["runtime"]["waiting_step_count"] == 2
            assert [step["dispatch_kind"] for step in schedule_event["payload"]["steps"]] == [
                "blocking",
                "blocking",
                "blocking",
            ]

            dispatch_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_dispatched"]
            assert [event["payload"]["agent_type"] for event in dispatch_events] == ["analyst", "developer", "tester"]
            completed_events = [event for event in detail["events"] if event["event_type"] == "scheduler_step_completed"]
            assert completed_events[-1]["payload"]["runtime"]["completed_step_count"] == 3
        finally:
            if previous_config_file is None:
                os.environ.pop("AGENT_CONFIG_FILE", None)
            else:
                os.environ["AGENT_CONFIG_FILE"] = previous_config_file

    def test_standalone_multi_mention_stream_rebuilds_tool_loop_from_turn_state(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        seen_messages = []

        async def scripted_stream(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            call_no = len(seen_messages)
            if call_no == 1:
                yield {
                    "type": "done",
                    "full_content": "I will inspect the backend tree first.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "list_files",
                                "arguments": "{\"directory\": \"backend\", \"pattern\": \"*.py\", \"recursive\": false}",
                            },
                        }
                    ],
                }
                return
            if call_no == 2:
                yield {
                    "type": "done",
                    "full_content": "Now I should open the API route file.",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"file_path\": \"backend/routes/api.py\", \"encoding\": \"utf-8\"}",
                            },
                        }
                    ],
                }
                return
            if call_no == 3:
                yield {"type": "content", "delta": "Analyst done."}
                yield {"type": "done", "full_content": "Analyst done.", "tool_calls": None}
                return

            yield {"type": "content", "delta": "Developer done."}
            yield {"type": "done", "full_content": "Developer done.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = scripted_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/chats", json={"title": "Standalone Rebuild"})
        cid = r.json()["id"]

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst @developer inspect the backend", "client_turn_id": "turn-standalone-collab-rebuild"},
        )

        assert stream.status_code == 200
        assert len(seen_messages) == 4

        second_call_messages = seen_messages[1]
        third_call_messages = seen_messages[2]
        fourth_call_messages = seen_messages[3]

        assert any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in second_call_messages
        )
        assert any(
            message.get("role") == "tool" and message.get("name") == "read_file"
            for message in third_call_messages
        )
        assert not any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in third_call_messages
        )
        assert any(
            message.get("role") == "user" and "## Inter-Agent Messages" in str(message.get("content") or "")
            and "Analyst done." in str(message.get("content") or "")
            for message in fourth_call_messages
        )
        assert any(
            message.get("role") == "tool"
            and message.get("name") == "read_file"
            for message in fourth_call_messages
        )

    def test_project_multi_mention_stream_rebuilds_tool_loop_from_turn_state(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        seen_messages = []

        async def scripted_stream(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            call_no = len(seen_messages)
            if call_no == 1:
                yield {
                    "type": "done",
                    "full_content": "I will inspect the backend tree first.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "list_files",
                                "arguments": "{\"directory\": \"backend\", \"pattern\": \"*.py\", \"recursive\": false}",
                            },
                        }
                    ],
                }
                return
            if call_no == 2:
                yield {
                    "type": "done",
                    "full_content": "Now I should open the API route file.",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"file_path\": \"backend/routes/api.py\", \"encoding\": \"utf-8\"}",
                            },
                        }
                    ],
                }
                return
            if call_no == 3:
                yield {"type": "content", "delta": "Analyst done."}
                yield {"type": "done", "full_content": "Analyst done.", "tool_calls": None}
                return

            yield {"type": "content", "delta": "Developer done."}
            yield {"type": "done", "full_content": "Developer done.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = scripted_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Project Rebuild", "agent_names": ["analyst", "developer"]})
        cid = r.json()["chatroom_id"]

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst @developer inspect the backend", "client_turn_id": "turn-project-collab-rebuild"},
        )

        assert stream.status_code == 200
        assert len(seen_messages) == 4

        second_call_messages = seen_messages[1]
        third_call_messages = seen_messages[2]

        assert any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in second_call_messages
        )
        assert any(
            message.get("role") == "tool" and message.get("name") == "read_file"
            for message in third_call_messages
        )
        assert not any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in third_call_messages
        )
    def test_runtime_cards_persist_for_refresh_replay(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_tool_stream(messages, tools=None):
            yield {"type": "content", "delta": "Checking files"}
            yield {
                "type": "done",
                "full_content": "Checking files",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "list_files",
                            "arguments": "{\"path\": \".\"}",
                        },
                    }
                ],
            }
            yield {"type": "content", "delta": "Done."}
            yield {"type": "done", "full_content": "Done.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_tool_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Runtime Replay", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "inspect repo", "client_turn_id": "turn-stream-1"},
        )
        body = stream.text

        assert stream.status_code == 200
        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        messages = client.get(f"/api/chatrooms/{cid}/messages").json()

        assert "tool_call" in body
        assert any(card.get("type") == "tool_call" for card in cards), cards
        assert any(card.get("type") == "llm_call" for card in cards)
        assert all(card.get("client_turn_id") == "turn-stream-1" for card in cards)
        assert any(
            message.get("agent_name") and message.get("client_turn_id") == "turn-stream-1"
            for message in messages
        )

    def test_runtime_cards_include_consult_calls(self, client):
        from models.database import Message, SessionLocal

        r = client.post("/api/projects", json={"name": "Consult Runtime Cards", "agent_names": ["analyst", "developer"]})
        cid = r.json()["chatroom_id"]

        db = SessionLocal()
        try:
            db.add_all(
                [
                    Message(
                        chatroom_id=cid,
                        agent_id=None,
                        content="consult_call",
                        message_type="runtime_card",
                        metadata_json=json.dumps(
                            {
                                "card": {
                                    "type": "consult_call",
                                    "source": "consult_agent",
                                    "status": "running",
                                    "agent": "developer",
                                    "target_agent": "analyst",
                                    "question_preview": "What is the main risk?",
                                    "consult_step_id": "consult-analyst-1",
                                    "client_turn_id": "turn-consult-runtime",
                                    "available_actions": ["wait", "cancel"],
                                }
                            }
                        ),
                    ),
                    Message(
                        chatroom_id=cid,
                        agent_id=None,
                        content="consult_call",
                        message_type="runtime_card",
                        metadata_json=json.dumps(
                            {
                                "card": {
                                    "type": "consult_call",
                                    "source": "consult_agent",
                                    "status": "completed",
                                    "agent": "developer",
                                    "target_agent": "analyst",
                                    "question_preview": "What is the main risk?",
                                    "response_preview": "The main risk is drift.",
                                    "consult_step_id": "consult-analyst-1",
                                    "client_turn_id": "turn-consult-runtime",
                                    "available_actions": ["close"],
                                }
                            }
                        ),
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        consult_cards = [card for card in cards if card.get("type") == "consult_call"]

        assert len(consult_cards) == 2
        assert consult_cards[0]["status"] == "running"
        assert consult_cards[0]["source"] == "consult_agent"
        assert consult_cards[0]["available_actions"] == ["wait", "cancel"]
        assert consult_cards[1]["status"] == "completed"
        assert consult_cards[1]["response_preview"] == "The main risk is drift."
        assert consult_cards[1]["available_actions"] == ["close"]

    def test_messages_include_consult_runtime_summary(self, client):
        from models.database import Message, SessionLocal, TaskRun, TaskRunEvent

        r = client.post("/api/projects", json={"name": "Consult Message Summary", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        turn_id = "turn-consult-message-summary"

        db = SessionLocal()
        try:
            task_run = TaskRun(
                chatroom_id=cid,
                run_kind="project_single_agent",
                status="running",
                title="Consult message summary",
                user_request="Surface consult state in chat messages.",
                client_turn_id=turn_id,
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "consult-analyst-message-1",
                            "position": 0,
                            "requested_name": "analyst",
                            "agent_id": "analyst",
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "consult",
                            "wait_for_step_id": None,
                            "attached_to_step_id": None,
                            "source": "consult_agent",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                )
            )
            db.add(
                Message(
                    chatroom_id=cid,
                    agent_id=None,
                    content="Please check with the analyst.",
                    message_type="text",
                    metadata_json=json.dumps({"client_turn_id": turn_id}),
                )
            )
            db.commit()
        finally:
            db.close()

        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        summary_message = next(message for message in messages if message.get("client_turn_id") == turn_id)

        assert summary_message["runtime_summary"]["task_run_id"] is not None
        assert summary_message["runtime_summary"]["active_subagent_handle"]["dispatch_kind"] == "consult"
        assert summary_message["runtime_summary"]["active_consult_handle"]["step_id"] == "consult-analyst-message-1"
        assert summary_message["runtime_summary"]["active_consult_handle"]["control_state"] == "await_completion"

    def test_stream_rebuilds_tool_loop_from_turn_state(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        seen_messages = []

        async def scripted_stream(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            call_no = len(seen_messages)
            if call_no == 1:
                yield {
                    "type": "done",
                    "full_content": "I will inspect the backend tree first.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "list_files",
                                "arguments": "{\"directory\": \"backend\", \"pattern\": \"*.py\", \"recursive\": false}",
                            },
                        }
                    ],
                }
                return
            if call_no == 2:
                yield {
                    "type": "done",
                    "full_content": "Now I should open the API route file.",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"file_path\": \"backend/routes/api.py\", \"encoding\": \"utf-8\"}",
                            },
                        }
                    ],
                }
                return

            yield {"type": "content", "delta": "Done inspecting the backend."}
            yield {"type": "done", "full_content": "Done inspecting the backend.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = scripted_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Stream Tool Loop Rebuild", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "Inspect the backend implementation", "client_turn_id": "turn-stream-rebuild-1"},
        )

        assert stream.status_code == 200
        assert len(seen_messages) == 3

        second_call_messages = seen_messages[1]
        third_call_messages = seen_messages[2]

        assert any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in second_call_messages
        )
        assert any(
            message.get("role") == "tool" and message.get("name") == "read_file"
            for message in third_call_messages
        )
        assert not any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in third_call_messages
        )
        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(message.get("agent_name") == "analyst" for message in messages)

    def test_project_single_agent_stream_rehydrates_checkpoint_state(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes
        from services.run_ledger import append_task_event

        seen_messages = []

        async def scripted_stream(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            yield {"type": "content", "delta": "Resumed."}
            yield {"type": "done", "full_content": "Resumed.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = scripted_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        original_create_task_run = api_routes.create_task_run

        def seeded_create_task_run(db, *args, **kwargs):
            task_run = original_create_task_run(db, *args, **kwargs)
            append_task_event(
                db,
                task_run,
                "tool_round_recorded",
                agent_name="analyst",
                summary="analyst completed a tool round before streaming resume.",
                payload={
                    "turn": 1,
                    "tool_names": ["read_file"],
                    "tool_count": 1,
                    "tool_status_counts": {"succeeded": 1},
                    "blocked_tool_count": 0,
                    "turn_local_state": {
                        "assistant_content": "Open the design doc before continuing.",
                        "tool_results": [
                            {
                                "tool_call_id": "stream_resume_call_1",
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
                                        "id": "stream_resume_call_1",
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
                                "tool_call_id": "stream_resume_call_1",
                                "name": "read_file",
                                "content": "Design checkpoint contents",
                            },
                        ],
                    },
                },
            )
            return task_run

        r = client.post("/api/projects", json={"name": "Stream Resume Project", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]

        with patch.object(api_routes, "create_task_run", side_effect=seeded_create_task_run):
            stream = client.post(
                f"/api/chatrooms/{cid}/messages/stream",
                json={"content": "Resume the approved streaming work", "client_turn_id": "turn-stream-resume-1"},
            )

        assert stream.status_code == 200
        assert len(seen_messages) == 1
        first_call_messages = seen_messages[0]
        assert any(
            message.get("role") == "assistant"
            and isinstance(message.get("tool_calls"), list)
            and any(
                tool_call.get("function", {}).get("name") == "read_file"
                for tool_call in message.get("tool_calls", [])
            )
            for message in first_call_messages
        )
        assert any(
            message.get("role") == "tool"
            and message.get("name") == "read_file"
            and "Design checkpoint contents" in str(message.get("content") or "")
            for message in first_call_messages
        )

    def test_stream_assistant_multi_agent_mentions_schedule_multiple_handoffs(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def scripted_stream(messages, tools=None):
            yield {"type": "content", "delta": "@tester @developer please inspect the backend"}
            yield {
                "type": "done",
                "full_content": "@tester @developer please inspect the backend",
                "tool_calls": None,
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = scripted_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        original_trigger_agent_response = api_routes.trigger_agent_response
        handoff_calls = []
        helper_calls = []
        published_handoffs = []

        async def patched_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, task_run_id=None, extra_context="", checkpoint_snapshot=None):
            if client_turn_id and str(client_turn_id).startswith("handoff-"):
                handoff_calls.append({
                    "chatroom_id": chatroom_id,
                    "user_message": user_message,
                    "client_turn_id": client_turn_id,
                    "extra_context": extra_context,
                })
                return {"completed": True, "awaiting_tool_approval": False, "task_run_id": None}
            return await original_trigger_agent_response(
                chatroom_id,
                user_message,
                client_turn_id=client_turn_id,
                task_run_id=task_run_id,
                extra_context=extra_context,
                checkpoint_snapshot=checkpoint_snapshot,
            )

        async def fake_maybe_schedule_assistant_handoff(**kwargs):
            helper_calls.append(kwargs)
            await kwargs["publish_agent_message_card"](
                chatroom_id=kwargs["chatroom_id"],
                from_agent="analyst",
                to_agent="tester",
                content=kwargs["content"],
                client_turn_id="handoff-test-1",
                extra_metadata={"handoff_depth": 1},
            )
            await kwargs["publish_agent_message_card"](
                chatroom_id=kwargs["chatroom_id"],
                from_agent="analyst",
                to_agent="developer",
                content=kwargs["content"],
                client_turn_id="handoff-test-2",
                extra_metadata={"handoff_depth": 1},
            )
            await patched_trigger_agent_response(
                kwargs["chatroom_id"],
                "@tester please inspect the backend",
                client_turn_id="handoff-test-1",
                extra_context="Automatic handoff from analyst to tester.",
            )
            await patched_trigger_agent_response(
                kwargs["chatroom_id"],
                "@developer please inspect the backend",
                client_turn_id="handoff-test-2",
                extra_context="Automatic handoff from analyst to developer.",
            )
            return "tester"

        with patch.object(api_routes, "trigger_agent_response", side_effect=patched_trigger_agent_response), \
             patch("routes.api.maybe_schedule_assistant_handoff", side_effect=fake_maybe_schedule_assistant_handoff):
            r = client.post("/api/projects", json={"name": "Auto Handoff Project", "agent_names": ["analyst", "tester", "developer"]})
            cid = r.json()["chatroom_id"]

            stream = client.post(
                f"/api/chatrooms/{cid}/messages/stream",
                json={"content": "@analyst coordinate this", "client_turn_id": "turn-auto-handoff-1"},
            )

        assert stream.status_code == 200
        assert len(helper_calls) == 1
        assert helper_calls[0]["content"] == "@tester @developer please inspect the backend"
        runtime_cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        handoff_cards = [
            card for card in runtime_cards
            if card.get("type") == "agent_message" and card.get("content") == "@tester @developer please inspect the backend"
        ]
        assert len(handoff_cards) >= 2
        handoff_targets = {card.get("to_agent") for card in handoff_cards}
        assert {"tester", "developer"}.issubset(handoff_targets)
        assert len(handoff_calls) == 2
        handoff_messages = {call["user_message"] for call in handoff_calls}
        assert "@tester please inspect the backend" in handoff_messages
        assert "@developer please inspect the backend" in handoff_messages

    def test_stream_persists_final_done_content_without_delta(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        seen_messages = []

        async def scripted_stream(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            if len(seen_messages) == 1:
                yield {
                    "type": "done",
                    "full_content": "I will inspect the backend tree first.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "list_files",
                                "arguments": "{\"directory\": \"backend\", \"pattern\": \"*.py\", \"recursive\": false}",
                            },
                        }
                    ],
                }
                return

            yield {
                "type": "done",
                "full_content": "Done inspecting the backend.",
                "tool_calls": None,
                "usage": {"prompt_tokens": 32, "completion_tokens": 8, "total_tokens": 40},
                "finish_reason": "stop",
                "timings": {"completed_ms": 21},
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = scripted_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Done Only Final Stream", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        turn_id = "turn-stream-done-only"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "Inspect the backend implementation", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert len(seen_messages) == 2

        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        final_message = next(
            message
            for message in reversed(messages)
            if message.get("agent_name") == "analyst"
        )
        assert final_message["content"] == "Done inspecting the backend."
        assert final_message.get("client_turn_id") == turn_id

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        assert any(card.get("type") == "tool_call" for card in cards)
        assert any(
            card.get("type") == "llm_call" and card.get("response") == "Done inspecting the backend."
            for card in cards
        )

    def test_tool_error_marks_runtime_card_failed(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_tool_error_stream(messages, tools=None):
            yield {
                "type": "done",
                "full_content": "",
                "tool_calls": [
                    {
                        "id": "call_missing_file",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"file_path\": \"missing.md\", \"encoding\": \"utf-8\"}",
                        },
                    }
                ],
            }
            yield {"type": "content", "delta": "Could not read file."}
            yield {"type": "done", "full_content": "Could not read file.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_tool_error_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Tool Error Replay", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "inspect missing file", "client_turn_id": "turn-tool-error-1"},
        )
        body = stream.text

        assert stream.status_code == 200
        assert '"success": false' in body or '"success":false' in body

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        tool_card = next(card for card in cards if card.get("type") == "tool_call")
        assert tool_card["tool"] == "read_file"
        assert tool_card["success"] is False
        assert "File not found" in (tool_card.get("result") or "")

    def test_tool_blocked_marks_runtime_card_and_ledger(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_blocked_tool_stream(messages, tools=None):
            yield {
                "type": "done",
                "full_content": "",
                "tool_calls": [
                    {
                        "id": "call_delete_file",
                        "function": {
                            "name": "delete_file",
                            "arguments": "{\"file_path\": \"danger.txt\"}",
                        },
                    }
                ],
            }
            yield {"type": "content", "delta": "Delete was blocked."}
            yield {"type": "done", "full_content": "Delete was blocked.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_blocked_tool_stream
        mock_llm.chat_with_tools = AsyncMock(
            return_value={
                "content": "Delete approved and completed. No further action needed.",
                "tool_calls": None,
            }
        )
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Tool Approval Block", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        turn_id = "turn-tool-approval-block-1"
        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "delete the file", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert '"status": "approval_blocked"' in stream.text or '"status":"approval_blocked"' in stream.text

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        tool_card = next(card for card in cards if card.get("type") == "tool_call")
        assert tool_card["tool"] == "delete_file"
        assert tool_card["success"] is False
        assert tool_card["status"] == "approval_blocked"
        assert tool_card["blocked"] is True
        assert tool_card["blocked_kind"] == "approval"

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()
        assert runs[0]["updated_at"] != runs[0]["created_at"]
        detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
        round_event = next(event for event in detail["events"] if event["event_type"] == "tool_round_recorded")
        blocked_event = next(event for event in detail["events"] if event["event_type"] == "tool_call_blocked")
        assert round_event["payload"]["tool_status_counts"]["approval_blocked"] == 1
        assert round_event["payload"]["blocked_tool_count"] == 1
        assert not round_event["payload"].get("turn_local_state", {}).get("tool_results")
        assert blocked_event["payload"]["tool_name"] == "delete_file"
        assert blocked_event["payload"]["blocked_kind"] == "approval"
        assert blocked_event["payload"]["status"] == "approval_blocked"
        assert mock_llm.chat_with_tools.await_count == 0
        messages = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert not any(
            message.get("agent_name") == "analyst"
            and message.get("message_type") == "text"
            and message.get("client_turn_id") == turn_id
            for message in messages
        )
        queue_items = client.get(
            "/api/approval-queue",
            params={"task_run_id": runs[0]["id"], "status": "pending"},
        ).json()
        assert len(queue_items) == 1
        assert queue_items[0]["queue_kind"] == "approval"
        assert queue_items[0]["target_kind"] == "tool"
        assert queue_items[0]["target_name"] == "delete_file"
        assert queue_items[0]["status"] == "pending"

        approved = client.post(
            f"/api/approval-queue/{queue_items[0]['id']}/approve",
            json={"note": "Approve for later replay."},
        ).json()
        assert approved["status"] == "approved"
        assert approved["resolved_by"] == "user"

        resolved_detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
        assert any(
            event["event_type"] == "approval_queue_item_resolved"
            and event["payload"]["status"] == "approved"
            for event in resolved_detail["events"]
        )
        assert resolved_detail["approval_queue_items"][0]["status"] == "approved"

    def test_blocked_tool_stops_before_following_tool_calls(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_multi_tool_stream(messages, tools=None):
            yield {
                "type": "done",
                "full_content": "",
                "tool_calls": [
                    {
                        "id": "call_delete_file_blocked",
                        "function": {
                            "name": "delete_file",
                            "arguments": "{\"file_path\": \"danger.txt\"}",
                        },
                    },
                    {
                        "id": "call_read_file_never_run",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"file_path\": \"README.md\"}",
                        },
                    },
                ],
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_multi_tool_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        project = client.post("/api/projects", json={"name": "Blocked Tool Prefix Stop", "agent_names": ["analyst"]}).json()
        cid = project["chatroom_id"]
        turn_id = "turn-blocked-tool-prefix-stop"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "run two tools", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert '"type": "approval_pending"' in stream.text or '"type":"approval_pending"' in stream.text

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        tool_cards = [card for card in cards if card.get("type") == "tool_call" and card.get("client_turn_id") == turn_id]
        assert len(tool_cards) == 1
        assert tool_cards[0]["tool"] == "delete_file"

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()
        queue_items = client.get(
            "/api/approval-queue",
            params={"task_run_id": runs[0]["id"], "status": "pending"},
        ).json()
        assert len(queue_items) == 1
        assert queue_items[0]["target_name"] == "delete_file"

    def test_approve_tool_queue_item_replays_blocked_tool(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_blocked_tool_stream(messages, tools=None):
            yield {
                "type": "done",
                "full_content": "",
                "tool_calls": [
                    {
                        "id": "call_delete_file_replay",
                        "function": {
                            "name": "delete_file",
                            "arguments": json.dumps({"file_path": str(delete_target)}, ensure_ascii=False),
                        },
                    }
                ],
            }
            yield {"type": "content", "delta": "Delete was blocked pending approval."}
            yield {
                "type": "done",
                "full_content": "Delete was blocked pending approval.",
                "tool_calls": None,
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_blocked_tool_stream
        mock_llm.chat_with_tools = AsyncMock(
            return_value={
                "content": "Delete approved and completed. No further action needed.",
                "tool_calls": None,
            }
        )
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        try:
            project = client.post("/api/projects", json={"name": "Tool Replay Approval", "agent_names": ["analyst"]}).json()
            delete_target = Path(project["workspace_path"]) / f"queue-replay-delete-{os.getpid()}.txt"
            if delete_target.exists():
                delete_target.unlink()
            delete_target.write_text("delete me", encoding="utf-8")
            cid = project["chatroom_id"]
            turn_id = "turn-tool-replay-approval-1"
            stream = client.post(
                f"/api/chatrooms/{cid}/messages/stream",
                json={"content": "delete the approved temp file", "client_turn_id": turn_id},
            )

            assert stream.status_code == 200
            assert delete_target.exists() is True

            runs = client.get(
                f"/api/chatrooms/{cid}/task-runs",
                params={"client_turn_id": turn_id},
            ).json()
            queue_items = client.get(
                "/api/approval-queue",
                params={"task_run_id": runs[0]["id"], "status": "pending"},
            ).json()
            assert len(queue_items) == 1

            approved = client.post(
                f"/api/approval-queue/{queue_items[0]['id']}/approve",
                json={"note": "Replay the approved delete."},
            ).json()
            assert approved["status"] == "approved"
            assert approved["resolution_payload"]["action_taken"] == "queue_resolved_only"

            resolved_detail = _wait_for_task_run_event(
                client,
                runs[0]["id"],
                "approval_queue_item_resolved",
                payload_predicate=lambda payload: payload.get("action_taken") == "tool_replayed",
                timeout_seconds=3.0,
            )
            assert resolved_detail is not None
            assert delete_target.exists() is False
            replay_round = next(
                event
                for event in resolved_detail["events"]
                if event["event_type"] == "tool_round_recorded"
                and event["payload"].get("replay_of_queue_item_id") == queue_items[0]["id"]
            )
            assert replay_round["payload"]["replay"] is True

            resolved_event = next(
                event
                for event in resolved_detail["events"]
                if event["event_type"] == "approval_queue_item_resolved"
                and event["payload"].get("action_taken") == "tool_replayed"
            )
            assert resolved_event["payload"]["action_taken"] == "tool_replayed"
            assert resolved_event["payload"]["replay_status"] == "succeeded"
            followup_event = next(
                event
                for event in resolved_detail["events"]
                if event["event_type"] == "approval_queue_item_followup_triggered"
            )
            assert followup_event["payload"]["queue_item_id"] == queue_items[0]["id"]
            assert followup_event["payload"]["tool_call_id"] == "call_delete_file_replay"
            assert "message_id" not in followup_event["payload"]

            messages = client.get(f"/api/chatrooms/{cid}/messages").json()
            followup_call = mock_llm.chat_with_tools.await_args_list[-1]
            followup_messages = followup_call.args[0]
            assert any(
                message.get("role") == "assistant"
                and isinstance(message.get("tool_calls"), list)
                and any(
                    tool_call.get("function", {}).get("name") == "delete_file"
                    and tool_call.get("id") == "call_delete_file_replay"
                    for tool_call in message.get("tool_calls", [])
                )
                for message in followup_messages
            )
            assert any(
                message.get("role") == "tool"
                and message.get("tool_call_id") == "call_delete_file_replay"
                and "deleted" in str(message.get("content", "")).lower()
                for message in followup_messages
            )
            assert not any(message["message_type"] == "tool_result" for message in messages)
            assert any(
                message["message_type"] == "text"
                and message["agent_name"] == "analyst"
                and "Delete approved and completed" in message["content"]
                for message in messages
            )
        finally:
            if delete_target.exists():
                delete_target.unlink()

    def test_run_shell_foreground_wait_elapsed_does_not_create_timeout_queue_item(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_timeout_then_followup_stream(messages, tools=None):
            if not any(message.get("role") == "tool" for message in messages if isinstance(message, dict)):
                yield {
                    "type": "done",
                    "full_content": "",
                    "tool_calls": [
                        {
                            "id": "call_run_shell_background_running",
                            "function": {
                                "name": "run_shell",
                                "arguments": json.dumps(
                                    {
                                        "command": 'python -c "import time; time.sleep(2); print(\'done\')"',
                                        "cwd": ".",
                                        "timeout_seconds": 1,
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
                return
            yield {"type": "content", "delta": "Command is running in the background."}
            yield {
                "type": "done",
                "full_content": "Command is running in the background.",
                "tool_calls": None,
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_timeout_then_followup_stream
        mock_llm.chat_with_tools = AsyncMock(
            return_value={
                "content": "Finished after waiting.",
                "tool_calls": None,
            }
        )
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        project = client.post(
            "/api/projects",
            json={"name": "Background Running Shell", "agent_names": ["analyst"]},
        ).json()
        cid = project["chatroom_id"]
        turn_id = "turn-background-running-shell"

        with patch.object(api_routes, "_spawn_background_tracked_run_shell_watch"):
            stream = client.post(
                f"/api/chatrooms/{cid}/messages/stream",
                json={"content": "run the long test command", "client_turn_id": turn_id},
            )

            assert stream.status_code == 200

            runs = client.get(
                f"/api/chatrooms/{cid}/task-runs",
                params={"client_turn_id": turn_id},
            ).json()
            assert len(runs) == 1
            task_run_id = runs[0]["id"]

            first_queue_items = client.get(
                "/api/approval-queue",
                params={"task_run_id": task_run_id, "status": "pending"},
            ).json()
            assert len(first_queue_items) == 1
            assert first_queue_items[0]["target_name"] == "run_shell"
            assert first_queue_items[0]["request_payload"]["blocked_kind"] == "approval"

            approved_first = client.post(
                f"/api/approval-queue/{first_queue_items[0]['id']}/approve",
                json={"note": "Allow the command first."},
            ).json()
            assert approved_first["status"] == "approved"

            detail = _wait_for_task_run_event(
                client,
                task_run_id,
                "tool_round_recorded",
                payload_predicate=lambda payload: payload.get("tool_status_counts", {}).get("background_running") == 1,
                timeout_seconds=8.0,
            )
            assert detail is not None
            assert detail["status"] == "running"

            pending_after_background = client.get(
                "/api/approval-queue",
                params={"task_run_id": task_run_id, "status": "pending"},
            ).json()
            assert pending_after_background == []
            assert any(
                event["event_type"] == "tool_round_recorded"
                and event["payload"].get("tool_status_counts", {}).get("background_running") == 1
                for event in detail["events"]
            )
            assert not any(
                event["event_type"] == "approval_queue_item_created"
                and event["payload"].get("target_name") == "run_shell"
                and event["payload"].get("status") == "pending"
                and event["payload"].get("queue_item_id") != first_queue_items[0]["id"]
                for event in detail["events"]
            )
        followup_call = mock_llm.chat_stream
        assert followup_call is not None

    def test_approve_run_shell_item_does_not_create_timeout_queue(self, client):
        import models.database as db_mod
        import routes.api as api_routes
        from types import SimpleNamespace
        from services.runner_lifecycle import record_tool_round

        def run_followup_now(item_id, *, request_payload, resolved_by, resolution_note):
            thread = threading.Thread(
                target=lambda: asyncio.run(
                    api_routes._finalize_approved_queue_item_followup_async(
                        item_id,
                        request_payload=request_payload,
                        resolved_by=resolved_by,
                        resolution_note=resolution_note,
                    )
                )
            )
            thread.start()
            thread.join()

        project = client.post("/api/projects", json={"name": "No Timeout Queue Project", "agent_names": ["analyst"]}).json()
        cid = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            project_row = db.query(db_mod.Project).filter(db_mod.Project.id == project["id"]).first()
            task_run = db_mod.TaskRun(
                chatroom_id=cid,
                project_id=project["id"],
                run_kind="project_single_agent",
                status="running",
                title="Run shell without timeout queue",
                user_request="Run shell without timeout queue",
                initiator="user",
                target_agent_name="analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            arguments = json.dumps(
                {
                    "command": "sleep 2",
                    "cwd": project_row.workspace_path,
                    "timeout_seconds": 1,
                },
                ensure_ascii=False,
            )
            record_tool_round(
                db,
                task_run,
                agent_name="analyst",
                turn=1,
                tool_names=["run_shell"],
                tool_results=[],
                blocked_tool_results=[
                    SimpleNamespace(
                        tool_call_id="call_run_shell_continue_timeout",
                        tool_name="run_shell",
                        arguments=arguments,
                        status="approval_blocked",
                        success=False,
                        blocked=True,
                        blocked_kind="approval",
                        blocked_reason="run_shell blocked in project chat",
                        result="run_shell blocked in project chat",
                        metadata={},
                    )
                ],
                summary="run_shell blocked in project chat",
            )
            queue_item = (
                db.query(db_mod.ApprovalQueueItem)
                .filter(db_mod.ApprovalQueueItem.task_run_id == task_run.id)
                .filter(db_mod.ApprovalQueueItem.status == "pending")
                .first()
            )
            assert queue_item is not None
            queue_item_id = queue_item.id
            task_run_id = task_run.id
        finally:
            db.close()

        with patch.object(api_routes, "_spawn_approval_followup_worker", side_effect=run_followup_now), patch.object(api_routes, "_spawn_background_tracked_run_shell_watch"):
            approved = client.post(
                f"/api/approval-queue/{queue_item_id}/approve",
                json={"note": "Start run shell."},
            ).json()

        assert approved["status"] == "approved"

        detail = _wait_for_task_run_event(
            client,
            task_run_id,
            "tool_round_recorded",
            payload_predicate=lambda payload: payload.get("tool_status_counts", {}).get("background_running") == 1,
            timeout_seconds=3.0,
        )
        assert detail is not None
        assert detail["status"] in {"running", "completed"}
        assert any(
            event["event_type"] == "tool_round_recorded"
            and event["payload"].get("tool_status_counts", {}).get("background_running") == 1
            for event in detail["events"]
        )
        assert not any(
            event["event_type"] == "approval_queue_item_followup_triggered"
            for event in detail["events"]
        )
        pending_items = client.get(
            "/api/approval-queue",
            params={"task_run_id": task_run_id, "status": "pending"},
        ).json()
        assert pending_items == []

    def test_approve_run_shell_queue_item_continue_restores_project_workspace(self, client):
        import models.database as db_mod
        from types import SimpleNamespace
        from services.runner_lifecycle import record_tool_round

        project = client.post("/api/projects", json={"name": "Continue Workspace Project", "agent_names": ["analyst"]}).json()
        cid = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            project_row = db.query(db_mod.Project).filter(db_mod.Project.id == project["id"]).first()
            task_run = db_mod.TaskRun(
                chatroom_id=cid,
                project_id=project["id"],
                run_kind="project_single_agent",
                status="running",
                title="Continue run_shell within project workspace",
                user_request="Continue run_shell within project workspace",
                initiator="user",
                target_agent_name="analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            arguments = json.dumps(
                {
                    "command": "pwd",
                    "cwd": project_row.workspace_path,
                    "timeout_seconds": 10,
                },
                ensure_ascii=False,
            )
            record_tool_round(
                db,
                task_run,
                agent_name="analyst",
                turn=1,
                tool_names=["run_shell"],
                tool_results=[],
                blocked_tool_results=[
                    SimpleNamespace(
                        tool_call_id="call_run_shell_continue_workspace",
                        tool_name="run_shell",
                        arguments=arguments,
                        status="approval_blocked",
                        success=False,
                        blocked=True,
                        blocked_kind="approval",
                        blocked_reason="run_shell blocked in project chat",
                        result="run_shell blocked in project chat",
                        metadata={},
                    )
                ],
                summary="run_shell blocked in project chat",
            )
            queue_item = (
                db.query(db_mod.ApprovalQueueItem)
                .filter(db_mod.ApprovalQueueItem.task_run_id == task_run.id)
                .filter(db_mod.ApprovalQueueItem.status == "pending")
                .first()
            )
            assert queue_item is not None
            queue_item_id = queue_item.id
            task_run_id = task_run.id
        finally:
            db.close()

        approved = client.post(
            f"/api/approval-queue/{queue_item_id}/approve",
            json={"note": "Continue in project workspace."},
        ).json()

        assert approved["status"] == "approved"
        assert approved["resolution_payload"]["action_taken"] == "queue_resolved_only"

        resolved_detail = _wait_for_task_run_event(
            client,
            task_run_id,
            "approval_queue_item_resolved",
            payload_predicate=lambda payload: payload.get("action_taken") == "run_shell_continued_after_approval",
            timeout_seconds=3.0,
        )
        assert resolved_detail is not None
        resolved_event = next(
            event
            for event in resolved_detail["events"]
            if event["event_type"] == "approval_queue_item_resolved"
            and event["payload"].get("action_taken") == "run_shell_continued_after_approval"
        )
        assert resolved_event["payload"]["replay_success"] is True
        assert resolved_event["payload"]["replay_status"] == "succeeded"
        assert "working directory outside workspace" not in (
            resolved_event["payload"].get("replay_result_preview", "").lower()
        )

    def test_approve_tool_queue_item_during_shutdown_skips_followup_worker(self, tmp_path):
        _make_app(tmp_path)
        import models.database as db_mod
        import routes.api as api_routes
        from services.runtime_lifecycle import mark_runtime_shutting_down, mark_runtime_starting

        db = db_mod.SessionLocal()
        try:
            project = db_mod.Project(name="Shutdown Approval Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = db_mod.Chatroom(
                project_id=project.id,
                title="Shutdown Approval Chat",
                session_type="project",
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            task_run = db_mod.TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                run_kind="project_single_agent",
                status="blocked",
                title="Approve during shutdown",
                user_request="Approve during shutdown",
                initiator="user",
                target_agent_name="analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            queue_item = db_mod.ApprovalQueueItem(
                task_run_id=task_run.id,
                chatroom_id=chatroom.id,
                project_id=project.id,
                queue_kind="approval",
                status="pending",
                source="tool_call_blocked",
                title="Approve run_shell",
                summary="run_shell blocked in project chat",
                agent_name="analyst",
                target_kind="tool",
                target_name="run_shell",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "run_shell",
                        "arguments": json.dumps({"command": "pwd", "cwd": ".", "timeout_seconds": 10}),
                        "resume_supported": True,
                        "turn": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(queue_item)
            db.commit()
            db.refresh(queue_item)
            queue_item_id = queue_item.id
            task_run_id = task_run.id
        finally:
            db.close()

        route_db = db_mod.SessionLocal()
        mark_runtime_shutting_down()
        try:
            with patch.object(api_routes, "_spawn_approval_followup_worker") as spawn_followup:
                approved = asyncio.run(
                    api_routes.approve_approval_queue_item(
                        queue_item_id,
                        api_routes.ApprovalQueueDecisionRequest(
                            note="Approve while server is shutting down.",
                        ),
                        route_db,
                    )
                )
        finally:
            route_db.close()
            mark_runtime_starting()

        assert approved["status"] == "approved"
        assert approved["resolution_payload"]["action_taken"] == "queue_resolved_only"
        assert approved["resolution_payload"]["followup_status"] == "skipped"
        assert approved["resolution_payload"]["followup_reason"] == "runtime_shutting_down"
        spawn_followup.assert_not_called()

        db = db_mod.SessionLocal()
        try:
            refreshed_item = db.query(db_mod.ApprovalQueueItem).filter(db_mod.ApprovalQueueItem.id == queue_item_id).first()
            assert refreshed_item is not None
            assert refreshed_item.status == "approved"
            persisted_payload = json.loads(refreshed_item.resolution_payload_json or "{}")
            assert persisted_payload["followup_status"] == "skipped"
            events = (
                db.query(db_mod.TaskRunEvent)
                .filter(db_mod.TaskRunEvent.task_run_id == task_run_id)
                .filter(db_mod.TaskRunEvent.event_type == "approval_queue_item_resolved")
                .order_by(db_mod.TaskRunEvent.event_index.asc())
                .all()
            )
        finally:
            db.close()

        shutdown_event = next(
            event
            for event in events
            if (json.loads(event.payload_json or "{}")).get("followup_reason") == "runtime_shutting_down"
        )
        shutdown_payload = json.loads(shutdown_event.payload_json or "{}")
        assert shutdown_payload["followup_status"] == "skipped"

    def test_startup_recovery_continues_agent_after_tracked_run_shell_result(self, tmp_path):
        app = _make_app(tmp_path)

        from fastapi.testclient import TestClient
        import models.database as db_mod
        import routes.api as api_routes
        from services.run_shell_processes import create_tracked_run_shell_handle, launch_tracked_run_shell, wait_for_tracked_run_shell

        with TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"}) as client:
            db = db_mod.SessionLocal()
            try:
                project = db_mod.Project(name="Tracked Recovery Project", status="active")
                db.add(project)
                db.commit()
                db.refresh(project)

                chatroom = db_mod.Chatroom(
                    project_id=project.id,
                    title="Tracked Recovery Chat",
                    session_type="project-bound",
                    is_visible_in_chat_list=True,
                )
                db.add(chatroom)
                db.commit()
                db.refresh(chatroom)

                task_run = db_mod.TaskRun(
                    chatroom_id=chatroom.id,
                    project_id=project.id,
                    client_turn_id="delegate-tracked-recovery",
                    run_kind="project_single_agent",
                    status="running",
                    title="Tracked run_shell recovery",
                    user_request="Run tests",
                    initiator="user",
                    target_agent_name="tester",
                )
                db.add(task_run)
                db.commit()
                db.refresh(task_run)

                command = f'{sys.executable} -c "print(\'recovered done\')"'
                handle = create_tracked_run_shell_handle(
                    command=command,
                    cwd=str(tmp_path),
                    timeout_seconds=1,
                    chatroom_id=chatroom.id,
                    project_id=project.id,
                    task_run_id=task_run.id,
                    client_turn_id=task_run.client_turn_id,
                    tool_call_id="call_recover",
                    turn=1,
                    agent_name="tester",
                )
                launch_tracked_run_shell(handle)
                asyncio.run(wait_for_tracked_run_shell(handle, timeout_seconds=None))

                db.add(
                    db_mod.Message(
                        chatroom_id=chatroom.id,
                        content="runtime_card",
                        message_type="runtime_card",
                        metadata_json=json.dumps(
                            {
                                "card": {
                                    "type": "tool_call",
                                    "tool": "run_shell",
                                    "arguments": json.dumps({"command": command, "cwd": str(tmp_path)}),
                                    "status": "running",
                                    "result": "running",
                                    "tracked_process": handle,
                                    "tool_call_id": "call_recover",
                                    "client_turn_id": task_run.client_turn_id,
                                    "run_id": task_run.id,
                                    "turn": 1,
                                }
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                db.commit()
                task_run_id = task_run.id
            finally:
                db.close()

            followup_calls = []

            async def fake_trigger_agent_response(
                chatroom_id,
                user_message,
                client_turn_id=None,
                task_run_id=None,
                extra_context="",
                checkpoint_snapshot=None,
            ):
                followup_calls.append(
                    {
                        "chatroom_id": chatroom_id,
                        "user_message": user_message,
                        "client_turn_id": client_turn_id,
                        "task_run_id": task_run_id,
                        "extra_context": extra_context,
                        "checkpoint_snapshot": checkpoint_snapshot,
                    }
                )
                return {"completed": True, "awaiting_tool_approval": False, "task_run_id": task_run_id}

            with patch.object(api_routes, "trigger_agent_response", side_effect=fake_trigger_agent_response):
                summary = asyncio.run(api_routes.recover_interrupted_task_runs(limit=10))
            assert summary["detected"] == 1
            assert summary["recovered"] == 0
            assert summary["skipped"] == 1

            detail = client.get(f"/api/task-runs/{task_run_id}").json()
            assert any(
                event["event_type"] == "tracked_run_shell_completed"
                and event["payload"].get("tool_success") is True
                for event in detail["events"]
            )
            recovery_event = next(
                event
                for event in detail["events"]
                if event["event_type"] == "tracked_run_shell_completed"
            )
            assert recovery_event["payload"]["tool_success"] is True
            queue_items = client.get(
                "/api/approval-queue",
                params={"task_run_id": task_run_id, "status": "pending"},
            ).json()
            assert queue_items == []

    def test_startup_recovery_terminalizes_orphaned_tracked_run_shell_task_run(self, tmp_path):
        _make_app(tmp_path)

        import models.database as db_mod
        import routes.api as api_routes
        from services.run_shell_processes import create_tracked_run_shell_handle, load_tracked_run_shell_handle

        db = db_mod.SessionLocal()
        try:
            project = db_mod.Project(name="Orphaned Tracked Shell Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = db_mod.Chatroom(
                project_id=project.id,
                title="Orphaned Tracked Shell Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            task_run = db_mod.TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                client_turn_id="orphaned-tracked-shell",
                run_kind="project_single_agent_stream",
                status="running",
                title="Orphaned run_shell recovery",
                user_request="Run tests",
                initiator="user",
                target_agent_name="Tester",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            handle = create_tracked_run_shell_handle(
                command="python -m pytest",
                cwd=str(tmp_path),
                timeout_seconds=60,
                chatroom_id=chatroom.id,
                project_id=project.id,
                task_run_id=task_run.id,
                client_turn_id=task_run.client_turn_id,
                tool_call_id="call_orphaned",
                turn=1,
                agent_name="Tester",
            )
            record = load_tracked_run_shell_handle(handle)
            assert record is not None
            state_path = Path(record["state_path"])
            log_path = Path(record["log_path"])
            record.update({
                "status": "running",
                "pid": 99999999,
                "worker_pid": 99999998,
                "started_at": "2026-05-11T21:53:57",
            })
            state_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            log_path.write_text("...........FF\n", encoding="utf-8")

            db.add(db_mod.Message(
                chatroom_id=chatroom.id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "tool": "run_shell",
                        "arguments": json.dumps({"command": "python -m pytest", "cwd": str(tmp_path)}),
                        "status": "running",
                        "result": "...........FF",
                        "pid": 99999999,
                        "tracked_process": handle,
                        "tool_call_id": "call_orphaned",
                        "client_turn_id": task_run.client_turn_id,
                        "run_id": task_run.id,
                        "turn": 1,
                    }
                }),
            ))
            db.commit()
            task_run_id = task_run.id
            chatroom_id = chatroom.id
        finally:
            db.close()

        summary = asyncio.run(api_routes.recover_interrupted_task_runs(limit=10))
        assert summary["detected"] == 1
        assert summary["recovered"] == 1

        db = db_mod.SessionLocal()
        try:
            detail = db.query(db_mod.TaskRun).filter(db_mod.TaskRun.id == task_run_id).first()
            assert detail is not None
            assert detail.status == "failed"
            assert "command stopped before completion" in (detail.summary or "")
            assert any(
                event.event_type == "tool_round_recorded"
                and json.loads(event.payload_json or "{}").get("recovery_kind") == "orphaned_run_shell_tracked_process"
                for event in detail.events
            )

            processes = [entry.model_dump() for entry in api_routes._build_chat_process_entries(db, chatroom_id)]
            assert all(process["status"] != "running" for process in processes)
            assert any(process["kind"] == "command" and process["status"] == "terminated" for process in processes)
        finally:
            db.close()

    def test_delegated_child_run_shell_result_reports_to_parent_owner(self, tmp_path):
        _make_app(tmp_path)

        import models.database as db_mod
        import routes.api as api_routes

        db = db_mod.SessionLocal()
        try:
            parent_agent = db.query(db_mod.Agent).filter(db_mod.Agent.agent_type == "valet").first()
            child_agent = db.query(db_mod.Agent).filter(db_mod.Agent.agent_type == "tester").first()
            assert parent_agent is not None
            assert child_agent is not None
            workspace = tmp_path / "delegated-owner-workspace"
            workspace.mkdir()
            project = db_mod.Project(
                name="Delegated Owner Project",
                status="active",
                workspace_path=str(workspace),
            )
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = db_mod.Chatroom(
                project_id=project.id,
                title="Delegated Owner Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            db.add_all(
                [
                    db_mod.AgentAssignment(project_id=project.id, agent_id=parent_agent.id),
                    db_mod.AgentAssignment(project_id=project.id, agent_id=child_agent.id),
                ]
            )
            db.commit()

            parent_run = db_mod.TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                client_turn_id="parent-turn",
                run_kind="project_single_agent",
                status="running",
                title="Run tests",
                user_request="测试一下当前项目",
                initiator="user",
                target_agent_name="Valet",
            )
            child_run = db_mod.TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                client_turn_id="delegate-task-1",
                run_kind="project_single_agent",
                status="running",
                title="@tester Run tests",
                user_request="@tester Run tests",
                initiator="user",
                target_agent_name="Tester",
            )
            db.add_all([parent_run, child_run])
            db.commit()
            db.refresh(parent_run)
            db.refresh(child_run)

            api_routes.append_task_event(
                db,
                parent_run,
                "delegated_task_dispatched",
                agent_name="Valet",
                payload={
                    "task_id": "task-1",
                    "task_title": "Run tests",
                    "from_agent": "Valet",
                    "target_agent_name": "Tester",
                    "child_client_turn_id": "delegate-task-1",
                },
            )
            db.add(
                db_mod.Message(
                    chatroom_id=chatroom.id,
                    agent_id=parent_agent.id,
                    content="@tester Run tests",
                    message_type="text",
                    metadata_json=json.dumps(
                        {
                            "client_turn_id": "delegate-task-1",
                            "parent_task_run_id": parent_run.id,
                            "delegated_task": {
                                "task_id": "task-1",
                                "task_title": "Run tests",
                                "task_description": "Run backend tests and report results.",
                                "delegator": "Valet",
                                "target_agent_name": "Tester",
                                "parent_task_run_id": parent_run.id,
                                "parent_client_turn_id": "parent-turn",
                                "required_outputs": ["test_report"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
            child_run_id = child_run.id
            parent_run_id = parent_run.id
            chatroom_id = chatroom.id
            child_agent_id = child_agent.id
        finally:
            db.close()

        followup_calls = []

        async def fake_trigger_agent_response(
            chatroom_id,
            user_message,
            client_turn_id=None,
            task_run_id=None,
            extra_context="",
            checkpoint_snapshot=None,
        ):
            followup_calls.append(
                {
                    "chatroom_id": chatroom_id,
                    "user_message": user_message,
                    "client_turn_id": client_turn_id,
                    "task_run_id": task_run_id,
                    "extra_context": extra_context,
                    "checkpoint_snapshot": checkpoint_snapshot,
                }
            )
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": task_run_id}

        next_card = {
            "type": "tool_call",
            "agent": "Tester",
            "tool": "run_shell",
            "arguments": json.dumps(
                {
                    "command": "/home/sun/.catown/venv/bin/python3 -m pytest backend/tests -q",
                    "cwd": "/workspace",
                }
            ),
            "success": False,
            "status": "failed",
            "blocked": False,
            "result": "4 failed, 764 passed",
            "tool_call_id": "call-test",
            "turn": 1,
            "tracked_process": {"token": "token-test", "status": "failed"},
        }
        with patch.object(api_routes, "trigger_agent_response", side_effect=fake_trigger_agent_response):
            asyncio.run(api_routes._continue_agent_after_tracked_run_shell_async(child_run_id, next_card))

        assert len(followup_calls) == 1
        assert followup_calls[0]["chatroom_id"] == chatroom_id
        assert followup_calls[0]["user_message"] == ""
        assert followup_calls[0]["client_turn_id"] == "parent-turn"
        assert followup_calls[0]["task_run_id"] == parent_run_id
        assert "agent handoff message from Tester to Valet" in followup_calls[0]["extra_context"]

        db = db_mod.SessionLocal()
        try:
            child_run = db.query(db_mod.TaskRun).filter(db_mod.TaskRun.id == child_run_id).first()
            parent_run = db.query(db_mod.TaskRun).filter(db_mod.TaskRun.id == parent_run_id).first()
            assert child_run.status == "completed"
            assert parent_run.status == "running"
            child_event_types = [event.event_type for event in child_run.events]
            parent_event_types = [event.event_type for event in parent_run.events]
            assert "tracked_run_shell_followup_queued" not in child_event_types
            assert "test_report_produced" in child_event_types
            assert "delegated_task_result_reported" in child_event_types
            assert "delegated_task_result_reported" in parent_event_types
            report_event = next(event for event in child_run.events if event.event_type == "test_report_produced")
            report_payload = json.loads(report_event.payload_json or "{}")
            assert report_payload["produced_outputs"] == ["test_report"]
            assert report_payload["required_outputs"] == ["test_report"]
            assert report_payload["test_report"]["kind"] == "test_report"
            assert report_payload["test_report"]["counts"]["passed"] == 764
            assert report_payload["test_report"]["counts"]["failed"] == 4
            assert report_payload["test_report"]["blocked"] is True
            report = (
                db.query(db_mod.Message)
                .filter(db_mod.Message.chatroom_id == chatroom_id, db_mod.Message.agent_id == child_agent_id)
                .order_by(db_mod.Message.id.desc())
                .first()
            )
            assert report is not None
            assert "@Valet" in report.content
            assert "Required output: test_report" in report.content
            assert "4 failed, 764 passed" in report.content
            metadata = json.loads(report.metadata_json or "{}")
            assert metadata["delegated_task_result"]["produced_outputs"] == ["test_report"]
            assert isinstance(metadata["delegated_task_result"].get("artifact_asset_id"), int)
            assert metadata["test_report"]["kind"] == "test_report"
            assert metadata["test_report_artifact"]["asset_type"] == "document.test_report"
            asset = (
                db.query(db_mod.Asset)
                .filter(db_mod.Asset.id == metadata["test_report_artifact"]["asset_id"])
                .first()
            )
            assert asset is not None
            assert asset.asset_type == "document.test_report"
            assert asset.owner_agent.lower() == "tester"
            assert asset.content_markdown is not None
            assert "# Test Report" in asset.content_markdown
            assert "@Valet Test report from Tester." in asset.content_markdown
            assert asset.storage_path is not None
            assert asset.storage_path.startswith("reports/tests/")
            asset_path = workspace / asset.storage_path
            assert asset_path.exists()
            assert asset_path.read_text(encoding="utf-8") == asset.content_markdown
        finally:
            db.close()

    def test_approve_pipeline_tool_queue_item_replays_and_resumes_pipeline(self, client):
        import models.database as db_mod
        import routes.api as api_routes

        db = db_mod.SessionLocal()
        try:
            project = db_mod.Project(name="Pipeline Queue Replay Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = db_mod.Chatroom(
                project_id=project.id,
                title="Pipeline Queue Replay Chat",
                session_type="project-bound",
                is_visible_in_chat_list=True,
            )
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            project.default_chatroom_id = chatroom.id
            db.commit()
            db.refresh(project)

            task_run = db_mod.TaskRun(
                chatroom_id=chatroom.id,
                project_id=project.id,
                run_kind="pipeline_run",
                status="running",
                title="Resume pipeline after tool approval",
                user_request="Resume pipeline after tool approval",
                initiator="user",
                target_agent_name="analyst",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            pipeline = db_mod.Pipeline(
                project_id=project.id,
                pipeline_name="default",
                status="paused",
                current_stage_index=0,
            )
            db.add(pipeline)
            db.commit()
            db.refresh(pipeline)

            run = db_mod.PipelineRun(
                pipeline_id=pipeline.id,
                task_run_id=task_run.id,
                run_number=1,
                status="paused",
                input_requirement="Resume pipeline after tool approval",
                workspace_path=str(Path.cwd()),
            )
            db.add(run)
            db.commit()
            db.refresh(run)

            stage = db_mod.PipelineStage(
                run_id=run.id,
                stage_name="analysis",
                display_name="Analysis",
                stage_order=0,
                agent_name="analyst",
                status="blocked",
                gate_type="auto",
            )
            db.add(stage)
            db.commit()
            db.refresh(stage)

            queue_item = db_mod.ApprovalQueueItem(
                task_run_id=task_run.id,
                chatroom_id=chatroom.id,
                project_id=project.id,
                pipeline_run_id=run.id,
                pipeline_stage_id=stage.id,
                queue_kind="approval",
                status="pending",
                source="runtime",
                title="Approve read_file",
                summary="read_file blocked in pipeline stage",
                agent_name="analyst",
                target_kind="tool",
                target_name="read_file",
                request_payload_json=json.dumps(
                    {
                        "tool_name": "read_file",
                        "arguments": "{\"file_path\": \"README.md\"}",
                        "resume_supported": True,
                        "pipeline_id": pipeline.id,
                        "pipeline_run_id": run.id,
                        "pipeline_stage_id": stage.id,
                        "stage_name": "analysis",
                        "display_name": "Analysis",
                        "turn": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(queue_item)
            db.commit()
            db.refresh(queue_item)
            queue_item_id = queue_item.id
            task_run_id = task_run.id
            pipeline_id = pipeline.id
        finally:
            db.close()

        async def fake_replay(db, item, request_payload):
            return api_routes.build_tool_result_record(
                tool_call_id="queue-replay-pipeline-1",
                tool_name="read_file",
                arguments=request_payload.get("arguments", "{}"),
                result="README contents",
                success=True,
            )

        instruct_mock = AsyncMock(return_value=None)
        resume_mock = AsyncMock(return_value=None)

        def run_followup_now(item_id, *, request_payload, resolved_by, resolution_note):
            thread = threading.Thread(
                target=lambda: asyncio.run(
                    api_routes._finalize_approved_queue_item_followup_async(
                        item_id,
                        request_payload=request_payload,
                        resolved_by=resolved_by,
                        resolution_note=resolution_note,
                    )
                )
            )
            thread.start()
            thread.join()

        with patch.object(api_routes, "_replay_blocked_tool_queue_item", side_effect=fake_replay), \
             patch.object(api_routes, "_spawn_approval_followup_worker", side_effect=run_followup_now), \
             patch.object(api_routes.pipeline_engine, "instruct", instruct_mock), \
             patch.object(api_routes.pipeline_engine, "resume", resume_mock):
            approved = client.post(
                f"/api/approval-queue/{queue_item_id}/approve",
                json={"note": "Replay and resume pipeline."},
            ).json()

        assert approved["status"] == "approved"
        assert approved["resolution_payload"]["action_taken"] == "queue_resolved_only"

        resolved_detail = _wait_for_task_run_event(
            client,
            task_run_id,
            "approval_queue_item_resolved",
            payload_predicate=lambda payload: payload.get("action_taken") == "tool_replayed",
            timeout_seconds=3.0,
        )
        assert resolved_detail is not None
        instruct_mock.assert_awaited_once()
        resume_mock.assert_awaited_once()
        assert instruct_mock.await_args.args[1] == pipeline_id
        assert resume_mock.await_args.args[1] == pipeline_id

        followup_event = next(
            event
            for event in resolved_detail["events"]
            if event["event_type"] == "approval_queue_item_followup_triggered"
        )
        assert followup_event["payload"]["queue_item_id"] == queue_item_id
        assert followup_event["payload"]["pipeline_id"] == pipeline_id
        resolved_event = next(
            event
            for event in resolved_detail["events"]
            if event["event_type"] == "approval_queue_item_resolved"
            and event["payload"].get("action_taken") == "tool_replayed"
        )
        assert resolved_event["payload"]["action_taken"] == "tool_replayed"

    def test_sandbox_blocked_marks_runtime_card_and_ledger(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        async def mock_sandbox_block_stream(messages, tools=None):
            yield {
                "type": "done",
                "full_content": "",
                "tool_calls": [
                    {
                        "id": "call_escape_workspace",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"file_path\": \"/etc/passwd\", \"encoding\": \"utf-8\"}",
                        },
                    }
                ],
            }
            yield {"type": "content", "delta": "Workspace escape was blocked."}
            yield {"type": "done", "full_content": "Workspace escape was blocked.", "tool_calls": None}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = mock_sandbox_block_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Tool Sandbox Block", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        turn_id = "turn-tool-sandbox-block-1"
        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "read outside workspace", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        assert '"status": "sandbox_blocked"' in stream.text or '"status":"sandbox_blocked"' in stream.text

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        tool_card = next(card for card in cards if card.get("type") == "tool_call")
        assert tool_card["tool"] == "read_file"
        assert tool_card["success"] is False
        assert tool_card["status"] == "sandbox_blocked"
        assert tool_card["blocked"] is True
        assert tool_card["blocked_kind"] == "sandbox"

        runs = client.get(
            f"/api/chatrooms/{cid}/task-runs",
            params={"client_turn_id": turn_id},
        ).json()
        detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
        round_event = next(event for event in detail["events"] if event["event_type"] == "tool_round_recorded")
        blocked_event = next(event for event in detail["events"] if event["event_type"] == "tool_call_blocked")
        assert round_event["payload"]["tool_status_counts"]["sandbox_blocked"] == 1
        assert round_event["payload"]["blocked_tool_count"] == 1
        assert blocked_event["payload"]["tool_name"] == "read_file"
        assert blocked_event["payload"]["blocked_kind"] == "sandbox"
        assert blocked_event["payload"]["status"] == "sandbox_blocked"
        queue_items = client.get(
            "/api/approval-queue",
            params={"task_run_id": runs[0]["id"], "status": "pending"},
        ).json()
        assert len(queue_items) == 1
        assert queue_items[0]["queue_kind"] == "escalation"
        assert queue_items[0]["target_kind"] == "tool"
        assert queue_items[0]["target_name"] == "read_file"
        assert queue_items[0]["status"] == "pending"

        rejected = client.post(
            f"/api/approval-queue/{queue_items[0]['id']}/reject",
            json={"note": "Do not allow workspace escape."},
        ).json()
        assert rejected["status"] == "rejected"
        assert rejected["resolved_by"] == "user"

        resolved_detail = client.get(f"/api/task-runs/{runs[0]['id']}").json()
        assert any(
            event["event_type"] == "approval_queue_item_resolved"
            and event["payload"]["status"] == "rejected"
            for event in resolved_detail["events"]
        )
        assert resolved_detail["approval_queue_items"][0]["status"] == "rejected"

    def test_stream_failure_persists_error_card_and_fallback_message(self, client):
        import llm.client as llm_mod
        import routes.api as api_routes

        call_count = 0

        async def broken_stream(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "type": "done",
                    "full_content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "list_files",
                                "arguments": "{\"directory\": \".\", \"pattern\": \"*PRD*.md\", \"recursive\": true}",
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
                    "finish_reason": "tool_calls",
                    "timings": {"completed_ms": 12},
                }
                return
            raise RuntimeError("synthetic stream crash")

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = broken_stream
        llm_mod._llm_client = mock_llm
        api_routes.get_default_llm_client = lambda: mock_llm
        api_routes.get_llm_client_for_agent = lambda agent_name: mock_llm

        r = client.post("/api/projects", json={"name": "Broken Stream", "agent_names": ["analyst"]})
        cid = r.json()["chatroom_id"]
        turn_id = "turn-stream-failure"

        stream = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@analyst review this", "client_turn_id": turn_id},
        )

        assert stream.status_code == 200
        body = stream.text
        assert '"type": "done"' in body or '"type":"done"' in body

        cards = client.get(f"/api/chatrooms/{cid}/runtime-cards").json()
        messages = client.get(f"/api/chatrooms/{cid}/messages").json()

        assert any(card.get("type") == "tool_call" for card in cards)
        assert any(card.get("type") == "agent_error" for card in cards), cards
        assert any(card.get("client_turn_id") == turn_id for card in cards)
        assert any(
            message.get("agent_name") == "analyst"
            and message.get("client_turn_id") == turn_id
            and "本轮执行中断" in (message.get("content") or "")
            for message in messages
        ), messages


# ==================== 协作 API ====================

class TestCollaborationEndpoints:
    def test_collaboration_status(self, client):
        r = client.get("/api/collaboration/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "active"

    def test_list_collaboration_tasks(self, client):
        r = client.get("/api/collaboration/tasks")
        assert r.status_code == 200
        assert "tasks" in r.json()

    def test_get_task_not_found(self, client):
        r = client.get("/api/collaboration/tasks/nonexistent")
        assert r.status_code == 404

    def test_delegate_task_endpoint_registers_shared_delegated_task(self, client, fresh_db):
        from agents.collaboration import collaboration_coordinator

        collaboration_coordinator.task_registry.clear()
        collaboration_coordinator.collaborators.clear()

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
        finally:
            db.close()

        r = client.post(
            "/api/collaboration/delegate",
            params={
                "target_agent_name": "tester",
                "task_title": "Run tests",
                "task_description": "Run backend tests",
                "chatroom_id": 1,
            },
        )

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "delegated"
        assert data["assigned_to"] == "tester"
        task = collaboration_coordinator.task_registry[data["task_id"]]
        assert task.title == "Run tests"
        assert task.description == "Run backend tests"
        assert task.created_by_agent_id == 0
        assert task.assigned_to_agent_id == agent.id
        assert task.chatroom_id == 1
        assert collaboration_coordinator.collaborators[agent.id].assigned_tasks[data["task_id"]].id == data["task_id"]

    def test_collaboration_status_counts_only_active_tasks(self, client):
        from agents.collaboration import collaboration_coordinator, CollaborationTask, TaskStatus
        import uuid

        collaboration_coordinator.task_registry.clear()
        for status in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.DELEGATED, TaskStatus.COMPLETED]:
            task_id = str(uuid.uuid4())
            collaboration_coordinator.task_registry[task_id] = CollaborationTask(
                id=task_id,
                title=status.value,
                description=status.value,
                status=status,
                created_by_agent_id=1,
                assigned_to_agent_id=2,
                chatroom_id=1,
            )

        r = client.get("/api/collaboration/status")
        assert r.status_code == 200
        data = r.json()
        assert data["pending_tasks"] == 3

    def test_collaboration_task_endpoint_backfills_runtime_result(self, client):
        from agents.collaboration import collaboration_coordinator, CollaborationTask, TaskStatus
        from models.database import SessionLocal, Message, TaskRun
        import uuid
        import json

        task_id = str(uuid.uuid4())
        task = CollaborationTask(
            id=task_id,
            title="Run tests",
            description="Run project tests and report back",
            status=TaskStatus.DELEGATED,
            created_by_agent_id=1,
            assigned_to_agent_id=2,
            chatroom_id=1,
            result="Completed in chat window. See delegated turn output.",
        )
        collaboration_coordinator.task_registry[task_id] = task

        db = SessionLocal()
        try:
            db.add(Message(
                chatroom_id=1,
                agent_id=2,
                content="pytest finished: 12 passed, 1 failed in test_x.",
                message_type="text",
                metadata_json=json.dumps({"client_turn_id": f"delegate-{task_id}"}),
            ))
            db.add(TaskRun(
                chatroom_id=1,
                project_id=None,
                client_turn_id=f"delegate-{task_id}",
                run_kind="project_single_agent",
                status="completed",
                title="Run tests",
                user_request="Run tests",
                initiator="user",
                target_agent_name="tester",
                summary="pytest finished: 12 passed, 1 failed in test_x.",
            ))
            db.commit()
        finally:
            db.close()

        r = client.get(f"/api/collaboration/tasks/{task_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        assert "12 passed, 1 failed" in data["result"]
        assert data["result_details"]["ran_tests"] is True
        assert data["result_details"]["passed_count"] == 12
        assert data["result_details"]["failed_count"] == 1
        assert data["result_details"]["next_step"] == "Inspect failed tests and logs."

    def test_collaboration_task_endpoint_rebuilds_from_runtime_when_registry_is_empty(self, client):
        from agents.collaboration import collaboration_coordinator
        from models.database import SessionLocal, TaskRun
        import uuid

        task_id = str(uuid.uuid4())
        collaboration_coordinator.task_registry.clear()

        db = SessionLocal()
        try:
            db.add(TaskRun(
                chatroom_id=1,
                project_id=None,
                client_turn_id=f"delegate-{task_id}",
                run_kind="project_single_agent",
                status="running",
                title="Run tests",
                user_request="Run tests",
                initiator="user",
                target_agent_name="tester",
                summary="Pytest finished; waiting for delegated finalization.",
            ))
            db.commit()
        finally:
            db.close()

        r = client.get(f"/api/collaboration/tasks/{task_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == task_id
        assert data["status"] == "in_progress"
        assert "waiting for delegated finalization" in data["result"].lower()

    def test_collaboration_task_endpoint_shows_running_followup_activity_after_approval(self, client):
        from agents.collaboration import collaboration_coordinator, CollaborationTask, TaskStatus
        from models.database import SessionLocal, TaskRun, TaskRunEvent, ApprovalQueueItem
        import uuid
        import json

        task_id = str(uuid.uuid4())
        collaboration_coordinator.task_registry[task_id] = CollaborationTask(
            id=task_id,
            title="Run tests",
            description="Run project tests and report back",
            status=TaskStatus.IN_PROGRESS,
            created_by_agent_id=1,
            assigned_to_agent_id=2,
            chatroom_id=1,
            result="Waiting for approval.",
        )

        db = SessionLocal()
        try:
            run = TaskRun(
                chatroom_id=1,
                project_id=None,
                client_turn_id=f"delegate-{task_id}",
                run_kind="project_single_agent",
                status="running",
                title="Run tests",
                user_request="Run tests",
                initiator="user",
                target_agent_name="tester",
            )
            db.add(run)
            db.commit()
            db.refresh(run)

            queue_item = ApprovalQueueItem(
                task_run_id=run.id,
                chatroom_id=1,
                project_id=None,
                queue_kind="approval",
                source="tool_call_blocked",
                title="Approval needed for run_shell",
                summary="Need approval",
                agent_name="Tester",
                target_kind="tool",
                target_name="run_shell",
                status="approved",
                request_key=str(uuid.uuid4()),
                request_payload_json=json.dumps({
                    "tool_name": "run_shell",
                    "arguments": json.dumps({
                        "command": "python -m pytest backend/tests -q --tb=short --disable-warnings",
                        "cwd": "/mnt/c/Users/sun/AI/catown",
                    }),
                }),
            )
            db.add(queue_item)
            db.commit()
            db.refresh(queue_item)

            db.add(TaskRunEvent(
                task_run_id=run.id,
                event_index=1,
                event_type="approval_queue_item_resolved",
                agent_name="Tester",
                summary="Approved queue item for run_shell.",
                payload_json=json.dumps({
                    "queue_item_id": queue_item.id,
                    "target_name": "run_shell",
                    "status": "approved",
                    "action_taken": "queue_resolved_only",
                }),
            ))
            db.commit()
        finally:
            db.close()

        r = client.get(f"/api/collaboration/tasks/{task_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "in_progress"
        assert "python -m pytest backend/tests -q --tb=short --disable-warnings" in data["result"]

    def test_collaboration_task_endpoint_marks_stalled_when_no_live_process_or_recent_activity(self, client):
        from agents.collaboration import collaboration_coordinator, CollaborationTask, TaskStatus
        from models.database import SessionLocal, TaskRun, Message
        from datetime import datetime, timedelta
        import uuid
        import json

        task_id = str(uuid.uuid4())
        collaboration_coordinator.task_registry[task_id] = CollaborationTask(
            id=task_id,
            title="Run tests",
            description="Run project tests and report back",
            status=TaskStatus.IN_PROGRESS,
            created_by_agent_id=1,
            assigned_to_agent_id=2,
            chatroom_id=1,
            result="Task is running.",
            metadata={},
        )

        stale_time = datetime.now() - timedelta(seconds=240)

        db = SessionLocal()
        try:
            run = TaskRun(
                chatroom_id=1,
                project_id=None,
                client_turn_id=f"delegate-{task_id}",
                run_kind="project_single_agent",
                status="running",
                title="Run tests",
                user_request="Run tests",
                initiator="user",
                target_agent_name="tester",
                created_at=stale_time,
                updated_at=stale_time,
            )
            db.add(run)
            db.commit()
            db.refresh(run)

            db.add(Message(
                chatroom_id=1,
                agent_id=None,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({
                    "card": {
                        "type": "tool_call",
                        "agent": "Tester",
                        "tool": "run_shell",
                        "arguments": json.dumps({
                            "command": "python -m pytest backend/tests -q --tb=short --disable-warnings",
                            "cwd": "/mnt/c/Users/sun/AI/catown",
                        }),
                        "status": "running",
                        "result": "....FF....",
                        "duration_ms": 84018,
                        "pid": 99999999,
                        "client_turn_id": f"delegate-{task_id}",
                        "run_id": run.id,
                    }
                }),
                created_at=stale_time,
            ))
            db.commit()
        finally:
            db.close()

        r = client.get(f"/api/collaboration/tasks/{task_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "stalled"
        assert data["result"].lower().startswith("stalled:")
        assert data["result_details"]["phase"] == "stalled"
        assert data["result_details"]["idle_seconds"] >= 120


class TestTaskRunCompactionProjection:
    def test_task_run_detail_surfaces_context_compaction_projection(self, client):
        import models.database as db_mod

        project = client.post("/api/projects", json={"name": "Compaction Detail Projection"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                client_turn_id="turn-compaction-detail",
                run_kind="project_single_agent",
                status="running",
                title="Compaction detail projection",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                db_mod.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="context_compaction",
                    agent_name="analyst",
                    summary="analyst compacted context.",
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
            task_run_id = task_run.id
        finally:
            db.close()

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        compaction_event = next(event for event in detail["events"] if event["event_type"] == "context_compaction")

        assert compaction_event["budget_summary"] == "roles developer 1200 / user 2000 | scopes run 1800 / turn 400"
        assert "run 2/3 fragments, 1400/2200 tokens" in compaction_event["scope_usage_summary"]
        assert "Candidates 9 -> selected 7" in compaction_event["detail_summary"]
        assert compaction_event["max_tokens_by_scope"] == {"run": 1800, "turn": 400}
        assert detail["checkpoint_snapshot"]["latest_compaction"]["detail_summary"] == compaction_event["detail_summary"]

    def test_task_run_detail_surfaces_handoff_and_runtime_projection(self, client):
        import models.database as db_mod

        project = client.post("/api/projects", json={"name": "Runtime Event Projection"}).json()
        chatroom_id = project["chatroom_id"]

        db = db_mod.SessionLocal()
        try:
            task_run = db_mod.TaskRun(
                chatroom_id=chatroom_id,
                project_id=project["id"],
                client_turn_id="turn-runtime-detail",
                run_kind="project_orchestration",
                status="running",
                title="Runtime projection",
            )
            db.add(task_run)
            db.commit()
            db.refresh(task_run)

            db.add(
                db_mod.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="handoff_created",
                    agent_name="planner",
                    summary="planner handed work to developer.",
                    payload_json=json.dumps(
                        {
                            "from_agent": "planner",
                            "to_agent": "developer",
                            "from_step_id": "step-1",
                            "to_step_id": "step-2",
                            "attached_to_step_id": "step-1",
                            "dispatch_kind": "sidecar",
                            "content_preview": "Implement the API route.",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                db_mod.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_plan_created",
                    agent_name="planner",
                    summary="scheduler plan created.",
                    payload_json=json.dumps(
                        {
                            "mode": "parallel_fanout",
                            "step_count": 2,
                            "blocking_step_count": 1,
                            "sidecar_step_count": 1,
                            "sidecar_agent_types": ["developer"],
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "requested_name": "Planner",
                                    "agent_name": "planner",
                                    "agent_type": "planner",
                                    "dispatch_kind": "blocking",
                                    "status": "planned",
                                },
                                {
                                    "step_id": "step-2",
                                    "position": 2,
                                    "requested_name": "Developer",
                                    "agent_name": "developer",
                                    "agent_type": "developer",
                                    "dispatch_kind": "sidecar",
                                    "status": "planned",
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.add(
                db_mod.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=3,
                    event_type="scheduler_runtime_updated",
                    agent_name="planner",
                    summary="scheduler runtime updated.",
                    payload_json=json.dumps(
                        {
                            "runtime": {
                                "completed_step_count": 1,
                                "ready_step_count": 2,
                                "running_step_count": 1,
                                "waiting_step_count": 0,
                                "step_count": 4,
                            }
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
            task_run_id = task_run.id
        finally:
            db.close()

        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        handoff_event = next(event for event in detail["events"] if event["event_type"] == "handoff_created")
        schedule_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_plan_created")
        runtime_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_runtime_updated")

        assert handoff_event["from_agent"] == "planner"
        assert handoff_event["to_agent"] == "developer"
        assert handoff_event["dispatch_kind"] == "sidecar"
        assert handoff_event["content_preview"] == "Implement the API route."
        assert schedule_event["schedule_mode"] == "parallel_fanout"
        assert schedule_event["schedule_step_count"] == 2
        assert schedule_event["schedule_sidecar_agent_types"] == ["developer"]
        assert len(schedule_event["schedule_steps"]) == 2
        assert runtime_event["runtime_snapshot"]["completed_step_count"] == 1
        assert runtime_event["runtime_snapshot"]["ready_step_count"] == 2


# ==================== 多 Agent 流水线 ====================

class TestMultiAgentPipeline:
    def test_multi_mention_stream(self, client):
        r = client.post("/api/projects", json={
            "name": "PipelineTest",
            "agent_names": ["analyst", "developer"]
        })
        cid = r.json()["chatroom_id"]
        r2 = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "@assistant @coder build a hello world"}
        )
        body = r2.text
        assert "data:" in body
        assert "collab_start" in body or "user_saved" in body
