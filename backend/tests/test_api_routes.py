"""
API 路由测试

使用 FastAPI TestClient 测试 REST 端点（mock LLM）
"""
import pytest
import sys
import os
import json
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock
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
            client = TestClient(_make_app(tmp_path), base_url="http://testserver")

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
            agent = refreshed["agents"]["assistant"]
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
        assert data["agents"][0]["name"] == "assistant"
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
        assert data["agents"][0]["name"] == "assistant"
        assert os.path.isdir(data["workspace_path"])

        listed_chats = client.get("/api/chats").json()
        assert any(row["id"] == chat["id"] for row in listed_chats)
        assert all(row["id"] != data["default_chatroom_id"] for row in listed_chats)

        messages = client.get(f"/api/chatrooms/{data['default_chatroom_id']}/messages").json()
        assert any("created from standalone chat" in message["content"] for message in messages)
        assert any(message["content"] == "Carry this into the project" for message in messages)

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
        assert [agent["name"] for agent in r.json()["agents"]] == ["assistant"]

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
