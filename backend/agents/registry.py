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
from config import settings
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
    
    # 优先从外置配置文件加载
    config_file = settings.AGENT_CONFIG_FILE
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
            soul={"identity": "一个万能打杂的助手", "values": ["能帮就帮"], "style": "友好随和"},
            role={"title": "助理", "responsibilities": ["回答问题", "协助处理一般任务"], "rules": ["不确定时提问"]},
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory"]
        ),
        create_agent_config_from_provider(
            agent_name="analyst",
            soul={"identity": "善于提炼的需求专家", "values": ["需求不清是一切烂系统的根源"], "style": "条理清晰"},
            role={"title": "需求分析师", "responsibilities": ["将需求转化为PRD"], "rules": ["输出 Markdown"]},
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory", "read_file", "write_file"]
        ),
        create_agent_config_from_provider(
            agent_name="architect",
            soul={"identity": "务实的技术架构师", "values": ["简单优先"], "style": "严谨"},
            role={"title": "架构师", "responsibilities": ["设计技术方案"], "rules": ["不过度设计"]},
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory", "read_file", "write_file"]
        ),
        create_agent_config_from_provider(
            agent_name="developer",
            soul={"identity": "注重代码质量的工程师", "values": ["可读性优先"], "style": "简洁"},
            role={"title": "开发工程师", "responsibilities": ["基于 spec 写代码", "写测试"], "rules": ["代码写到 src/"]},
            provider_config=default_provider,
            tools=["web_search", "retrieve_memory", "read_file", "write_file", "list_files", "execute_code", "search_files"]
        ),
        create_agent_config_from_provider(
            agent_name="tester",
            soul={"identity": "天生多疑的QA", "values": ["边界条件是bug的温床"], "style": "冷静精确"},
            role={"title": "测试工程师", "responsibilities": ["测试软件找bug"], "rules": ["安全问题标记blocker"]},
            provider_config=default_provider,
            tools=["retrieve_memory", "read_file", "execute_code", "list_files", "search_files"]
        ),
        create_agent_config_from_provider(
            agent_name="release",
            soul={"identity": "谨慎的发布守门人", "values": ["测试报告是唯一准绳"], "style": "保守果断"},
            role={"title": "发布经理", "responsibilities": ["审查测试报告", "发布版本"], "rules": ["有blocker不发布"]},
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
                    role=config.role.title,
                    soul=json.dumps(config.soul.model_dump(), ensure_ascii=False),
                    tools=json.dumps(config.tools),
                    skills=json.dumps(config.skills),
                    config=json.dumps(config.model_dump(), ensure_ascii=False, default=str),
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
