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
            # 提取基本配置
            role = agent_data.get("role", "Agent")
            system_prompt = agent_data.get("system_prompt", "")
            tools = agent_data.get("tools", [])
            default_model = agent_data.get("default_model")
            
            # 提取 provider 配置
            provider_data = agent_data.get("provider", {})
            
            if provider_data:
                # 使用新配置格式
                config = create_agent_config_from_provider(
                    agent_name=agent_name,
                    role=role,
                    system_prompt=system_prompt,
                    provider_config=provider_data,
                    tools=tools,
                    default_model=default_model
                )
            else:
                # 兼容旧配置格式
                config = AgentConfigV2(
                    name=agent_name,
                    role=role,
                    system_prompt=system_prompt,
                    tools=tools,
                    llm_base_url=agent_data.get("llm_base_url"),
                    llm_api_key=agent_data.get("llm_api_key"),
                    llm_model=agent_data.get("llm_model")
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
                "role": config.role,
                "system_prompt": config.system_prompt,
                "tools": config.tools,
            }
            
            if config.default_model:
                agent_data["default_model"] = config.default_model
            
            if config.provider:
                agent_data["provider"] = config.provider.model_dump(by_alias=True)
            else:
                # 兼容旧格式
                if config.llm_base_url:
                    agent_data["llm_base_url"] = config.llm_base_url
                if config.llm_api_key:
                    agent_data["llm_api_key"] = config.llm_api_key
                if config.llm_model:
                    agent_data["llm_model"] = config.llm_model
            
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
                    "role": "通用助手",
                    "system_prompt": "You are a helpful assistant.",
                    "tools": ["web_search", "retrieve_memory"],
                    "provider": {
                        "baseUrl": "http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
                        "apiKey": "your-api-key",
                        "auth": "api-key",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "GLM-V5-128K",
                                "name": "GLM-V5-128K",
                                "api": "openai-completions",
                                "reasoning": False,
                                "input": ["text"],
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0
                                },
                                "contextWindow": 128000,
                                "maxTokens": 16384
                            }
                        ]
                    }
                },
                "coder": {
                    "role": "代码专家",
                    "system_prompt": "You are an expert programmer.",
                    "tools": ["web_search", "execute_code", "retrieve_memory"],
                    "default_model": "GLM-V5-128K",
                    "provider": {
                        "baseUrl": "http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
                        "apiKey": "your-api-key",
                        "auth": "api-key",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "Qwen-V3.5-256K",
                                "name": "Qwen-V3.5-256K",
                                "api": "openai-completions",
                                "reasoning": False,
                                "input": ["text", "image"],
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0
                                },
                                "contextWindow": 256000,
                                "maxTokens": 32768
                            },
                            {
                                "id": "GLM-V5-128K",
                                "name": "GLM-V5-128K",
                                "api": "openai-completions",
                                "reasoning": False,
                                "input": ["text"],
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0
                                },
                                "contextWindow": 128000,
                                "maxTokens": 16384
                            }
                        ]
                    }
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
