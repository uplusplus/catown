"""
单元测试 - Agent 核心功能
"""
import pytest
from agents.core import Agent, AgentConfig, MemoryItem


@pytest.fixture
def sample_agent_config():
    """创建示例 Agent 配置"""
    return AgentConfig(
        name="test_agent",
        role="测试 Agent",
        system_prompt="You are a test agent.",
        tools=["web_search", "retrieve_memory"]
    )


@pytest.fixture
def test_agent(sample_agent_config):
    """创建测试 Agent 实例"""
    return Agent(sample_agent_config, llm_client=None)


class TestAgentCreation:
    """测试 Agent 创建"""
    
    def test_agent_initialization(self, test_agent, sample_agent_config):
        """测试 Agent 初始化"""
        assert test_agent.name == "test_agent"
        assert test_agent.role == "测试 Agent"
        assert test_agent.system_prompt == "You are a test agent."
        assert test_agent.tools == ["web_search", "retrieve_memory"]
        assert len(test_agent.short_term_memory) == 0
        assert len(test_agent.long_term_memory) == 0
        assert len(test_agent.conversation_history) == 0
    
    def test_agent_properties(self, test_agent):
        """测试 Agent 属性访问"""
        assert test_agent.name == test_agent.config.name
        assert test_agent.role == test_agent.config.role
        assert test_agent.system_prompt == test_agent.config.system_prompt


class TestAgentMemory:
    """测试 Agent 记忆系统"""
    
    def test_add_short_term_memory(self, test_agent):
        """测试添加短期记忆"""
        test_agent.add_memory("Test memory", memory_type="short_term", importance=5)
        
        assert len(test_agent.short_term_memory) == 1
        assert test_agent.short_term_memory[0].content == "Test memory"
    
    def test_add_long_term_memory(self, test_agent):
        """测试添加长期记忆"""
        test_agent.add_memory("Important fact", memory_type="long_term", importance=8)
        
        assert len(test_agent.long_term_memory) == 1
        assert test_agent.long_term_memory[0].importance == 8
    
    def test_short_term_memory_limit(self, test_agent):
        """测试短期记忆数量限制（最多 20 条）"""
        for i in range(25):
            test_agent.add_memory(f"Memory {i}", memory_type="short_term")
        
        assert len(test_agent.short_term_memory) == 20
        # 应保留最后 20 条
        assert test_agent.short_term_memory[0].content == "Memory 5"
    
    def test_memory_summary(self, test_agent):
        """测试记忆摘要"""
        test_agent.add_memory("Short term", memory_type="short_term")
        test_agent.add_memory("Long term", memory_type="long_term")
        
        summary = test_agent.get_memory_summary()
        assert summary["short_term_count"] == 1
        assert summary["long_term_count"] == 1
        assert summary["conversation_turns"] == 0


class TestAgentConversation:
    """测试 Agent 对话功能"""
    
    def test_add_conversation_turn(self, test_agent):
        """测试添加对话记录"""
        test_agent.add_conversation_turn("user", "Hello")
        test_agent.add_conversation_turn("assistant", "Hi there!")
        
        assert len(test_agent.conversation_history) == 2
        assert test_agent.conversation_history[0]["role"] == "user"
        assert test_agent.conversation_history[1]["role"] == "assistant"
    
    def test_reset_conversation(self, test_agent):
        """测试重置对话"""
        test_agent.add_conversation_turn("user", "Test")
        assert len(test_agent.conversation_history) == 1
        
        test_agent.reset_conversation()
        assert len(test_agent.conversation_history) == 0
    
    def test_reset_preserves_memory(self, test_agent):
        """测试重置对话不影响记忆"""
        test_agent.add_memory("Remember this", memory_type="long_term", importance=8)
        test_agent.add_conversation_turn("user", "Test")
        
        test_agent.reset_conversation()
        
        assert len(test_agent.conversation_history) == 0
        assert len(test_agent.long_term_memory) == 1


class TestAgentModel:
    """测试 Agent 模型管理"""
    
    def test_set_model(self, test_agent):
        """测试设置当前模型"""
        assert test_agent.current_model is None
        
        test_agent.set_model("gpt-4")
        assert test_agent.current_model == "gpt-4"
    
    def test_get_effective_model_default(self, test_agent):
        """测试获取默认模型"""
        model = test_agent.get_effective_model()
        assert model == "gpt-4"
    
    def test_get_effective_model_after_set(self, test_agent):
        """测试设置后获取模型"""
        test_agent.set_model("claude-3")
        assert test_agent.get_effective_model() == "claude-3"
    
    def test_get_available_models_no_provider(self, test_agent):
        """测试无 provider 时获取可用模型"""
        models = test_agent.get_available_models()
        assert models == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
