"""
配置模型测试

覆盖 AgentConfigV2 / AgentProviderConfig / ModelConfig / create_agent_config_from_provider
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestModelConfig:
    """ModelConfig 测试"""

    def test_create_minimal(self):
        from agents.config_models import ModelConfig
        m = ModelConfig(id="gpt-4", name="GPT-4")
        assert m.id == "gpt-4"
        assert m.contextWindow == 128000
        assert m.maxTokens == 16384
        assert m.reasoning is False
        assert m.input == ["text"]

    def test_create_full(self):
        from agents.config_models import ModelConfig
        m = ModelConfig(
            id="qwen", name="Qwen", api="openai-completions",
            reasoning=True, input=["text", "image"],
            contextWindow=256000, maxTokens=32768
        )
        assert m.contextWindow == 256000
        assert "image" in m.input
        assert m.reasoning is True


class TestAgentProviderConfig:
    """AgentProviderConfig 测试"""

    def test_create(self):
        from agents.config_models import AgentProviderConfig, ModelConfig
        p = AgentProviderConfig(
            baseUrl="http://localhost:8000/v1",
            apiKey="sk-test",
            models=[
                ModelConfig(id="m1", name="Model 1"),
                ModelConfig(id="m2", name="Model 2"),
            ]
        )
        assert p.baseUrl == "http://localhost:8000/v1"
        assert len(p.models) == 2

    def test_get_default_model(self):
        from agents.config_models import AgentProviderConfig, ModelConfig
        p = AgentProviderConfig(
            baseUrl="http://x", apiKey="k",
            models=[ModelConfig(id="first", name="F"), ModelConfig(id="second", name="S")]
        )
        default = p.get_default_model()
        assert default.id == "first"

    def test_get_default_model_empty(self):
        from agents.config_models import AgentProviderConfig
        p = AgentProviderConfig(baseUrl="http://x", apiKey="k", models=[])
        assert p.get_default_model() is None

    def test_get_model_by_id(self):
        from agents.config_models import AgentProviderConfig, ModelConfig
        p = AgentProviderConfig(
            baseUrl="http://x", apiKey="k",
            models=[ModelConfig(id="a", name="A"), ModelConfig(id="b", name="B")]
        )
        assert p.get_model_by_id("b").name == "B"
        assert p.get_model_by_id("nope") is None

    def test_get_models_by_capability(self):
        from agents.config_models import AgentProviderConfig, ModelConfig
        p = AgentProviderConfig(
            baseUrl="http://x", apiKey="k",
            models=[
                ModelConfig(id="t", name="T", input=["text"]),
                ModelConfig(id="v", name="V", input=["text", "image"]),
            ]
        )
        vision = p.get_models_by_capability("image")
        assert len(vision) == 1
        assert vision[0].id == "v"


class TestAgentConfigV2:
    """AgentConfigV2 测试"""

    def test_create_minimal(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="test", role="r", system_prompt="sp")
        assert c.name == "test"
        assert c.tools == []
        assert c.provider is None

    def test_effective_url_from_provider(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig
        c = AgentConfigV2(
            name="t", role="r", system_prompt="sp",
            provider=AgentProviderConfig(baseUrl="http://custom/v1", apiKey="k")
        )
        assert c.get_effective_base_url() == "http://custom/v1"

    def test_effective_url_fallback(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", role="r", system_prompt="sp", llm_base_url="http://fallback/v1")
        assert c.get_effective_base_url() == "http://fallback/v1"

    def test_effective_url_default(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", role="r", system_prompt="sp")
        assert "openai" in c.get_effective_base_url()

    def test_effective_api_key_from_provider(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig
        c = AgentConfigV2(
            name="t", role="r", system_prompt="sp",
            provider=AgentProviderConfig(baseUrl="http://x", apiKey="sk-provider-key")
        )
        assert c.get_effective_api_key() == "sk-provider-key"

    def test_effective_model_prefer_default(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig
        c = AgentConfigV2(
            name="t", role="r", system_prompt="sp",
            provider=AgentProviderConfig(
                baseUrl="http://x", apiKey="k",
                models=[ModelConfig(id="m1", name="M1")]
            ),
            default_model="m1"
        )
        assert c.get_effective_model() == "m1"

    def test_effective_model_from_provider(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig
        c = AgentConfigV2(
            name="t", role="r", system_prompt="sp",
            provider=AgentProviderConfig(
                baseUrl="http://x", apiKey="k",
                models=[ModelConfig(id="provider-m", name="PM")]
            )
        )
        assert c.get_effective_model() == "provider-m"

    def test_effective_model_fallback(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", role="r", system_prompt="sp", llm_model="fallback-model")
        assert c.get_effective_model() == "fallback-model"

    def test_effective_model_ultimate_default(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", role="r", system_prompt="sp")
        assert c.get_effective_model() == "gpt-4"

    def test_get_model_config(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig
        c = AgentConfigV2(
            name="t", role="r", system_prompt="sp",
            provider=AgentProviderConfig(
                baseUrl="http://x", apiKey="k",
                models=[ModelConfig(id="abc", name="ABC", contextWindow=64000)]
            )
        )
        info = c.get_model_config("abc")
        assert info is not None
        assert info.contextWindow == 64000

    def test_get_model_config_no_provider(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", role="r", system_prompt="sp")
        assert c.get_model_config("any") is None


class TestCreateAgentConfigFromProvider:
    """create_agent_config_from_provider 测试"""

    def test_basic_creation(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_name="researcher",
            role="Research Expert",
            system_prompt="You research things.",
            provider_config={
                "baseUrl": "http://localhost:8000/v1",
                "apiKey": "sk-test",
                "models": [{"id": "gpt-4", "name": "GPT-4"}]
            },
            tools=["web_search"]
        )
        assert c.name == "researcher"
        assert c.tools == ["web_search"]
        assert c.provider is not None
        assert c.get_effective_model() == "gpt-4"

    def test_with_default_model(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_name="a", role="r", system_prompt="s",
            provider_config={
                "baseUrl": "http://x", "apiKey": "k",
                "models": [
                    {"id": "m1", "name": "M1"},
                    {"id": "m2", "name": "M2"},
                ]
            },
            default_model="m2"
        )
        assert c.get_effective_model() == "m2"

    def test_multiple_models(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_name="a", role="r", system_prompt="s",
            provider_config={
                "baseUrl": "http://x", "apiKey": "k",
                "models": [
                    {"id": "v1", "name": "V1", "input": ["text"]},
                    {"id": "v2", "name": "V2", "input": ["text", "image"]},
                ]
            }
        )
        assert len(c.provider.models) == 2
        vision = c.provider.get_models_by_capability("image")
        assert len(vision) == 1
