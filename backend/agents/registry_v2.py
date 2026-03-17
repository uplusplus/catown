"""
内置 Agent 注册表 - 支持新配置格式
"""
from typing import Dict, List, Union
from agents.core import Agent, AgentConfig
from agents.config_models import AgentConfigV2, create_agent_config_from_provider
from models.database import SessionLocal, Agent as DBAgent
import json
import os


class AgentRegistry:
    """Agent 注册表"""
    
    def __init__(self):
        self.agents: Dict[str, Agent] = {}
        self.configs: Dict[str, Union[AgentConfig, AgentConfigV2]] = {}
    
    def register(self, config: Union[AgentConfig, AgentConfigV2], agent: Agent):
        """注册 Agent"""
        self.agents[config.name] = agent
        self.configs[config.name] = config
    
    def get(self, name: str) -> Agent:
        """获取 Agent"""
        return self.agents.get(name)
    
    def get_config(self, name: str) -> Union[AgentConfig, AgentConfigV2, None]:
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


def get_builtin_agent_configs() -> List[Union[AgentConfig, AgentConfigV2]]:
    """获取内置 Agent 配置（新版本 - 支持多模型）"""
    
    # 尝试从配置文件加载
    config_file = os.getenv("AGENT_CONFIG_FILE", "configs/agents.json")
    if os.path.exists(config_file):
        try:
            from agents.config_manager import load_agent_configs
            configs_dict = load_agent_configs(config_file)
            return list(configs_dict.values())
        except Exception as e:
            print(f"Warning: Failed to load config from {config_file}: {e}")
    
    # 使用默认配置
    return [
        # Assistant - 使用新配置格式
        create_agent_config_from_provider(
            agent_name="assistant",
            role="通用助手",
            system_prompt="""You are a helpful assistant in the Catown platform. 
Your role is to help users with general tasks, answer questions, and coordinate with other agents when needed.
Always be polite, clear, and helpful.""",
            provider_config={
                "baseUrl": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                "apiKey": os.getenv("LLM_API_KEY", ""),
                "auth": "api-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": os.getenv("LLM_MODEL", "gpt-4"),
                        "name": os.getenv("LLM_MODEL", "gpt-4"),
                        "api": "openai-completions",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 128000,
                        "maxTokens": 16384
                    }
                ]
            },
            tools=["web_search", "retrieve_memory"]
        ),
        
        # Coder
        create_agent_config_from_provider(
            agent_name="coder",
            role="代码专家",
            system_prompt="""You are an expert programmer in the Catown platform.
Your specialties include:
- Writing clean, efficient code
- Code review and debugging
- Explaining technical concepts
- Suggesting best practices

Always provide well-commented code and explain your reasoning.""",
            provider_config={
                "baseUrl": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                "apiKey": os.getenv("LLM_API_KEY", ""),
                "auth": "api-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": os.getenv("LLM_MODEL", "gpt-4"),
                        "name": os.getenv("LLM_MODEL", "gpt-4"),
                        "api": "openai-completions",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 128000,
                        "maxTokens": 16384
                    }
                ]
            },
            tools=["web_search", "execute_code", "retrieve_memory"]
        ),
        
        # Reviewer
        create_agent_config_from_provider(
            agent_name="reviewer",
            role="审核专家",
            system_prompt="""You are a critical reviewer in the Catown platform.
Your role is to:
- Review work products for quality
- Provide constructive feedback
- Identify potential issues
- Suggest improvements

Be thorough but fair. Focus on helping improve the outcome.""",
            provider_config={
                "baseUrl": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                "apiKey": os.getenv("LLM_API_KEY", ""),
                "auth": "api-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": os.getenv("LLM_MODEL", "gpt-4"),
                        "name": os.getenv("LLM_MODEL", "gpt-4"),
                        "api": "openai-completions",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 128000,
                        "maxTokens": 16384
                    }
                ]
            },
            tools=["web_search", "retrieve_memory"]
        ),
        
        # Researcher
        create_agent_config_from_provider(
            agent_name="researcher",
            role="研究专家",
            system_prompt="""You are a research specialist in the Catown platform.
Your expertise includes:
- Gathering and analyzing information
- Conducting literature reviews
- Synthesizing complex topics
- Providing evidence-based insights

Always cite sources and distinguish between facts and opinions.""",
            provider_config={
                "baseUrl": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                "apiKey": os.getenv("LLM_API_KEY", ""),
                "auth": "api-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": os.getenv("LLM_MODEL", "gpt-4"),
                        "name": os.getenv("LLM_MODEL", "gpt-4"),
                        "api": "openai-completions",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 128000,
                        "maxTokens": 16384
                    }
                ]
            },
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
            # 检查是否已存在
            existing = db.query(DBAgent).filter(DBAgent.name == config.name).first()
            if not existing:
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
