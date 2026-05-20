"""
配置模型测试

覆盖 AgentConfigV2 / AgentProviderConfig / ModelConfig / create_agent_config_from_provider
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _soul(identity: str = "Builds practical software.") -> dict:
    return {
        "identity": identity,
        "values": ["clarity"],
        "style": "direct",
        "quirks": "",
    }


def _role(title: str = "Engineer") -> dict:
    return {
        "title": title,
        "responsibilities": ["Ship maintainable code"],
        "rules": ["Keep changes focused"],
    }


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
        c = AgentConfigV2(name="test", soul=_soul(), role=_role())
        assert c.name == "test"
        assert c.type == "test"
        assert c.tools == []
        assert c.provider is None

    def test_effective_url_from_provider(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig
        c = AgentConfigV2(
            name="t", soul=_soul(), role=_role(),
            provider=AgentProviderConfig(baseUrl="http://custom/v1", apiKey="k")
        )
        assert c.get_effective_base_url() == "http://custom/v1"

    def test_effective_url_default(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", soul=_soul(), role=_role())
        assert "openai" in c.get_effective_base_url()

    def test_effective_api_key_from_provider(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig
        c = AgentConfigV2(
            name="t", soul=_soul(), role=_role(),
            provider=AgentProviderConfig(baseUrl="http://x", apiKey="sk-provider-key")
        )
        assert c.get_effective_api_key() == "sk-provider-key"

    def test_effective_model_prefer_default(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig
        c = AgentConfigV2(
            name="t", soul=_soul(), role=_role(),
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
            name="t", soul=_soul(), role=_role(),
            provider=AgentProviderConfig(
                baseUrl="http://x", apiKey="k",
                models=[ModelConfig(id="provider-m", name="PM")]
            )
        )
        assert c.get_effective_model() == "provider-m"

    def test_effective_model_ultimate_default(self):
        from agents.config_models import AgentConfigV2
        c = AgentConfigV2(name="t", soul=_soul(), role=_role())
        assert c.get_effective_model() == "gpt-4"

    def test_get_model_config(self):
        from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig
        c = AgentConfigV2(
            name="t", soul=_soul(), role=_role(),
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
        c = AgentConfigV2(name="t", soul=_soul(), role=_role())
        assert c.get_model_config("any") is None

    def test_build_system_prompt_uses_structured_soul_and_role(self):
        from agents.config_models import AgentConfigV2

        c = AgentConfigV2(name="Builder", soul=_soul("Builds practical software."), role=_role("Developer"))
        prompt = c.build_system_prompt(project_memory="Project scope", long_term_memory="Prefer tests")

        assert "Builder" in prompt
        assert "Builds practical software." in prompt
        assert "Ship maintainable code" in prompt
        assert "Keep changes focused" in prompt
        assert "Project scope" in prompt
        assert "Prefer tests" in prompt


class TestCreateAgentConfigFromProvider:
    """create_agent_config_from_provider 测试"""

    def test_basic_creation(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_type="researcher",
            soul=_soul("Researches thoroughly."),
            role=_role("Research Expert"),
            provider_config={
                "baseUrl": "http://localhost:8000/v1",
                "apiKey": "sk-test",
                "models": [{"id": "gpt-4", "name": "GPT-4"}]
            },
            tools=["web_search"]
        )
        assert c.name == "Researcher"
        assert c.type == "researcher"
        assert c.tools == ["web_search"]
        assert c.provider is not None
        assert c.get_effective_model() == "gpt-4"

    def test_with_default_model(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_type="a", soul=_soul("Handles requests."), role=_role("Responder"),
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

    def test_preserves_metadata(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_type="valet",
            soul=_soul("Coordinates work."),
            role=_role("Coordinator"),
            provider_config={
                "baseUrl": "http://x",
                "apiKey": "k",
                "models": [{"id": "m1", "name": "M1"}],
            },
            metadata={
                "runtime_contract": {
                    "mode": "coordinator",
                    "dispatch_tools": ["delegate_task"],
                }
            },
        )

        assert c.metadata["runtime_contract"]["mode"] == "coordinator"
        assert c.metadata["runtime_contract"]["dispatch_tools"] == ["delegate_task"]

    def test_multiple_models(self):
        from agents.config_models import create_agent_config_from_provider
        c = create_agent_config_from_provider(
            agent_type="a", soul=_soul("Handles requests."), role=_role("Responder"),
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
