"""
Agent 配置模型 - 支持灵活的配置格式
"""
from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime


class ModelCost(BaseModel):
    """模型成本配置"""
    input: float = 0.0
    output: float = 0.0
    cacheRead: float = Field(default=0.0, alias="cacheRead")
    cacheWrite: float = Field(default=0.0, alias="cacheWrite")


class ModelConfig(BaseModel):
    """单个模型配置"""
    id: str
    name: str
    api: str = "openai-completions"
    reasoning: bool = False
    input: List[str] = ["text"]  # text, image, etc.
    cost: ModelCost = ModelCost()
    contextWindow: int = Field(default=128000, alias="contextWindow")
    maxTokens: int = Field(default=16384, alias="maxTokens")
    
    class Config:
        populate_by_name = True


class AgentProviderConfig(BaseModel):
    """Agent 提供者配置（支持新的配置格式）"""
    baseUrl: str = Field(alias="baseUrl")
    apiKey: str = Field(alias="apiKey", default="")
    auth: str = "api-key"  # api-key, bearer, etc.
    api: str = "openai-completions"
    models: List[ModelConfig] = []
    
    class Config:
        populate_by_name = True
    
    def get_default_model(self) -> Optional[ModelConfig]:
        """获取默认模型（第一个模型）"""
        return self.models[0] if self.models else None
    
    def get_model_by_id(self, model_id: str) -> Optional[ModelConfig]:
        """根据 ID 获取模型"""
        for model in self.models:
            if model.id == model_id:
                return model
        return None
    
    def get_models_by_capability(self, capability: str) -> List[ModelConfig]:
        """根据能力筛选模型（如支持 image）"""
        return [m for m in self.models if capability in m.input]


class AgentConfigV2(BaseModel):
    """Agent 配置 V2 - 支持新的配置格式"""
    name: str
    role: str
    system_prompt: str
    tools: List[str] = []
    provider: Optional[AgentProviderConfig] = None
    default_model: Optional[str] = None  # 默认使用的模型 ID
    metadata: Dict[str, Any] = {}
    
    # 兼容旧配置
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    
    def get_effective_base_url(self) -> str:
        """获取有效的 base URL"""
        if self.provider:
            return self.provider.baseUrl
        return self.llm_base_url or "https://api.openai.com/v1"
    
    def get_effective_api_key(self) -> str:
        """获取有效的 API key"""
        if self.provider:
            return self.provider.apiKey
        return self.llm_api_key or ""
    
    def get_effective_model(self) -> str:
        """获取有效的模型"""
        # 优先使用指定的默认模型
        if self.default_model:
            return self.default_model
        
        # 然后使用 provider 的第一个模型
        if self.provider and self.provider.models:
            return self.provider.models[0].id
        
        # 最后使用旧配置的模型
        return self.llm_model or "gpt-4"
    
    def get_model_config(self, model_id: str = None) -> Optional[ModelConfig]:
        """获取模型配置"""
        if not self.provider:
            return None
        
        target_id = model_id or self.get_effective_model()
        return self.provider.get_model_by_id(target_id)


# 示例配置
EXAMPLE_AGENT_CONFIG = {
    "myagent": {
        "baseUrl": "http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
        "apiKey": "__OPENCLAW_REDACTED__",
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
                    "cacheWrite": 0,
                },
                "contextWindow": 256000,
                "maxTokens": 32768,
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
                    "cacheWrite": 0,
                },
                "contextWindow": 128000,
                "maxTokens": 16384,
            },
        ],
    }
}


def parse_agent_config(config_dict: Dict[str, Any]) -> AgentProviderConfig:
    """
    解析 Agent 配置
    
    Args:
        config_dict: 配置字典，格式如 EXAMPLE_AGENT_CONFIG["myagent"]
    
    Returns:
        AgentProviderConfig 对象
    """
    return AgentProviderConfig(**config_dict)


def create_agent_config_from_provider(
    agent_name: str,
    role: str,
    system_prompt: str,
    provider_config: Dict[str, Any],
    tools: List[str] = None,
    default_model: str = None
) -> AgentConfigV2:
    """
    从 provider 配置创建 Agent 配置
    
    Args:
        agent_name: Agent 名称
        role: Agent 角色
        system_prompt: 系统提示
        provider_config: provider 配置字典
        tools: 工具列表
        default_model: 默认模型 ID
    
    Returns:
        AgentConfigV2 对象
    """
    provider = parse_agent_config(provider_config)
    
    return AgentConfigV2(
        name=agent_name,
        role=role,
        system_prompt=system_prompt,
        tools=tools or [],
        provider=provider,
        default_model=default_model
    )
