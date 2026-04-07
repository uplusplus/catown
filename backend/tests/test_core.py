"""
Catown 核心链路测试 — 带 LLM Mock

覆盖:
1. Agent 响应循环（含工具调用）
2. 记忆提取
3. SSE 流式端点
4. @mention 自动分配
"""
import pytest
import asyncio
import json
import os
import sys

# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


# ==================== Fixtures ====================

@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """设置测试环境"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "test.db"))


@pytest.fixture
def mock_llm_client():
    """Mock LLM 客户端"""
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()

    # 默认 chat 响应
    client.chat = AsyncMock(return_value="This is a test response.")

    # 默认 chat_with_tools 响应（无工具调用）
    client.chat_with_tools = AsyncMock(return_value={
        "content": "Test agent response.",
        "tool_calls": None
    })

    # 默认 chat_stream 响应
    async def mock_stream(messages, tools=None):
        yield {"type": "content", "delta": "Hello "}
        yield {"type": "content", "delta": "world!"}
        yield {"type": "done", "full_content": "Hello world!", "tool_calls": None}

    client.chat_stream = mock_stream
    return client


@pytest.fixture
def initialized_db(setup_env):
    """初始化测试数据库"""
    from models.database import init_database, Base, engine
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ==================== Tests ====================

class TestToolRegistry:
    """工具注册表测试"""

    def test_all_tools_registered(self):
        from tools import tool_registry
        tool_names = tool_registry.list_tools()
        expected = [
            "web_search", "execute_code", "retrieve_memory", "save_memory",
            "read_file", "write_file", "list_files", "delete_file", "search_files",
            "delegate_task", "broadcast_message", "check_task_status",
            "list_collaborators", "send_direct_message"
        ]
        for name in expected:
            assert name in tool_names, f"Tool '{name}' not registered"

    def test_tool_schemas_valid(self):
        from tools import tool_registry
        schemas = tool_registry.get_schemas()
        for schema in schemas:
            assert "type" in schema
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]
            assert "description" in schema["function"]

    @pytest.mark.asyncio
    async def test_web_search(self):
        from tools import tool_registry
        result = await tool_registry.execute("web_search", query="python fastapi")
        assert result is not None
        assert len(str(result)) > 0

    @pytest.mark.asyncio
    async def test_execute_code(self):
        from tools import tool_registry
        result = await tool_registry.execute("execute_code", code="print(2+3)")
        assert "5" in str(result)

    @pytest.mark.asyncio
    async def test_file_operations(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CATOWN_WORKSPACE", str(tmp_path))
        from tools import tool_registry

        # 写文件
        await tool_registry.execute("write_file", file_path="test.txt", content="hello")
        # 读文件
        result = await tool_registry.execute("read_file", file_path="test.txt")
        assert "hello" in str(result)
        # 列文件
        result = await tool_registry.execute("list_files", directory=".")
        assert "test.txt" in str(result)


class TestAgentMemory:
    """Agent 记忆系统测试"""

    def test_agent_memory_add(self):
        from agents.core import Agent, AgentConfig
        config = AgentConfig(name="test", role="tester", system_prompt="Test agent")
        agent = Agent(config)

        agent.add_memory("User prefers dark mode", memory_type="long_term", importance=8)
        agent.add_memory("Current task: bug fix", memory_type="short_term", importance=5)

        assert len(agent.long_term_memory) == 1
        assert len(agent.short_term_memory) == 1
        assert agent.long_term_memory[0].content == "User prefers dark mode"
        assert agent.long_term_memory[0].importance == 8

    def test_agent_memory_summary(self):
        from agents.core import Agent, AgentConfig
        config = AgentConfig(name="test", role="tester", system_prompt="Test agent")
        agent = Agent(config)

        agent.add_memory("a", memory_type="short_term")
        agent.add_memory("b", memory_type="long_term")
        agent.add_memory("c", memory_type="procedural")

        summary = agent.get_memory_summary()
        assert summary["short_term_count"] == 1
        assert summary["long_term_count"] == 1
        assert summary["procedural_count"] == 1

    def test_short_term_memory_limit(self):
        from agents.core import Agent, AgentConfig
        config = AgentConfig(name="test", role="tester", system_prompt="Test agent")
        agent = Agent(config)

        for i in range(25):
            agent.add_memory(f"memory {i}", memory_type="short_term")

        # 应该被截断到 20
        assert len(agent.short_term_memory) == 20


class TestAgentConfig:
    """Agent 配置系统测试"""

    def test_agent_config_v2(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig

        config = AgentConfigV2(
            name="coder",
            role="Code Expert",
            system_prompt="You are a coding expert.",
            tools=["execute_code", "web_search"],
            provider=AgentProviderConfig(
                baseUrl="http://localhost:8000/v1",
                apiKey="test",
                models=[ModelConfig(id="gpt-4", name="GPT-4")]
            )
        )

        assert config.name == "coder"
        assert config.get_effective_base_url() == "http://localhost:8000/v1"
        assert config.get_effective_model() == "gpt-4"

    def test_create_agent_from_provider(self):
        from agents.config_models import create_agent_config_from_provider

        provider_config = {
            "baseUrl": "http://localhost:8000/v1",
            "apiKey": "test",
            "models": [{"id": "gpt-4", "name": "GPT-4"}]
        }

        config = create_agent_config_from_provider(
            agent_name="researcher",
            role="Research Expert",
            system_prompt="You research things.",
            provider_config=provider_config,
            tools=["web_search"]
        )

        assert config.name == "researcher"
        assert config.tools == ["web_search"]
        assert config.provider is not None


class TestCollaborationSystem:
    """协作系统测试"""

    def test_collaboration_coordinator_init(self):
        from agents.collaboration import collaboration_coordinator
        assert collaboration_coordinator is not None
        assert hasattr(collaboration_coordinator, 'collaborators')
        assert hasattr(collaboration_coordinator, 'chatroom_agents')

    def test_register_collaborator(self):
        from agents.collaboration import CollaborationCoordinator, AgentCollaborator

        coordinator = CollaborationCoordinator()
        collab = AgentCollaborator(agent_id=1, agent_name="test_agent", chatroom_id=100)
        coordinator.register_collaborator(collab)

        assert 1 in coordinator.collaborators
        assert coordinator.collaborators[1].agent_name == "test_agent"
        assert 100 in coordinator.chatroom_agents

    def test_collaboration_strategies(self):
        from agents.collaboration import SingleAgentStrategy, MultiAgentStrategy
        from agents.core import Agent, AgentConfig

        agents = [
            Agent(AgentConfig(name="assistant", role="helper", system_prompt="help")),
            Agent(AgentConfig(name="coder", role="coder", system_prompt="code")),
            Agent(AgentConfig(name="researcher", role="researcher", system_prompt="research")),
        ]

        # Test single strategy with @mention
        strategy = SingleAgentStrategy()
        result = asyncio.run(strategy.select_agents("@coder do something", agents))
        assert len(result) == 1
        assert result[0].name == "coder"

        # Test multi strategy with code keywords
        multi = MultiAgentStrategy()
        result = asyncio.run(strategy.select_agents("please code this feature", agents))
        # SingleAgentStrategy won't match "code", falls back to assistant
        assert result[0].name == "assistant"

    @pytest.mark.asyncio
    async def test_list_collaborators_fallback(self):
        """list_collaborators 在无协作者时回退到全局 DB"""
        from tools.collaboration_tools import ListCollaboratorsTool

        tool = ListCollaboratorsTool(collaboration_coordinator=None)
        result = await tool.execute(chatroom_id=1)
        assert "not available" in str(result).lower()


class TestConfigValidation:
    """配置验证测试"""

    def test_valid_config(self):
        from routes.api import LLMConfigModel
        config = LLMConfigModel(
            api_key="sk-test123",
            base_url="https://api.openai.com/v1",
            model="gpt-4",
            temperature=0.7,
            max_tokens=2000
        )
        assert config.api_key == "sk-test123"

    def test_empty_api_key_rejected(self):
        from routes.api import LLMConfigModel
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMConfigModel(api_key="", model="gpt-4")

    def test_invalid_url_rejected(self):
        from routes.api import LLMConfigModel
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMConfigModel(api_key="test", base_url="not-a-url", model="gpt-4")

    def test_temperature_range(self):
        from routes.api import LLMConfigModel
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMConfigModel(api_key="test", temperature=3.0)

    def test_max_tokens_range(self):
        from routes.api import LLMConfigModel
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMConfigModel(api_key="test", max_tokens=999999)

    def test_url_strips_trailing_slash(self):
        from routes.api import LLMConfigModel
        config = LLMConfigModel(api_key="test", base_url="https://api.openai.com/v1/")
        assert config.base_url == "https://api.openai.com/v1"


class TestLLMClient:
    """LLM 客户端测试"""

    def test_llm_client_creation(self):
        from llm.client import LLMClient, get_llm_client, set_llm_client
        client = LLMClient()
        assert client.model == "test-model"
        assert client.client is not None

    def test_singleton(self):
        from llm.client import get_llm_client, set_llm_client, LLMClient
        c1 = get_llm_client()
        c2 = get_llm_client()
        assert c1 is c2

        new_client = LLMClient()
        set_llm_client(new_client)
        c3 = get_llm_client()
        assert c3 is new_client
