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


class SoulConfig(BaseModel):
    """Agent 灵魂配置"""
    identity: str
    values: List[str] = []
    style: str = ""
    quirks: str = ""


class RoleConfig(BaseModel):
    """Agent 角色配置"""
    title: str
    responsibilities: List[str] = []
    rules: List[str] = []


class MemoryConfig(BaseModel):
    """Agent 记忆配置"""
    long_term_enabled: bool = True
    auto_generalize: bool = False


class SleepConfig(BaseModel):
    """Agent 睡眠配置"""
    enabled: bool = True
    idle_threshold_minutes: int = 30
    preferred_window_start: str = "23:00"
    preferred_window_end: str = "07:00"
    max_retain_days: int = 30
    long_term_max_tokens: int = 100000


class AgentConfigV2(BaseModel):
    """Agent 配置 V2 - SOUL/ROLE/RULES 三层结构"""
    name: str
    soul: SoulConfig
    role: RoleConfig
    tools: List[str] = []
    skills: List[str] = []
    memory: MemoryConfig = MemoryConfig()
    sleep: SleepConfig = SleepConfig()
    provider: Optional[AgentProviderConfig] = None
    default_model: Optional[str] = None
    metadata: Dict[str, Any] = {}
    
    def get_effective_base_url(self) -> str:
        """获取有效的 base URL"""
        if self.provider:
            return self.provider.baseUrl
        return "https://api.openai.com/v1"
    
    def get_effective_api_key(self) -> str:
        """获取有效的 API key"""
        if self.provider:
            return self.provider.apiKey
        return ""
    
    def get_effective_model(self) -> str:
        """获取有效的模型"""
        if self.default_model:
            return self.default_model
        if self.provider and self.provider.models:
            return self.provider.models[0].id
        return "gpt-4"
    
    def build_system_prompt(self, project_memory: str = "", long_term_memory: str = "") -> str:
        """组装 system_prompt（SOUL + ROLE + RULES + MEMORY）"""
        parts = []
        
        # 1. 灵魂层
        parts.append(f"你是 {self.name}。{self.soul.identity}")
        if self.soul.values:
            parts.append("你的原则：\n" + "\n".join(f"- {v}" for v in self.soul.values))
        if self.soul.style:
            parts.append(f"沟通风格：{self.soul.style}")
        
        # 2. 角色层
        if self.role.responsibilities:
            parts.append("## 职责\n" + "\n".join(f"- {r}" for r in self.role.responsibilities))
        
        # 3. 规则层
        if self.role.rules:
            parts.append("## 规则\n" + "\n".join(f"- {r}" for r in self.role.rules))
        
        # 4. 项目记忆注入
        if project_memory:
            parts.append(f"## 项目上下文\n{project_memory}")
        
        # 5. 长期记忆注入
        if long_term_memory:
            parts.append(f"## 你的经验\n{long_term_memory}")
        
        return "\n\n".join(parts)
    
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
    soul: Dict[str, Any],
    role: Dict[str, Any],
    provider_config: Dict[str, Any],
    tools: List[str] = None,
    skills: List[str] = None,
    memory: Dict[str, Any] = None,
    sleep: Dict[str, Any] = None,
    default_model: str = None
) -> AgentConfigV2:
    """从 provider 配置创建 Agent 配置"""
    provider = parse_agent_config(provider_config)

    return AgentConfigV2(
        name=agent_name,
        soul=SoulConfig(**soul),
        role=RoleConfig(**role),
        tools=tools or [],
        skills=skills or [],
        memory=MemoryConfig(**(memory or {})),
        sleep=SleepConfig(**(sleep or {})),
        provider=provider,
        default_model=default_model
    )
