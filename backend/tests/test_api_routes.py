"""
API 路由测试

使用 FastAPI TestClient 测试 REST 端点（mock LLM）
"""
import pytest
import sys
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch
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
        'routes.api', 'routes.websocket'
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

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
    return TestClient(app, base_url="http://testserver")


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
        for expected in ["analyst", "developer", "tester", "architect"]:
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


# ==================== 工具 API ====================

class TestToolsEndpoint:
    def test_list_tools(self, client):
        r = client.get("/api/tools")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 14
        tool_names = [t["name"] for t in data["tools"]]
        for t in ["web_search", "execute_code", "delegate_task", "save_memory", "read_file"]:
            assert t in tool_names, f"Missing tool: {t}"


# ==================== 配置 API ====================

class TestConfigEndpoint:
    def test_get_config(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "llm" in data
        assert "server" in data

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
            "name": "Test Project", "description": "A test", "agent_names": ["analyst"]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Test Project"
        assert data["chatroom_id"] is not None
        assert len(data["agents"]) == 1

    def test_create_project_invalid_agent(self, client):
        r = client.post("/api/projects", json={
            "name": "Bad", "agent_names": ["nonexistent"]
        })
        assert r.status_code == 400

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


# ==================== 聊天 API ====================

class TestProjectV2Endpoints:
    def test_create_project_v2_bootstraps_brief_and_decision(self, client):
        r = client.post("/api/v2/projects", json={
            "name": "FitPet",
            "one_line_vision": "Help pet owners manage feeding and exercise",
            "target_platforms": ["ios", "android"],
            "target_users": ["pet owners"],
            "references": ["https://example.com/reference"]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["project"]["status"] == "draft"
        assert data["project"]["current_stage"] == "briefing"

        project_id = data["project"]["id"]
        assets = client.get(f"/api/v2/projects/{project_id}/assets")
        assert assets.status_code == 200
        assert len(assets.json()) == 1
        assert assets.json()[0]["asset_type"] == "project_brief"
        assert assets.json()[0]["status"] == "in_review"

        decisions = client.get(f"/api/v2/projects/{project_id}/decisions")
        assert decisions.status_code == 200
        assert len(decisions.json()) == 1
        assert decisions.json()[0]["decision_type"] == "scope_confirmation"
        assert decisions.json()[0]["status"] == "pending"

        stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs")
        assert stage_runs.status_code == 200
        assert len(stage_runs.json()) == 1
        assert stage_runs.json()[0]["stage_type"] == "briefing"

    def test_resolve_scope_confirmation_moves_project_forward(self, client):
        project = client.post("/api/v2/projects", json={
            "name": "CatPlan",
            "one_line_vision": "Ship an MVP cat social planner"
        }).json()["project"]
        project_id = project["id"]
        decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]

        resolved = client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={
            "resolution": "approved",
            "selected_option": "approve_brief_v1",
            "note": "Looks good"
        })
        assert resolved.status_code == 200
        payload = resolved.json()
        assert payload["decision"]["status"] == "approved"
        assert payload["project"]["status"] == "brief_confirmed"
        assert payload["project"]["current_stage"] == "product_definition"

        assets = client.get(f"/api/v2/projects/{project_id}/assets").json()
        assert assets[0]["status"] == "approved"

        stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs").json()
        stage_types = [item["stage_type"] for item in stage_runs]
        assert "briefing" in stage_types
        assert "product_definition" in stage_types

    def test_continue_project_promotes_queued_stage_run(self, client):
        project = client.post("/api/v2/projects", json={
            "name": "ContinueMe",
            "one_line_vision": "Validate continue flow"
        }).json()["project"]
        project_id = project["id"]
        decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]
        client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={
            "resolution": "approved"
        })

        continued = client.post(f"/api/v2/projects/{project_id}/continue")
        assert continued.status_code == 200
        payload = continued.json()
        assert payload["project"]["status"] == "defining"
        assert payload["project"]["current_focus"] == "Review PRD, UX blueprint, and tech spec scaffolds"
        assert payload["stage_run"]["stage_type"] == "product_definition"
        assert payload["stage_run"]["status"] == "completed"

        assets = client.get(f"/api/v2/projects/{project_id}/assets").json()
        asset_types = {item["asset_type"] for item in assets}
        assert {"prd", "ux_blueprint", "tech_spec"}.issubset(asset_types)

        stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs").json()
        assert any(item["stage_type"] == "build_execution" and item["status"] == "queued" for item in stage_runs)

    def test_project_overview_v2_returns_mission_board_shape(self, client):
        project = client.post("/api/v2/projects", json={
            "name": "Overview",
            "one_line_vision": "Validate overview aggregation"
        }).json()["project"]
        project_id = project["id"]
        decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]
        client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={"resolution": "approved"})
        client.post(f"/api/v2/projects/{project_id}/continue")

        overview = client.get(f"/api/v2/projects/{project_id}/overview")
        assert overview.status_code == 200
        data = overview.json()
        assert data["project"]["id"] == project_id
        assert "assets_by_type" in data
        assert "project_brief" in data["assets_by_type"]
        assert "prd" in data["assets_by_type"]
        assert "ux_blueprint" in data["assets_by_type"]
        assert "tech_spec" in data["assets_by_type"]
        assert data["stage_summary"]["completed"] >= 1
        assert data["release_readiness"]["status"] == "in_definition"
        assert data["recommended_next_action"] == "continue_project"
        assert any(link["asset_type"] == "prd" and link["direction"] == "output" for link in data["stage_asset_links"])

    def test_release_ready_chain_generates_build_test_and_release_assets(self, client):
        project = client.post("/api/v2/projects", json={
            "name": "Planner",
            "one_line_vision": "Validate release-ready chain"
        }).json()["project"]
        project_id = project["id"]
        decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]
        client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={"resolution": "approved"})

        step1 = client.post(f"/api/v2/projects/{project_id}/continue")
        assert step1.status_code == 200
        overview1 = client.get(f"/api/v2/projects/{project_id}/overview").json()
        assert overview1["recommended_next_action"] == "continue_project"

        step2 = client.post(f"/api/v2/projects/{project_id}/continue")
        assert step2.status_code == 200
        overview2 = client.get(f"/api/v2/projects/{project_id}/overview").json()
        assert "task_plan" in overview2["assets_by_type"]
        assert "build_artifact" in overview2["assets_by_type"]
        assert overview2["recommended_next_action"] == "continue_project"
        assert overview2["release_readiness"]["status"] == "in_build"

        step3 = client.post(f"/api/v2/projects/{project_id}/continue")
        assert step3.status_code == 200
        overview3 = client.get(f"/api/v2/projects/{project_id}/overview").json()
        assert "test_report" in overview3["assets_by_type"]
        assert overview3["recommended_next_action"] == "continue_project"
        assert overview3["release_readiness"]["status"] == "qa_complete"

        step4 = client.post(f"/api/v2/projects/{project_id}/continue")
        assert step4.status_code == 200
        payload = step4.json()
        assert payload["project"]["status"] == "release_ready"
        assert payload["stage_run"]["stage_type"] == "release_preparation"
        assert payload["stage_run"]["status"] == "completed"

        overview4 = client.get(f"/api/v2/projects/{project_id}/overview").json()
        assert "release_pack" in overview4["assets_by_type"]
        assert overview4["recommended_next_action"] == "resolve_release_approval"
        assert overview4["release_readiness"]["status"] == "awaiting_release_approval"
        assert overview4["release_readiness"]["next_gate"] == "release_approval"
        assert any(item["decision_type"] == "release_approval" and item["status"] == "pending" for item in overview4["pending_decisions"])
        assert any(link["decision_type"] == "release_approval" and link["asset_type"] == "release_pack" for link in overview4["decision_asset_links"])

    def test_release_approval_marks_project_released(self, client):
        project = client.post("/api/v2/projects", json={
            "name": "ReleaseMe",
            "one_line_vision": "Validate release approval flow"
        }).json()["project"]
        project_id = project["id"]
        decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]
        client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={"resolution": "approved"})
        for _ in range(4):
            client.post(f"/api/v2/projects/{project_id}/continue")

        decisions = client.get(f"/api/v2/projects/{project_id}/decisions").json()
        release_decision = next(item for item in decisions if item["decision_type"] == "release_approval")
        resolved = client.post(f"/api/v2/decisions/{release_decision['id']}/resolve", json={"resolution": "approved"})
        assert resolved.status_code == 200
        payload = resolved.json()
        assert payload["project"]["status"] == "released"
        assert payload["decision"]["status"] == "approved"

        overview = client.get(f"/api/v2/projects/{project_id}/overview").json()
        assert overview["project"]["status"] == "released"
        assert overview["assets_by_type"]["release_pack"]["status"] == "approved"

        release_pack_id = overview["assets_by_type"]["release_pack"]["id"]
        asset_detail = client.get(f"/api/v2/assets/{release_pack_id}")
        assert asset_detail.status_code == 200
        asset_data = asset_detail.json()
        assert any(link["relation_role"] == "approval_target" for link in asset_data["decision_links"])
        assert any(link["direction"] == "output" for link in asset_data["stage_links"])

    def test_stage_run_detail_returns_inputs_outputs_and_decisions(self, client):
        project = client.post("/api/v2/projects", json={
            "name": "StageDetail",
            "one_line_vision": "Validate stage detail"
        }).json()["project"]
        project_id = project["id"]
        decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]
        client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={"resolution": "approved"})
        client.post(f"/api/v2/projects/{project_id}/continue")

        stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs").json()
        product_definition = next(item for item in stage_runs if item["stage_type"] == "product_definition")
        detail = client.get(f"/api/v2/stage-runs/{product_definition['id']}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["stage_run"]["stage_type"] == "product_definition"
        assert any(asset["asset_type"] == "project_brief" for asset in data["input_assets"])
        assert any(asset["asset_type"] == "prd" for asset in data["output_assets"])

    def test_dashboard_v2_returns_project_first_view(self, client):
        client.post("/api/v2/projects", json={
            "name": "Dash",
            "one_line_vision": "Validate dashboard aggregation"
        })
        r = client.get("/api/v2/dashboard")
        assert r.status_code == 200
        data = r.json()
        assert "projects" in data
        assert "project_cards" in data
        assert "summary" in data
        assert "pending_decisions" in data
        assert len(data["projects"]) >= 1
        assert len(data["project_cards"]) >= 1
        assert "release_readiness" in data["project_cards"][0]
        assert "recommended_next_action" in data["project_cards"][0]


class TestChatEndpoints:
    def test_send_message(self, client):
        r = client.post("/api/projects", json={
            "name": "ChatTest", "agent_names": ["analyst"]
        })
        cid = r.json()["chatroom_id"]
        r2 = client.post(f"/api/chatrooms/{cid}/messages", json={"content": "Hello!"})
        assert r2.status_code == 200

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
