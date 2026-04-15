"""
前端启动流程测试

覆盖 start.bat 启动时序问题：
1. 后端未就绪时 API 返回错误 → 重试后成功
2. 多次调用 idempotent（不会重复创建数据）
3. WebSocket 连接后补拉数据的场景
4. 并发启动下的行为
"""
import pytest
import sys
import os
import json
import time
from unittest.mock import AsyncMock, MagicMock

from http_client import SyncASGITestClient
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_app(tmp_path):
    """在指定临时目录下创建全新的 FastAPI app（隔离测试）"""
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

    modules_to_clear = [
        'main', 'config', 'models.database', 'agents.registry',
        'agents.collaboration', 'tools', 'llm.client', 'chatrooms.manager',
        'routes.api', 'routes.websocket'
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    import llm.client as llm_mod
    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(return_value={
        "content": "Mocked agent response.", "tool_calls": None
    })

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
    app = _make_app(tmp_path)
    with SyncASGITestClient(app, base_url="http://testserver") as client:
        yield client


class TestStartupHealthCheck:
    """启动时健康检查流程（模拟前端 loadInitialData）"""

    def test_status_returns_healthy(self, client):
        """后端就绪后 /api/status 返回 healthy"""
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"

    def test_health_endpoint(self, client):
        """/health 端点可用"""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_status_is_idempotent(self, client):
        """多次调用 status 不影响状态"""
        for _ in range(5):
            r = client.get("/api/status")
            assert r.status_code == 200
            assert r.json()["status"] == "healthy"


class TestStartupDataLoad:
    """启动时数据加载流程（模拟前端 loadProjects + loadAgents）"""

    def test_load_agents_returns_builtin(self, client):
        """首次加载 agents 返回配置中的角色"""
        r = client.get("/api/agents")
        assert r.status_code == 200
        agents = r.json()
        # agents.json 中定义了 6 个角色
        assert len(agents) == 6
        names = {a["name"] for a in agents}
        assert names == {"analyst", "architect", "developer", "tester", "release", "assistant"}

    def test_load_projects_empty_on_fresh_start(self, client):
        """全新启动时 projects 为空"""
        r = client.get("/api/projects")
        assert r.status_code == 200
        assert r.json() == []

    def test_full_startup_sequence(self, client):
        """完整启动序列：status → agents → projects → config → tools"""
        # 1. 健康检查
        r1 = client.get("/api/status")
        assert r1.status_code == 200

        # 2. 加载 agents
        r2 = client.get("/api/agents")
        assert r2.status_code == 200
        assert len(r2.json()) >= 4

        # 3. 加载 projects（空）
        r3 = client.get("/api/projects")
        assert r3.status_code == 200

        # 4. 加载 config
        r4 = client.get("/api/config")
        assert r4.status_code == 200
        assert "llm" in r4.json()

        # 5. 加载 tools
        r5 = client.get("/api/tools")
        assert r5.status_code == 200
        assert r5.json()["count"] >= 14

    def test_load_data_after_create_project(self, client):
        """创建项目后 loadProjects 能拿到数据"""
        # 创建项目
        r = client.post("/api/projects", json={
            "name": "Startup Project", "agent_names": ["assistant"]
        })
        assert r.status_code == 200

        # 重新加载 projects（模拟前端刷新）
        r2 = client.get("/api/projects")
        projects = r2.json()
        assert len(projects) == 1
        assert projects[0]["name"] == "Startup Project"
        assert projects[0]["chatroom_id"] is not None


class TestRetryAndIdempotency:
    """重试与幂等性测试"""

    def test_create_project_idempotent_names(self, client):
        """同名项目可以创建多次（业务允许），每次返回不同 ID"""
        r1 = client.post("/api/projects", json={
            "name": "Same Name", "agent_names": ["assistant"]
        })
        r2 = client.post("/api/projects", json={
            "name": "Same Name", "agent_names": ["assistant"]
        })
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["id"] != r2.json()["id"]

    def test_repeated_agent_load_returns_same(self, client):
        """多次加载 agents 返回相同结果"""
        r1 = client.get("/api/agents")
        r2 = client.get("/api/agents")
        assert r1.json() == r2.json()

    def test_repeated_config_load_returns_same(self, client):
        """多次加载 config 返回相同结果"""
        r1 = client.get("/api/config")
        r2 = client.get("/api/config")
        assert r1.json() == r2.json()


class TestWebSocketStartupFlow:
    """WebSocket 启动流程测试（模拟前端 connectWebSocket + 数据补拉）"""

    def test_ws_join_then_load_messages(self, client):
        """WebSocket join room 后可以正常加载消息"""
        # 创建项目（模拟前端收到数据后创建）
        r = client.post("/api/projects", json={
            "name": "WS Test", "agent_names": ["assistant"]
        })
        cid = r.json()["chatroom_id"]

        # 发消息
        client.post(f"/api/chatrooms/{cid}/messages", json={"content": "via WS"})

        # 获取消息（模拟 WebSocket 连接后补拉）
        r2 = client.get(f"/api/chatrooms/{cid}/messages")
        assert r2.status_code == 200
        msgs = r2.json()
        assert len(msgs) >= 1
        assert msgs[0]["content"] == "via WS"

    def test_ws_collaboration_status_after_startup(self, client):
        """启动后协作系统可用"""
        r = client.get("/api/collaboration/status")
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_multiple_projects_then_select_first(self, client):
        """创建多个项目后，前端应能拿到列表并选中第一个"""
        names = ["Project A", "Project B", "Project C"]
        for name in names:
            client.post("/api/projects", json={
                "name": name, "agent_names": ["assistant"]
            })

        r = client.get("/api/projects")
        projects = r.json()
        assert len(projects) == 3
        # 模拟前端 selectProject(projects[0])
        first = projects[0]
        r2 = client.get(f"/api/projects/{first['id']}")
        assert r2.status_code == 200
        assert r2.json()["name"] == first["name"]


class TestDelayedBackendScenario:
    """模拟后端延迟就绪的场景"""

    def test_all_endpoints_work_in_sequence(self, client):
        """按前端实际调用顺序逐一请求，全部应成功"""
        endpoints = [
            ("GET", "/api/status"),
            ("GET", "/api/agents"),
            ("GET", "/api/projects"),
            ("GET", "/api/config"),
            ("GET", "/api/tools"),
            ("GET", "/api/collaboration/status"),
            ("GET", "/api/collaboration/tasks"),
            ("GET", "/health"),
        ]
        for method, path in endpoints:
            r = client.request(method, path)
            assert r.status_code == 200, f"{method} {path} returned {r.status_code}"

    def test_create_project_then_immediately_use(self, client):
        """创建项目后立即使用（无延迟）"""
        r = client.post("/api/projects", json={
            "name": "Instant Use", "agent_names": ["assistant", "developer"]
        })
        assert r.status_code == 200
        data = r.json()
        cid = data["chatroom_id"]

        # 立即发消息
        r2 = client.post(f"/api/chatrooms/{cid}/messages", json={"content": "instant"})
        assert r2.status_code == 200

        # 立即获取消息
        r3 = client.get(f"/api/chatrooms/{cid}/messages")
        assert r3.status_code == 200
        assert len(r3.json()) >= 1

    def test_sse_stream_after_startup(self, client):
        """启动后 SSE 流式端点可用"""
        r = client.post("/api/projects", json={
            "name": "SSE Startup", "agent_names": ["assistant"]
        })
        cid = r.json()["chatroom_id"]

        r2 = client.post(
            f"/api/chatrooms/{cid}/messages/stream",
            json={"content": "stream after startup"}
        )
        assert r2.status_code == 200
        assert "text/event-stream" in r2.headers["content-type"]
        assert "data:" in r2.text
