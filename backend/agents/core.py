# -*- coding: utf-8 -*-
"""
Agent 核心类

职责：
1. Agent 配置持有（角色、系统提示、工具列表、模型信息）
2. 记忆系统（短期/长期/程序性）
3. 对话历史管理

注意：实际的 LLM 调用和工具执行由 routes/api.py 的 trigger_agent_response 处理，
本类不维护独立的工具注册和 LLM 调用逻辑。
"""
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel
from datetime import datetime

try:
    from agents.config_models import AgentConfigV2
except ImportError:
    AgentConfigV2 = None


class AgentConfig(BaseModel):
    """Agent 配置（旧版兼容）"""
    name: str
    role: str
    system_prompt: str
    tools: List[str] = []
    metadata: Dict[str, Any] = {}


class MemoryItem(BaseModel):
    """记忆项"""
    content: str
    memory_type: str  # short_term, long_term, procedural
    importance: int = 5
    metadata: Dict[str, Any] = {}
    created_at: datetime = datetime.now


class Agent:
    """
    AI Agent 核心类
    
    持有 Agent 配置、记忆和对话历史。
    实际的 LLM 调用和工具执行在 routes/api.py 中完成。
    """
    
    def __init__(self, config: Union[AgentConfig, 'AgentConfigV2'], llm_client=None):
        self.config = config
        self.llm = llm_client
        self.short_term_memory: List[MemoryItem] = []
        self.long_term_memory: List[MemoryItem] = []
        self.procedural_memory: List[MemoryItem] = []
        self.conversation_history: List[Dict[str, str]] = []
        self.current_model: Optional[str] = None
    
    @property
    def name(self) -> str:
        return self.config.name
    
    @property
    def role(self) -> str:
        return self.config.role
    
    @property
    def system_prompt(self) -> str:
        return self.config.system_prompt
    
    @property
    def tools(self) -> List[str]:
        return self.config.tools
    
    def set_model(self, model_id: str):
        """设置当前使用的模型"""
        self.current_model = model_id
    
    def get_available_models(self) -> List[str]:
        """获取可用的模型列表"""
        if AgentConfigV2 and isinstance(self.config, AgentConfigV2):
            if self.config.provider:
                return [m.id for m in self.config.provider.models]
        return []
    
    def get_effective_model(self) -> str:
        """获取实际生效的模型 ID"""
        if self.current_model:
            return self.current_model
        if AgentConfigV2 and isinstance(self.config, AgentConfigV2):
            return self.config.get_effective_model()
        return "gpt-4"
    
    def get_model_info(self, model_id: str = None) -> Optional[Dict[str, Any]]:
        """获取模型详细信息"""
        if AgentConfigV2 and isinstance(self.config, AgentConfigV2):
            target_id = model_id or self.get_effective_model()
            model_config = self.config.get_model_config(target_id)
            if model_config:
                return {
                    "id": model_config.id,
                    "name": model_config.name,
                    "context_window": model_config.contextWindow,
                    "max_tokens": model_config.maxTokens,
                    "capabilities": model_config.input,
                    "reasoning": model_config.reasoning
                }
        return None
    
    # --- 记忆系统 ---
    
    def add_memory(self, content: str, memory_type: str = "short_term", importance: int = 5):
        """添加记忆"""
        item = MemoryItem(
            content=content,
            memory_type=memory_type,
            importance=importance
        )
        if memory_type == "short_term":
            self.short_term_memory.append(item)
            if len(self.short_term_memory) > 20:
                self.short_term_memory = self.short_term_memory[-20:]
        elif memory_type == "long_term":
            self.long_term_memory.append(item)
        elif memory_type == "procedural":
            self.procedural_memory.append(item)
    
    def get_memory_summary(self) -> Dict[str, Any]:
        """获取记忆摘要"""
        return {
            "short_term_count": len(self.short_term_memory),
            "long_term_count": len(self.long_term_memory),
            "procedural_count": len(self.procedural_memory),
            "conversation_turns": len(self.conversation_history) // 2
        }
    
    def add_conversation_turn(self, role: str, content: str):
        """添加对话记录"""
        self.conversation_history.append({"role": role, "content": content})
    
    def reset_conversation(self):
        """重置对话历史（保留记忆）"""
        self.conversation_history = []
