# -*- coding: utf-8 -*-
"""
Agent 核心类
"""
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel
from datetime import datetime
import json

# 导入新的配置模型
try:
    from agents.config_models import AgentConfigV2, AgentProviderConfig, ModelConfig
except ImportError:
    AgentConfigV2 = None
    AgentProviderConfig = None
    ModelConfig = None


class AgentConfig(BaseModel):
    """Agent 配置（旧版本，保持兼容）"""
    name: str
    role: str
    system_prompt: str
    tools: List[str] = []
    metadata: Dict[str, Any] = {}


class Message(BaseModel):
    """消息模型"""
    content: str
    role: str  # user, assistant, system, tool
    message_type: str = "text"
    metadata: Dict[str, Any] = {}


class ToolResult(BaseModel):
    """工具执行结果"""
    success: bool
    output: str
    error: Optional[str] = None


class MemoryItem(BaseModel):
    """记忆项"""
    content: str
    memory_type: str  # short_term, long_term, procedural
    importance: int = 5
    metadata: Dict[str, Any] = {}
    created_at: datetime = datetime.now()


class Agent:
    """
    AI Agent 核心类
    
    功能：
    1. 角色定义和系统提示
    2. 工具调用能力
    3. 记忆系统（短期/长期/程序性）
    4. 消息处理和响应生成
    5. 支持新的配置格式（多模型支持）
    """
    
    def __init__(self, config: Union[AgentConfig, 'AgentConfigV2'], llm_client=None):
        self.config = config
        self.llm = llm_client
        self.short_term_memory: List[MemoryItem] = []
        self.long_term_memory: List[MemoryItem] = []
        self.procedural_memory: List[MemoryItem] = []
        self.conversation_history: List[Message] = []
        self.available_tools: Dict[str, callable] = {}
        
        # 当前使用的模型（用于动态切换）
        self.current_model: Optional[str] = None
        
        # 初始化系统提示
        self.system_message = Message(
            content=config.system_prompt,
            role="system"
        )
    
    def set_model(self, model_id: str):
        """设置当前使用的模型"""
        self.current_model = model_id
    
    def get_available_models(self) -> List[str]:
        """获取可用的模型列表"""
        if AgentConfigV2 and isinstance(self.config, AgentConfigV2):
            if self.config.provider:
                return [m.id for m in self.config.provider.models]
        return []
    
    def get_model_info(self, model_id: str = None) -> Optional[Dict[str, Any]]:
        """获取模型信息"""
        if AgentConfigV2 and isinstance(self.config, AgentConfigV2):
            target_id = model_id or self.current_model or self.config.get_effective_model()
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
    
    def register_tool(self, name: str, func: callable, description: str):
        """注册工具"""
        self.available_tools[name] = {
            "function": func,
            "description": description
        }
    
    async def process_message(self, user_message: str, project_context: Dict = None) -> str:
        """
        处理用户消息并生成响应
        
        Args:
            user_message: 用户消息
            project_context: 项目上下文信息
            
        Returns:
            Agent 的响应
        """
        # 添加到短期记忆
        self._add_to_memory(
            MemoryItem(
                content=user_message,
                memory_type="short_term",
                importance=7
            )
        )
        
        # 构建对话上下文
        messages = self._build_conversation_context(user_message, project_context)
        
        # 调用 LLM 生成响应
        response = await self._generate_response(messages)
        
        # 处理工具调用
        if self._has_tool_calls(response):
            tool_results = await self._execute_tools(response, messages)
            response = self._format_tool_results(response, tool_results)
        
        # 保存对话历史
        self.conversation_history.append(Message(
            content=user_message,
            role="user"
        ))
        self.conversation_history.append(Message(
            content=response,
            role="assistant"
        ))
        
        # 更新长期记忆（重要信息）
        self._update_long_term_memory(user_message, response)
        
        return response
    
    def _build_conversation_context(self, user_message: str, project_context: Dict = None) -> List[Dict]:
        """构建对话上下文"""
        messages = []
        
        # 系统提示
        messages.append({
            "role": "system",
            "content": self._build_enhanced_system_prompt(project_context)
        })
        
        # 添加相关的短期记忆
        if self.short_term_memory:
            recent_memories = "\n".join([
                f"- {m.content}" 
                for m in self.short_term_memory[-5:]
            ])
            messages.append({
                "role": "system",
                "content": f"Recent context:\n{recent_memories}"
            })
        
        # 最近的对话历史
        for msg in self.conversation_history[-10:]:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # 当前用户消息
        messages.append({
            "role": "user",
            "content": user_message
        })
        
        return messages
    
    def _build_enhanced_system_prompt(self, project_context: Dict = None) -> str:
        """构建增强的系统提示"""
        prompt = self.config.system_prompt
        
        if project_context:
            prompt += f"\n\nCurrent project context:\n"
            prompt += f"Project: {project_context.get('name', 'N/A')}\n"
            prompt += f"Description: {project_context.get('description', 'N/A')}\n"
            
            # 添加其他 Agent 信息
            other_agents = project_context.get("other_agents", [])
            if other_agents:
                prompt += f"\nOther agents in this project:\n"
                for agent in other_agents:
                    prompt += f"- {agent['name']}: {agent['role']}\n"
        
        # 添加可用工具信息
        if self.available_tools:
            prompt += "\n\nAvailable tools:\n"
            for name, tool_info in self.available_tools.items():
                prompt += f"- {name}: {tool_info['description']}\n"
        
        return prompt
    
    async def _generate_response(self, messages: List[Dict]) -> str:
        """调用 LLM 生成响应"""
        if not self.llm:
            return "Error: LLM client not configured"
        
        # 获取要使用的模型
        model_to_use = self.current_model
        if AgentConfigV2 and isinstance(self.config, AgentConfigV2):
            model_to_use = model_to_use or self.config.get_effective_model()
        else:
            model_to_use = self.llm.model
        
        try:
            response = await self.llm.chat.completions.create(
                model=model_to_use,
                messages=messages,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error generating response: {str(e)}"
    
    def _has_tool_calls(self, response: str) -> bool:
        """检查响应是否包含工具调用"""
        # 简单的工具调用检测（可以改进为更复杂的解析）
        return any(tool_name in response for tool_name in self.available_tools.keys())
    
    async def _execute_tools(self, response: str, messages: List[Dict]) -> List[ToolResult]:
        """执行工具调用"""
        results = []
        
        # 简单的工具调用解析（实际应该使用更复杂的解析）
        for tool_name in self.available_tools.keys():
            if tool_name in response:
                try:
                    # 提取工具参数（简化版本）
                    result = await self.available_tools[tool_name]["function"]()
                    results.append(ToolResult(
                        success=True,
                        output=str(result)
                    ))
                except Exception as e:
                    results.append(ToolResult(
                        success=False,
                        output="",
                        error=str(e)
                    ))
        
        return results
    
    def _format_tool_results(self, response: str, results: List[ToolResult]) -> str:
        """格式化工具执行结果"""
        output = response + "\n\nTool execution results:\n"
        
        for i, result in enumerate(results, 1):
            if result.success:
                output += f"{i}. ✅ {result.output}\n"
            else:
                output += f"{i}. ❌ Error: {result.error}\n"
        
        return output
    
    def _add_to_memory(self, memory_item: MemoryItem):
        """添加到记忆系统"""
        if memory_item.memory_type == "short_term":
            self.short_term_memory.append(memory_item)
            # 保持短期记忆大小
            if len(self.short_term_memory) > 20:
                self.short_term_memory = self.short_term_memory[-20:]
        elif memory_item.memory_type == "long_term":
            self.long_term_memory.append(memory_item)
        elif memory_item.memory_type == "procedural":
            self.procedural_memory.append(memory_item)
    
    def _update_long_term_memory(self, user_message: str, response: str):
        """更新长期记忆（提取重要信息）"""
        # 简单的关键词匹配（实际应该使用 LLM 来提取重要信息）
        important_keywords = ["important", "remember", "key", "critical", "goal"]
        
        if any(keyword in user_message.lower() for keyword in important_keywords):
            self._add_to_memory(MemoryItem(
                content=f"User: {user_message[:200]}",
                memory_type="long_term",
                importance=8
            ))
    
    def get_memory_summary(self) -> Dict[str, Any]:
        """获取记忆摘要"""
        return {
            "short_term_count": len(self.short_term_memory),
            "long_term_count": len(self.long_term_memory),
            "procedural_count": len(self.procedural_memory),
            "conversation_turns": len(self.conversation_history) // 2
        }
    
    def reset_conversation(self):
        """重置对话历史（保留记忆）"""
        self.conversation_history = []
