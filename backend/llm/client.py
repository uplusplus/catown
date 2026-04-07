# -*- coding: utf-8 -*-
"""
LLM 客户端封装（OpenAI 兼容）

配置来源：统一从 config 模块读取 Settings（.env → 环境变量）
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from openai import AsyncOpenAI

from config import settings


class LLMClient:
    """
    LLM 客户端（支持 OpenAI 兼容接口）
    
    配置统一从 config.settings 读取，不再从环境变量直接硬编码。
    """
    
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL
        )
        self.model = settings.LLM_MODEL
    
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """发送聊天消息"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2000)
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"LLM API error: {str(e)}")
    
    async def chat_with_tools(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """支持工具调用的聊天"""
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7
            }
            if tools:
                kwargs["tools"] = tools
            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            return {
                "content": choice.message.content,
                "tool_calls": choice.message.tool_calls if hasattr(choice.message, 'tool_calls') else None
            }
        except Exception as e:
            raise Exception(f"LLM API error with tools: {str(e)}")


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
