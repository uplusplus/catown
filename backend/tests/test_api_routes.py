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
    return TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"})


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
