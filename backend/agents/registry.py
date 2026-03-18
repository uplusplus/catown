# -*- coding: utf-8 -*-
"""
内置 Agent 注册表
"""
from typing import Dict, List
from agents.core import Agent, AgentConfig
from models.database import SessionLocal, Agent as DBAgent
import json


class AgentRegistry:
    """Agent 注册表"""
    
    def __init__(self):
        self.agents: Dict[str, Agent] = {}
        self.configs: Dict[str, AgentConfig] = {}
    
    def register(self, config: AgentConfig, agent: Agent):
        """注册 Agent"""
        self.agents[config.name] = agent
        self.configs[config.name] = config
    
    def get(self, name: str) -> Agent:
        """获取 Agent"""
        return self.agents.get(name)
    
    def get_config(self, name: str) -> AgentConfig:
        """获取 Agent 配置"""
        return self.configs.get(name)
    
    def list_agents(self) -> List[str]:
        """列出所有 Agent"""
        return list(self.agents.keys())
    
    def create_agent(self, name: str, llm_client=None) -> Agent:
        """根据名称创建 Agent 实例"""
        config = self.configs.get(name)
        if not config:
            raise ValueError(f"Agent '{name}' not found")
        
        agent = Agent(config, llm_client)
        
        # 注册默认工具
        self._register_default_tools(agent, name)
        
        return agent
    
    def _register_default_tools(self, agent: Agent, agent_name: str):
        """注册默认工具"""
        # Web 搜索工具
        def web_search(query: str):
            """执行网络搜索"""
            # TODO: 实现实际的搜索逻辑
            return f"Search results for: {query}"
        
        agent.register_tool(
            "web_search",
            web_search,
            "Search the web for information"
        )
        
        # 代码执行工具（仅对 coder Agent）
        if agent_name == "coder":
            def execute_code(code: str):
                """执行代码"""
                # TODO: 实现安全的代码执行
                return f"Code executed: {code[:50]}..."
            
            agent.register_tool(
                "execute_code",
                execute_code,
                "Execute code snippets"
            )
        
        # 记忆检索工具
        def retrieve_memory(query: str):
            """检索记忆"""
            return agent.get_memory_summary()
        
        agent.register_tool(
            "retrieve_memory",
            retrieve_memory,
            "Retrieve information from memory"
        )


# 全局注册表实例
registry = AgentRegistry()


def get_builtin_agent_configs() -> List[AgentConfig]:
    """获取内置 Agent 配置"""
    return [
        AgentConfig(
            name="assistant",
            role="通用助手",
            system_prompt="""You are a helpful assistant in the Catown platform. 
Your role is to help users with general tasks, answer questions, and coordinate with other agents when needed.
Always be polite, clear, and helpful.
If a task requires specialized knowledge, suggest involving the appropriate agent.""",
            tools=["web_search", "retrieve_memory"]
        ),
        
        AgentConfig(
            name="coder",
            role="代码专家",
            system_prompt="""You are an expert programmer in the Catown platform.
Your specialties include:
- Writing clean, efficient code
- Code review and debugging
- Explaining technical concepts
- Suggesting best practices

Always provide well-commented code and explain your reasoning.
Ask clarifying questions if requirements are unclear.""",
            tools=["web_search", "execute_code", "retrieve_memory"]
        ),
        
        AgentConfig(
            name="reviewer",
            role="审核专家",
            system_prompt="""You are a critical reviewer in the Catown platform.
Your role is to:
- Review work products for quality
- Provide constructive feedback
- Identify potential issues
- Suggest improvements

Be thorough but fair. Focus on helping improve the outcome.""",
            tools=["web_search", "retrieve_memory"]
        ),
        
        AgentConfig(
            name="researcher",
            role="研究专家",
            system_prompt="""You are a research specialist in the Catown platform.
Your expertise includes:
- Gathering and analyzing information
- Conducting literature reviews
- Synthesizing complex topics
- Providing evidence-based insights

Always cite sources and distinguish between facts and opinions.""",
            tools=["web_search", "retrieve_memory"]
        )
    ]


def register_builtin_agents(llm_client=None):
    """注册内置 Agent"""
    configs = get_builtin_agent_configs()
    
    for config in configs:
        agent = Agent(config, llm_client)
        registry.register(config, agent)
        
        # 保存到数据库
        db = SessionLocal()
        try:
            db_agent = DBAgent(
                name=config.name,
                role=config.role,
                system_prompt=config.system_prompt,
                tools=json.dumps(config.tools)
            )
            db.add(db_agent)
            db.commit()
        except Exception as e:
            print(f"Warning: Could not save agent {config.name} to database: {e}")
        finally:
            db.close()
    
    print(f"✅ Registered {len(configs)} built-in agents")


def get_registry() -> AgentRegistry:
    """获取全局注册表"""
    return registry
