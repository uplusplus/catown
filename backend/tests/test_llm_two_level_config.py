"""
LLM 两级配置测试

覆盖：
1. Agent 自身 provider → 直接使用
2. Agent 无 provider → fallback 到 global_llm
3. global_llm 也无 → fallback 到环境变量
4. API 端点：PUT /config/global, PUT /config/agent/{name}
5. GET /config 返回 source 字段
"""
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def agents_config_file(tmp_path):
    """创建临时 agents.json 配置文件"""
    config = {
        "global_llm": {
            "provider": {
                "baseUrl": "http://global-api.example.com/v1",
                "apiKey": "global-key-123",
                "models": [
                    {"id": "global-model-7b", "name": "Global Model 7B"}
                ]
            },
            "default_model": "global-model-7b"
        },
        "agents": {
            "analyst": {
                "role": "分析师",
                "system_prompt": "You are an analyst.",
                "tools": ["read_file", "write_file"],
                "default_model": "analyst-model",
                "provider": {
                    "baseUrl": "http://analyst-api.example.com/v1",
                    "apiKey": "analyst-key",
                    "models": [{"id": "analyst-model", "name": "Analyst Model"}]
                }
            },
            "developer": {
                "role": "开发者",
                "system_prompt": "You are a developer.",
                "tools": ["read_file", "write_file", "execute_code"],
                "default_model": "",
                "provider": {}  # 无自身配置，应 fallback 到 global
            },
            "tester": {
                "role": "测试员",
                "system_prompt": "You are a tester.",
                "tools": ["read_file", "execute_code"],
                # 无 provider 字段
            }
        }
    }
    config_file = tmp_path / "agents.json"
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f)
    return str(config_file)


class TestLoadAgentProvider:
    """_load_agent_provider 两级查找测试"""

    def test_agent_with_own_provider(self, agents_config_file, monkeypatch):
        """有自身 provider 的 Agent 直接使用自身配置"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        # 重新加载 config 模块以获取新的 settings
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_agent_provider
        result = _load_agent_provider("analyst")

        assert result is not None
        assert result["base_url"] == "http://analyst-api.example.com/v1"
        assert result["api_key"] == "analyst-key"
        assert result["model"] == "analyst-model"

    def test_agent_empty_provider_fallback_global(self, agents_config_file, monkeypatch):
        """provider 为空的 Agent fallback 到 global_llm"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_agent_provider
        result = _load_agent_provider("developer")

        assert result is not None
        assert result["base_url"] == "http://global-api.example.com/v1"
        assert result["api_key"] == "global-key-123"
        assert result["model"] == "global-model-7b"

    def test_agent_no_provider_fallback_global(self, agents_config_file, monkeypatch):
        """没有 provider 字段的 Agent fallback 到 global_llm"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_agent_provider
        result = _load_agent_provider("tester")

        assert result is not None
        assert result["base_url"] == "http://global-api.example.com/v1"
        assert result["model"] == "global-model-7b"

    def test_unknown_agent_fallback_global(self, agents_config_file, monkeypatch):
        """不存在的 Agent 也 fallback 到 global_llm"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_agent_provider
        result = _load_agent_provider("nonexistent_agent")

        assert result is not None
        assert result["base_url"] == "http://global-api.example.com/v1"

    def test_no_config_file_returns_none(self, tmp_path, monkeypatch):
        """配置文件不存在时返回 None"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "nonexistent.json"))
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_agent_provider
        result = _load_agent_provider("analyst")
        assert result is None


class TestLoadGlobalProvider:
    """_load_global_provider 测试"""

    def test_load_global_provider(self, agents_config_file, monkeypatch):
        """正确加载 global_llm 配置"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_global_provider
        result = _load_global_provider()

        assert result is not None
        assert result["base_url"] == "http://global-api.example.com/v1"
        assert result["api_key"] == "global-key-123"
        assert result["model"] == "global-model-7b"

    def test_global_provider_from_data(self, tmp_path):
        """从传入的 data 字典加载"""
        data = {
            "global_llm": {
                "provider": {
                    "baseUrl": "http://test.com/v1",
                    "apiKey": "key",
                    "models": [{"id": "model-a"}]
                },
                "default_model": "model-a"
            }
        }
        config_file = tmp_path / "test_agents.json"
        with open(config_file, 'w') as f:
            json.dump(data, f)

        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        os.environ["AGENT_CONFIG_FILE"] = str(config_file)
        from llm.client import _load_global_provider
        result = _load_global_provider(data)

        assert result["base_url"] == "http://test.com/v1"
        assert result["model"] == "model-a"

    def test_no_global_llm_returns_none(self, tmp_path, monkeypatch):
        """没有 global_llm 段时返回 None"""
        data = {"agents": {}}
        config_file = tmp_path / "no_global.json"
        with open(config_file, 'w') as f:
            json.dump(data, f)

        monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_global_provider
        result = _load_global_provider()
        assert result is None


class TestGetFirstProvider:
    """_get_first_provider 测试 — 优先 Agent，兜底 global"""

    def test_returns_agent_provider_first(self, agents_config_file, monkeypatch):
        """有 Agent provider 时优先返回 Agent 的"""
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _get_first_provider
        result = _get_first_provider()

        assert result is not None
        # 应该返回第一个有 provider 的 Agent（analyst）
        assert result["base_url"] == "http://analyst-api.example.com/v1"

    def test_falls_back_to_global(self, tmp_path, monkeypatch):
        """没有 Agent provider 时 fallback 到 global"""
        data = {
            "global_llm": {
                "provider": {
                    "baseUrl": "http://fallback.com/v1",
                    "apiKey": "fb-key",
                    "models": [{"id": "fb-model"}]
                },
                "default_model": "fb-model"
            },
            "agents": {
                "no_provider_agent": {
                    "role": "test",
                    "system_prompt": "test",
                    "provider": {}
                }
            }
        }
        config_file = tmp_path / "fallback_test.json"
        with open(config_file, 'w') as f:
            json.dump(data, f)

        monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _get_first_provider
        result = _get_first_provider()

        assert result is not None
        assert result["base_url"] == "http://fallback.com/v1"


class TestConfigAPIEndpoints:
    """配置 API 端点集成测试"""

    @pytest.fixture
    def client_with_config(self, tmp_path):
        """创建带配置文件的测试客户端"""
        import importlib

        # 准备 agents.json
        config = {
            "global_llm": {
                "provider": {
                    "baseUrl": "http://global.com/v1",
                    "apiKey": "global-key",
                    "models": [{"id": "gpt-4"}]
                },
                "default_model": "gpt-4"
            },
            "agents": {
                "assistant": {
                    "role": "助手",
                    "system_prompt": "You are helpful.",
                    "tools": ["read_file"],
                    "default_model": "gpt-3.5-turbo",
                    "provider": {
                        "baseUrl": "http://assistant.com/v1",
                        "apiKey": "asst-key",
                        "models": [{"id": "gpt-3.5-turbo"}]
                    }
                },
                "coder": {
                    "role": "程序员",
                    "system_prompt": "You code.",
                    "tools": ["read_file", "execute_code"],
                    "provider": {}
                }
            }
        }
        config_file = tmp_path / "agents.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f)

        # 设置环境
        os.environ["AGENT_CONFIG_FILE"] = str(config_file)
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
        os.environ["LLM_MODEL"] = "test-model"
        os.environ["LOG_LEVEL"] = "WARNING"
        os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

        # 清理缓存模块
        modules_to_clear = [
            'main', 'config', 'models.database', 'agents.registry',
            'agents.collaboration', 'tools', 'llm.client', 'chatrooms.manager',
            'routes.api', 'routes.websocket'
        ]
        for mod_name in modules_to_clear:
            if mod_name in sys.modules:
                del sys.modules[mod_name]

        # Mock LLM
        import llm.client as llm_mod
        mock_llm = MagicMock()
        mock_llm.base_url = "http://localhost:9999/v1"
        mock_llm.model = "test-model"
        mock_llm.chat = AsyncMock(return_value="Mocked response.")
        mock_llm.chat_with_tools = AsyncMock(return_value={
            "content": "Mocked agent response.", "tool_calls": None
        })
        llm_mod._llm_client = mock_llm

        import main as main_mod
        # 简化中间件
        async def passthrough(self, request, call_next):
            return await call_next(request)
        main_mod.RateLimitMiddleware.dispatch = passthrough
        main_mod.RequestLoggingMiddleware.dispatch = passthrough

        from fastapi.testclient import TestClient
        return TestClient(main_mod.app, base_url="http://testserver"), config_file

    def test_get_config_returns_global_llm(self, client_with_config):
        """GET /config 返回 global_llm 段"""
        client, _ = client_with_config
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()

        assert "global_llm" in data
        assert data["global_llm"]["provider"]["baseUrl"] == "http://global.com/v1"
        assert data["global_llm"]["default_model"] == "gpt-4"

    def test_get_config_agent_source_field(self, client_with_config):
        """GET /config 返回 agent_llm_configs 含 source 字段"""
        client, _ = client_with_config
        r = client.get("/api/config")
        data = r.json()

        agent_cfgs = data["agent_llm_configs"]

        # assistant 有自身 provider → source=agent
        assert agent_cfgs["assistant"]["source"] == "agent"
        assert agent_cfgs["assistant"]["baseUrl"] == "http://assistant.com/v1"

        # coder 无 provider → source=global
        assert agent_cfgs["coder"]["source"] == "global"
        assert agent_cfgs["coder"]["baseUrl"] == "http://global.com/v1"

    def test_put_global_config(self, client_with_config):
        """PUT /config/global 更新全局配置"""
        client, config_file = client_with_config

        r = client.put("/api/config/global", json={
            "provider": {
                "baseUrl": "http://new-global.com/v1",
                "apiKey": "new-global-key",
                "models": [{"id": "claude-3"}]
            },
            "default_model": "claude-3"
        })
        assert r.status_code == 200
        assert "global_llm" in r.json()

        # 验证文件已更新
        with open(config_file, 'r') as f:
            saved = json.load(f)
        assert saved["global_llm"]["provider"]["baseUrl"] == "http://new-global.com/v1"
        assert saved["global_llm"]["default_model"] == "claude-3"

    def test_put_agent_config(self, client_with_config):
        """PUT /config/agent/{name} 更新 Agent 配置"""
        client, config_file = client_with_config

        r = client.put("/api/config/agent/coder", json={
            "provider": {
                "baseUrl": "http://coder-new.com/v1",
                "apiKey": "coder-new-key",
                "models": [{"id": "deepseek-coder"}]
            },
            "default_model": "deepseek-coder"
        })
        assert r.status_code == 200

        # 验证文件已更新
        with open(config_file, 'r') as f:
            saved = json.load(f)
        assert saved["agents"]["coder"]["provider"]["baseUrl"] == "http://coder-new.com/v1"
        assert saved["agents"]["coder"]["default_model"] == "deepseek-coder"

    def test_put_agent_config_not_found(self, client_with_config):
        """PUT /config/agent/不存在的Agent 返回 404"""
        client, _ = client_with_config

        r = client.put("/api/config/agent/nonexistent", json={
            "provider": {"baseUrl": "http://x.com/v1"}
        })
        assert r.status_code == 404

    def test_clear_agent_config_use_global(self, client_with_config):
        """清除 Agent provider 后 fallback 到 global"""
        client, config_file = client_with_config

        # 清除 assistant 的 provider
        r = client.put("/api/config/agent/assistant", json={
            "provider": {},
            "default_model": ""
        })
        assert r.status_code == 200

        # 验证 GET /config 中 assistant 现在 source=global
        r2 = client.get("/api/config")
        data = r2.json()
        assert data["agent_llm_configs"]["assistant"]["source"] == "global"
        assert data["agent_llm_configs"]["assistant"]["baseUrl"] == "http://global.com/v1"

    def test_roundtrip_global_then_agent_override(self, client_with_config):
        """完整流程：设全局 → Agent 覆盖 → 清除 Agent → 回到全局"""
        client, _ = client_with_config

        # 1. 更新全局
        client.put("/api/config/global", json={
            "provider": {
                "baseUrl": "http://roundtrip.com/v1",
                "apiKey": "rt-key",
                "models": [{"id": "rt-model"}]
            },
            "default_model": "rt-model"
        })

        # 2. coder 现在应该用新的全局
        r = client.get("/api/config")
        assert r.json()["agent_llm_configs"]["coder"]["baseUrl"] == "http://roundtrip.com/v1"
        assert r.json()["agent_llm_configs"]["coder"]["source"] == "global"

        # 3. 给 coder 单独设置
        client.put("/api/config/agent/coder", json={
            "provider": {
                "baseUrl": "http://coder-own.com/v1",
                "apiKey": "c-key",
                "models": [{"id": "coder-model"}]
            },
            "default_model": "coder-model"
        })

        r = client.get("/api/config")
        assert r.json()["agent_llm_configs"]["coder"]["baseUrl"] == "http://coder-own.com/v1"
        assert r.json()["agent_llm_configs"]["coder"]["source"] == "agent"

        # 4. 清除 coder 配置 → 回到全局
        client.put("/api/config/agent/coder", json={"provider": {}})

        r = client.get("/api/config")
        assert r.json()["agent_llm_configs"]["coder"]["baseUrl"] == "http://roundtrip.com/v1"
        assert r.json()["agent_llm_configs"]["coder"]["source"] == "global"
