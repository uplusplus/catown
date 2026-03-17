# -*- coding: utf-8 -*-
"""
LLM 客户端封装（OpenAI 兼容）
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import os
from openai import AsyncOpenAI


class LLMConfig(BaseModel):
    """LLM 配置"""
    api_key: str = os.getenv("LLM_API_KEY", "")
    base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model: str = os.getenv("LLM_MODEL", "gpt-4")
    temperature: float = 0.7
    max_tokens: int = 2000


class LLMClient:
    """
    LLM 客户端（支持 OpenAI 兼容接口）
    
    功能：
    1. 支持任何 OpenAI 兼容的 API
    2. 可配置的模型参数
    3. 统一的接口封装
    """
    
    def __init__(self, config: LLMConfig = None):
        self.config = config or LLMConfig()
        self.client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )
        self.model = self.config.model
    
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        发送聊天消息
        
        Args:
            messages: 消息列表
            **kwargs: 额外的参数
            
        Returns:
            LLM 的响应文本
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", self.config.temperature),
                max_tokens=kwargs.get("max_tokens", self.config.max_tokens)
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"LLM API error: {str(e)}")
    
    async def chat_with_tools(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """
        支持工具调用的聊天
        
        Args:
            messages: 消息列表
            tools: 工具定义列表
            
        Returns:
            包含响应和工具调用的字典
        """
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": self.config.temperature
            }
            
            if tools:
                kwargs["tools"] = tools
            
            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            
            result = {
                "content": choice.message.content,
                "tool_calls": choice.message.tool_calls if hasattr(choice.message, 'tool_calls') else None
            }
            
            return result
        except Exception as e:
            raise Exception(f"LLM API error with tools: {str(e)}")


# 全局 LLM 客户端实例
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def set_llm_client(client: LLMClient):
    """设置全局 LLM 客户端"""
    global _llm_client
    _llm_client = client
