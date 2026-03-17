"""
单元测试 - Agent 核心功能
"""
import pytest
import asyncio
from agents.core import Agent, AgentConfig, MemoryItem


@pytest.fixture
def sample_agent_config():
    """创建示例 Agent 配置"""
    return AgentConfig(
        name="test_agent",
        role="测试 Agent",
        system_prompt="You are a test agent.",
        tools=[]
    )


@pytest.fixture
def test_agent(sample_agent_config):
    """创建测试 Agent 实例"""
    return Agent(sample_agent_config, llm_client=None)


class TestAgentCreation:
    """测试 Agent 创建"""
    
    def test_agent_initialization(self, test_agent, sample_agent_config):
        """测试 Agent 初始化"""
        assert test_agent.config.name == sample_agent_config.name
        assert test_agent.config.role == sample_agent_config.role
        assert len(test_agent.short_term_memory) == 0
        assert len(test_agent.long_term_memory) == 0
    
    def test_agent_system_message(self, test_agent):
        """测试系统消息设置"""
        assert test_agent.system_message.role == "system"
        assert "test agent" in test_agent.system_message.content.lower()


class TestAgentMemory:
    """测试 Agent 记忆系统"""
    
    def test_add_short_term_memory(self, test_agent):
        """测试添加短期记忆"""
        memory = MemoryItem(
            content="Test memory",
            memory_type="short_term",
            importance=5
        )
        test_agent._add_to_memory(memory)
        
        assert len(test_agent.short_term_memory) == 1
        assert test_agent.short_term_memory[0].content == "Test memory"
    
    def test_add_long_term_memory(self, test_agent):
        """测试添加长期记忆"""
        memory = MemoryItem(
            content="Important fact",
            memory_type="long_term",
            importance=8
        )
        test_agent._add_to_memory(memory)
        
        assert len(test_agent.long_term_memory) == 1
        assert test_agent.long_term_memory[0].importance == 8
    
    def test_short_term_memory_limit(self, test_agent):
        """测试短期记忆数量限制"""
        # 添加超过限制的記憶
        for i in range(25):
            memory = MemoryItem(
                content=f"Memory {i}",
                memory_type="short_term",
                importance=5
            )
            test_agent._add_to_memory(memory)
        
        # 应该只保留最近的 20 条
        assert len(test_agent.short_term_memory) == 20


class TestAgentTools:
    """测试 Agent 工具功能"""
    
    def test_register_tool(self, test_agent):
        """测试工具注册"""
        def sample_tool():
            return "tool result"
        
        test_agent.register_tool(
            name="sample_tool",
            func=sample_tool,
            description="A sample tool"
        )
        
        assert "sample_tool" in test_agent.available_tools
        assert test_agent.available_tools["sample_tool"]["description"] == "A sample tool"
    
    def test_tool_execution(self, test_agent):
        """测试工具执行"""
        def add_numbers(a: int, b: int) -> int:
            return a + b
        
        test_agent.register_tool(
            name="add",
            func=add_numbers,
            description="Add two numbers"
        )
        
        # 执行工具
        result = test_agent.available_tools["add"]["function"](5, 3)
        assert result == 8


class TestAgentConversation:
    """测试 Agent 对话功能"""
    
    def test_conversation_history(self, test_agent):
        """测试对话历史记录"""
        # 模拟对话
        test_agent.conversation_history.append({
            "role": "user",
            "content": "Hello"
        })
        test_agent.conversation_history.append({
            "role": "assistant",
            "content": "Hi there!"
        })
        
        assert len(test_agent.conversation_history) == 2
    
    def test_reset_conversation(self, test_agent):
        """测试重置对话"""
        # 添加一些对话
        test_agent.conversation_history.append({"role": "user", "content": "Test"})
        assert len(test_agent.conversation_history) == 1
        
        # 重置
        test_agent.reset_conversation()
        assert len(test_agent.conversation_history) == 0
    
    def test_memory_summary(self, test_agent):
        """测试记忆摘要"""
        # 添加一些记忆
        test_agent._add_to_memory(MemoryItem(
            content="Short term",
            memory_type="short_term",
            importance=5
        ))
        
        test_agent._add_to_memory(MemoryItem(
            content="Long term",
            memory_type="long_term",
            importance=7
        ))
        
        summary = test_agent.get_memory_summary()
        
        assert summary["short_term_count"] == 1
        assert summary["long_term_count"] == 1
        assert summary["conversation_turns"] == 0


class TestAgentContextBuilding:
    """测试 Agent 上下文构建"""
    
    def test_build_conversation_context(self, test_agent):
        """测试构建对话上下文"""
        messages = test_agent._build_conversation_context(
            user_message="Test message",
            project_context=None
        )
        
        assert len(messages) > 0
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Test message"
    
    def test_build_enhanced_system_prompt(self, test_agent):
        """测试构建增强系统提示"""
        project_context = {
            "name": "Test Project",
            "description": "A test project",
            "other_agents": [
                {"name": "helper", "role": "Helper Agent"}
            ]
        }
        
        prompt = test_agent._build_enhanced_system_prompt(project_context)
        
        assert "Test Project" in prompt
        assert "helper" in prompt


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])
