import logging
logger = logging.getLogger("catown.registry")
# -*- coding: utf-8 -*-
"""
内置 Agent 注册表

职责：
1. 管理 Agent 配置（从 agents.json 或默认配置加载）
2. 注册 Agent 到数据库
3. 提供 Agent 列表查询

注意：Agent 的工具绑定统一通过 tools/tool_registry 管理，
本模块不再维护独立的工具注册逻辑。
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
        """获取 Agent 实例"""
        return self.agents.get(name)
    
    def get_config(self, name: str) -> Union[AgentConfig, AgentConfigV2, None]:
        """获取 Agent 配置"""
        return self.configs.get(name)
    
    def list_agents(self) -> List[str]:
        """列出所有已注册 Agent 名称"""
        return list(self.agents.keys())
    
    def get_tools_for_agent(self, agent_name: str) -> List[str]:
        """获取 Agent 配置中声明的工具列表"""
        config = self.configs.get(agent_name)
        if config:
            return config.tools
        return []


# 全局注册表实例
registry = AgentRegistry()


def get_builtin_agent_configs() -> List[Union[AgentConfig, AgentConfigV2]]:
    """获取内置 Agent 配置"""
    
    # 优先从配置文件加载（相对于 backend 目录）
    _backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_file = os.getenv("AGENT_CONFIG_FILE", os.path.join(_backend_dir, "configs", "agents.json"))
    if os.path.exists(config_file):
        try:
            from agents.config_manager import load_agent_configs
            configs_dict = load_agent_configs(config_file)
            return list(configs_dict.values())
        except Exception as e:
            logger.warning(f"Failed to load config from {config_file}: {e}")
    
    # 使用默认配置（从环境变量读取 LLM 连接信息）
    default_provider = {
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
    }
    
    return [
        create_agent_config_from_provider(
            agent_name="assistant",
            role="助理",
            system_prompt="You are a helpful assistant in the Catown platform. Help users with general tasks, answer questions, and coordinate with other agents when needed.",
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory"]
        ),
        create_agent_config_from_provider(
            agent_name="analyst",
            role="需求分析师",
            system_prompt="You are a professional requirements analyst. Transform vague user requirements into structured, actionable PRDs.",
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory", "read_file", "write_file"]
        ),
        create_agent_config_from_provider(
            agent_name="architect",
            role="架构师",
            system_prompt="You are a senior software architect. Design technical solutions based on PRDs.",
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory", "read_file", "write_file"]
        ),
        create_agent_config_from_provider(
            agent_name="developer",
            role="开发工程师",
            system_prompt="You are an expert software developer. Implement code based on tech specs.",
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory", "read_file", "write_file", "list_files", "execute_code", "search_files"]
        ),
        create_agent_config_from_provider(
            agent_name="tester",
            role="测试工程师",
            system_prompt="You are a QA engineer. Test software against requirements and find bugs.",
            provider_config=default_provider,
            tools=["retrieve_memory", "read_file", "execute_code", "list_files", "search_files"]
        ),
        create_agent_config_from_provider(
            agent_name="release",
            role="发布经理",
            system_prompt="You are a release manager. Prepare software for release.",
            provider_config=default_provider,
            tools=["retrieve_memory", "read_file", "write_file", "list_files", "execute_code"]
        ),
    ]


def register_builtin_agents():
    """
    注册内置 Agent 到数据库和注册表
    
    Agent 的工具配置存储在 config.tools 列表中，
    实际工具调用通过 routes/api.py 的 trigger_agent_response
    从 tool_registry 获取 schema 并执行。
    """
    configs = get_builtin_agent_configs()
    
    for config in configs:
        # 创建 Agent 实例（不传入 llm_client，实际调用走 routes/api.py）
        agent = Agent(config)
        registry.register(config, agent)
        
        # 同步到数据库
        db = SessionLocal()
        try:
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
            logger.warning(f"Could not save agent {config.name} to database: {e}")
        finally:
            db.close()
    
    logger.info(f"Registered {len(configs)} built-in agents: {', '.join(registry.list_agents())}")


def get_registry() -> AgentRegistry:
    """获取全局注册表"""
    return registry
