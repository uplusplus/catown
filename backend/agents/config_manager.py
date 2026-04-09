"""
Agent 配置管理器 - 支持从文件加载配置
"""
import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from agents.config_models import (
    AgentConfigV2,
    AgentProviderConfig,
    SoulConfig,
    RoleConfig,
    MemoryConfig,
    SleepConfig,
    create_agent_config_from_provider
)
from agents.core import AgentConfig


class AgentConfigManager:
    """Agent 配置管理器"""
    
    def __init__(self, config_dir: str = "configs"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(exist_ok=True)
        self.configs: Dict[str, AgentConfigV2] = {}
    
    def load_from_json(self, file_path: str) -> Dict[str, AgentConfigV2]:
        """
        从 JSON 文件加载配置
        
        文件格式示例：
        {
            "agents": {
                "assistant": {
                    "role": "通用助手",
                    "system_prompt": "...",
                    "provider": {
                        "baseUrl": "...",
                        "apiKey": "...",
                        "models": [...]
                    }
                }
            }
        }
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return self._parse_config_data(data)
    
    def load_from_yaml(self, file_path: str) -> Dict[str, AgentConfigV2]:
        """从 YAML 文件加载配置"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        return self._parse_config_data(data)
    
    def _parse_config_data(self, data: Dict) -> Dict[str, AgentConfigV2]:
        """解析配置数据"""
        configs = {}

        agents_data = data.get("agents", {})

        for agent_name, agent_data in agents_data.items():
            # 提取 soul/role
            soul_data = agent_data.get("soul", {})
            role_data = agent_data.get("role", {})

            # 如果 role 是旧格式字符串，转为 dict
            if isinstance(role_data, str):
                role_data = {"title": role_data, "responsibilities": [], "rules": []}

            tools = agent_data.get("tools", [])
            skills = agent_data.get("skills", [])
            memory = agent_data.get("memory", {})
            sleep = agent_data.get("sleep", {})
            default_model = agent_data.get("default_model")

            # 提取 provider 配置
            provider_data = agent_data.get("provider", {})

            if provider_data:
                config = create_agent_config_from_provider(
                    agent_name=agent_name,
                    soul=soul_data,
                    role=role_data,
                    provider_config=provider_data,
                    tools=tools,
                    skills=skills,
                    memory=memory,
                    sleep=sleep,
                    default_model=default_model
                )
            else:
                config = AgentConfigV2(
                    name=agent_name,
                    soul=SoulConfig(**soul_data),
                    role=RoleConfig(**role_data),
                    tools=tools,
                    skills=skills,
                    memory=MemoryConfig(**memory),
                    sleep=SleepConfig(**sleep),
                    default_model=default_model,
                )

            configs[agent_name] = config

        self.configs.update(configs)
        return configs
    
    def get_config(self, agent_name: str) -> Optional[AgentConfigV2]:
        """获取指定 Agent 的配置"""
        return self.configs.get(agent_name)
    
    def list_configs(self) -> List[str]:
        """列出所有配置"""
        return list(self.configs.keys())
    
    def save_to_json(self, file_path: str):
        """保存配置到 JSON 文件"""
        data = {"agents": {}}
        
        for name, config in self.configs.items():
            agent_data = {
                "name": config.name,
                "soul": config.soul.model_dump(),
                "role": config.role.model_dump(),
                "tools": config.tools,
                "skills": config.skills,
                "memory": config.memory.model_dump(),
                "sleep": config.sleep.model_dump(),
            }
            
            if config.default_model:
                agent_data["default_model"] = config.default_model

            if config.provider:
                agent_data["provider"] = config.provider.model_dump(by_alias=True)

            data["agents"][name] = agent_data
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def add_config(self, config: AgentConfigV2):
        """添加配置"""
        self.configs[config.name] = config
    
    def create_default_config_file(self, file_path: str):
        """创建默认配置文件"""
        default_config = {
            "agents": {
                "assistant": {
                    "name": "Bot",
                    "soul": {
                        "identity": "一个万能打杂的助手",
                        "values": ["能帮就帮", "做事有条理"],
                        "style": "友好随和"
                    },
                    "role": {
                        "title": "通用助手",
                        "responsibilities": ["回答问题", "协助处理一般任务"],
                        "rules": ["不确定时提问"]
                    },
                    "tools": ["web_search", "retrieve_memory"]
                }
            }
        }

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)


# 便捷函数
def load_agent_configs(config_file: str) -> Dict[str, AgentConfigV2]:
    """加载 Agent 配置"""
    manager = AgentConfigManager()
    
    if config_file.endswith('.json'):
        return manager.load_from_json(config_file)
    elif config_file.endswith('.yaml') or config_file.endswith('.yml'):
        return manager.load_from_yaml(config_file)
    else:
        raise ValueError(f"Unsupported config file format: {config_file}")
