"""
LLM 两级配置测试

覆盖：
1. Agent 自身 provider → 直接使用
2. Agent 无 provider → fallback 到 global_llm
3. global_llm 也无 → fallback 到环境变量
4. API 端点：PUT /config/global, PUT /config/agent/{name}
5. GET /config 返回 source 字段
6. orchestration sidecar 策略配置
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
        "framework_llm": {
            "provider": {
                "baseUrl": "http://framework-api.example.com/v1",
                "apiKey": "framework-key-123",
                "models": [
                    {"id": "framework-model-7b", "name": "Framework Model 7B"}
                ]
            },
            "default_model": "framework-model-7b",
            "fallback": {
                "enabled": True,
                "provider": {
                    "baseUrl": "http://framework-fallback.example.com/v1",
                    "apiKey": "framework-fallback-key",
                    "models": [
                        {"id": "framework-fallback-model", "name": "Framework Fallback Model"}
                    ]
                },
                "default_model": "framework-fallback-model"
            }
        },
        "agents": {
            "analyst": {
                "soul": {
                    "identity": "Analyzes requirements carefully.",
                    "values": ["clarity"],
                    "style": "structured",
                    "quirks": "",
                },
                "role": {
                    "title": "分析师",
                    "responsibilities": ["梳理需求"],
                    "rules": ["输出清晰结论"],
                },
                "tools": ["read_file", "write_file"],
                "default_model": "analyst-model",
                "provider": {
                    "baseUrl": "http://analyst-api.example.com/v1",
                    "apiKey": "analyst-key",
                    "models": [{"id": "analyst-model", "name": "Analyst Model"}]
                }
            },
            "developer": {
                "soul": {
                    "identity": "Builds maintainable code.",
                    "values": ["readability"],
                    "style": "practical",
                    "quirks": "",
                },
                "role": {
                    "title": "开发者",
                    "responsibilities": ["实现功能"],
                    "rules": ["优先简单方案"],
                },
                "tools": ["read_file", "write_file", "execute_code"],
                "default_model": "",
                "provider": {}  # 无自身配置，应 fallback 到 global
            },
            "tester": {
                "soul": {
                    "identity": "Finds regressions before release.",
                    "values": ["safety"],
                    "style": "precise",
                    "quirks": "",
                },
                "role": {
                    "title": "测试员",
                    "responsibilities": ["验证质量"],
                    "rules": ["标记 blocker"],
                },
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

    def test_provider_mode_inherits_global_and_allows_agent_override(self, tmp_path, monkeypatch):
        config_file = tmp_path / "agents.json"
        config_file.write_text(
            json.dumps(
                {
                    "global_llm": {
                        "provider": {
                            "baseUrl": "http://global-api.example.com/v1",
                            "apiKey": "global-key",
                            "models": [{"id": "global-model"}],
                        },
                        "default_model": "global-model",
                        "runtime": {"provider_mode": "responses_http"},
                    },
                    "agents": {
                        "analyst": {
                            "provider": {
                                "baseUrl": "http://analyst-api.example.com/v1",
                                "apiKey": "analyst-key",
                                "models": [{"id": "analyst-model"}],
                            },
                            "default_model": "analyst-model",
                        },
                        "developer": {
                            "provider": {
                                "baseUrl": "http://developer-api.example.com/v1",
                                "apiKey": "developer-key",
                                "models": [{"id": "developer-model"}],
                            },
                            "default_model": "developer-model",
                            "runtime": {"provider_mode": "chat_completions"},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_agent_provider

        inherited = _load_agent_provider("analyst")
        overridden = _load_agent_provider("developer")

        assert inherited["provider_mode"] == "responses_http"
        assert overridden["provider_mode"] == "chat_completions"

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


class TestLoadFrameworkProvider:
    """framework_llm provider loading tests."""

    def test_load_framework_provider_is_independent(self, agents_config_file, monkeypatch):
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_framework_provider

        result = _load_framework_provider()

        assert result is not None
        assert result["base_url"] == "http://framework-api.example.com/v1"
        assert result["api_key"] == "framework-key-123"
        assert result["model"] == "framework-model-7b"

    def test_framework_provider_returns_none_when_override_missing(self, tmp_path, monkeypatch):
        config_file = tmp_path / "agents.json"
        config_file.write_text(
            json.dumps(
                {
                    "global_llm": {
                        "provider": {
                            "baseUrl": "http://global-api.example.com/v1",
                            "apiKey": "global-key",
                            "models": [{"id": "global-model"}],
                        },
                        "default_model": "global-model",
                    },
                    "agents": {},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_framework_provider

        assert _load_framework_provider() is None

    def test_load_framework_fallback_provider_is_explicit(self, agents_config_file, monkeypatch):
        monkeypatch.setenv("AGENT_CONFIG_FILE", agents_config_file)
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_framework_fallback_provider

        result = _load_framework_fallback_provider()

        assert result is not None
        assert result["base_url"] == "http://framework-fallback.example.com/v1"
        assert result["api_key"] == "framework-fallback-key"
        assert result["model"] == "framework-fallback-model"

    def test_disabled_framework_fallback_provider_returns_none(self, tmp_path, monkeypatch):
        config_file = tmp_path / "agents.json"
        config_file.write_text(
            json.dumps(
                {
                    "framework_llm": {
                        "provider": {
                            "baseUrl": "http://framework-api.example.com/v1",
                            "apiKey": "framework-key",
                            "models": [{"id": "framework-model"}],
                        },
                        "default_model": "framework-model",
                        "fallback": {
                            "enabled": False,
                            "provider": {
                                "baseUrl": "http://disabled-fallback.example.com/v1",
                                "apiKey": "disabled-key",
                                "models": [{"id": "disabled-model"}],
                            },
                            "default_model": "disabled-model",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
        if 'config' in sys.modules:
            del sys.modules['config']
        if 'llm.client' in sys.modules:
            del sys.modules['llm.client']

        from llm.client import _load_framework_fallback_provider

        assert _load_framework_fallback_provider() is None


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
                    "soul": {
                        "identity": "Handles generic tasks.",
                        "values": [],
                        "style": "",
                        "quirks": "",
                    },
                    "role": {
                        "title": "test",
                        "responsibilities": [],
                        "rules": [],
                    },
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
                    "models": [{"id": "gpt-4", "name": "GPT-4"}]
                },
                "default_model": "gpt-4"
            },
            "framework_llm": {
                "provider": {
                    "baseUrl": "http://framework.com/v1",
                    "apiKey": "framework-key",
                    "models": [{"id": "qwen-local", "name": "Qwen Local"}]
                },
                "default_model": "qwen-local",
                "fallback": {
                    "enabled": False,
                    "provider": {
                        "baseUrl": "http://framework-fallback.com/v1",
                        "apiKey": "framework-fallback-key",
                        "models": [{"id": "fallback-model", "name": "Fallback Model"}]
                    },
                    "default_model": "fallback-model"
                }
            },
            "agents": {
                "assistant": {
                    "soul": {
                        "identity": "Helps users move work forward.",
                        "values": ["clarity"],
                        "style": "friendly",
                        "quirks": "",
                    },
                    "role": {
                        "title": "助手",
                        "responsibilities": ["回答问题"],
                        "rules": ["不确定时提问"],
                    },
                    "tools": ["read_file"],
                    "default_model": "gpt-3.5-turbo",
                    "provider": {
                        "baseUrl": "http://assistant.com/v1",
                        "apiKey": "asst-key",
                        "models": [{"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"}]
                    }
                },
                "coder": {
                    "soul": {
                        "identity": "Writes production code.",
                        "values": ["quality"],
                        "style": "direct",
                        "quirks": "",
                    },
                    "role": {
                        "title": "程序员",
                        "responsibilities": ["实现代码"],
                        "rules": ["补充必要测试"],
                    },
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
            'routes.api', 'routes.websocket', 'pipeline.engine', 'routes.pipeline',
            'services.approval_queue', 'services.approval_replay', 'services.monitor_projection',
            'services.tool_execution_preferences',
        ]
        from tests.conftest import reset_app_modules
        reset_app_modules(modules_to_clear)

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
        return (
            TestClient(main_mod.app, base_url="http://testserver", headers={"X-Catown-Client": "test"}),
            config_file,
        )

    def test_get_config_returns_global_llm(self, client_with_config):
        """GET /config 返回 global_llm 段"""
        client, _ = client_with_config
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()

        assert "global_llm" in data
        assert data["global_llm"]["provider"]["baseUrl"] == "http://global.com/v1"
        assert data["global_llm"]["default_model"] == "gpt-4"
        assert data["framework_llm"]["provider"]["baseUrl"] == "http://framework.com/v1"
        assert data["framework_llm"]["default_model"] == "qwen-local"
        assert data["framework_llm"]["fallback"]["enabled"] is False
        assert data["orchestration"]["sidecar_agent_types"] == ["tester"]

    def test_get_config_agent_source_field(self, client_with_config):
        """GET /config 返回 agent_llm_configs 含 source 字段"""
        client, _ = client_with_config
        r = client.get("/api/config")
        data = r.json()

        agent_cfgs = data["agent_llm_configs"]

        # 默认助手会归一化为 valet；有自身 provider → source=agent
        assert agent_cfgs["valet"]["source"] == "agent"
        assert agent_cfgs["valet"]["baseUrl"] == "http://assistant.com/v1"

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
        with open(config_file, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert saved["global_llm"]["provider"]["baseUrl"] == "http://new-global.com/v1"
        assert saved["global_llm"]["default_model"] == "claude-3"

    def test_put_framework_config(self, client_with_config):
        """PUT /config/framework updates the dedicated framework LLM config."""
        client, config_file = client_with_config

        r = client.put("/api/config/framework", json={
            "provider": {
                "baseUrl": "http://framework-new.com/v1",
                "apiKey": "framework-new-key",
                "models": [{"id": "qwen-local-14b"}]
            },
            "default_model": "qwen-local-14b",
            "fallback": {
                "enabled": True,
                "provider": {
                    "baseUrl": "http://framework-fallback-new.com/v1",
                    "apiKey": "framework-fallback-new-key",
                    "models": [{"id": "fallback-remote"}]
                },
                "default_model": "fallback-remote"
            }
        })
        assert r.status_code == 200
        assert "framework_llm" in r.json()

        with open(config_file, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert saved["framework_llm"]["provider"]["baseUrl"] == "http://framework-new.com/v1"
        assert saved["framework_llm"]["default_model"] == "qwen-local-14b"
        assert saved["framework_llm"]["fallback"]["enabled"] is True
        assert saved["framework_llm"]["fallback"]["provider"]["baseUrl"] == "http://framework-fallback-new.com/v1"

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
        with open(config_file, 'r', encoding='utf-8') as f:
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

    def test_put_orchestration_config(self, client_with_config):
        """PUT /config/orchestration updates the sidecar policy."""
        client, config_file = client_with_config

        r = client.put("/api/config/orchestration", json={
            "sidecar_agent_types": ["developer", " tester ", ""]
        })
        assert r.status_code == 200
        assert r.json()["orchestration"]["sidecar_agent_types"] == ["developer", "tester"]

        with open(config_file, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert saved["orchestration"]["sidecar_agent_types"] == ["developer", "tester"]

    def test_clear_agent_config_use_global(self, client_with_config):
        """清除 Agent provider 后 fallback 到 global"""
        client, config_file = client_with_config

        # 清除 assistant 的 provider
        r = client.put("/api/config/agent/assistant", json={
            "provider": {},
            "default_model": ""
        })
        assert r.status_code == 200

        # 验证 GET /config 中 valet 现在 source=global
        r2 = client.get("/api/config")
        data = r2.json()
        assert data["agent_llm_configs"]["valet"]["source"] == "global"
        assert data["agent_llm_configs"]["valet"]["baseUrl"] == "http://global.com/v1"

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

    def test_test_config_uses_responses_websocket_provider_mode(self, client_with_config):
        client, config_file = client_with_config

        with open(config_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        saved["agents"]["assistant"]["runtime"] = {"provider_mode": "responses_websocket"}
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(saved, f)

        created_clients = []

        class FakeLLMClient:
            def __init__(self, **kwargs):
                created_clients.append(kwargs)

            async def chat_stream(self, messages):
                yield {"type": "done", "response_id": "resp_ws_test"}

        with patch("routes.api.LLMClient", FakeLLMClient):
            response = client.post("/api/config/test", params={"agent_name": "assistant"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["provider_mode"] == "responses_websocket"
        assert payload["response_id"] == "resp_ws_test"
        assert created_clients == [
            {
                "base_url": "http://assistant.com/v1",
                "api_key": "asst-key",
                "model": "gpt-3.5-turbo",
                "agent_name": "valet",
                "provider_mode": "responses_websocket",
            }
        ]
