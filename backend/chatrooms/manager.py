# -*- coding: utf-8 -*-
"""
聊天室管理器
"""
from typing import Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel
import asyncio
import json


class ChatroomMessage(BaseModel):
    """聊天室消息"""
    id: int
    chatroom_id: int
    agent_id: Optional[int]
    agent_name: Optional[str]
    content: str
    message_type: str
    created_at: datetime
    metadata: Dict = {}


class ChatroomManager:
    """
    聊天室管理器
    
    功能：
    1. 聊天室创建和管理
    2. 消息路由和分发
    3. Agent 协作协调
    """
    
    def __init__(self):
        self.chatrooms: Dict[int, 'Chatroom'] = {}
        self.message_queue: asyncio.Queue = asyncio.Queue()
    
    async def create_chatroom(self, project_id: int, project_name: str) -> int:
        """创建新的聊天室"""
        from models.database import get_db, Chatroom
        
        db = next(get_db())
        try:
            chatroom = Chatroom(project_id=project_id)
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            
            self.chatrooms[chatroom.id] = ChatroomInstance(
                id=chatroom.id,
                project_id=project_id,
                project_name=project_name
            )
            
            print(f"✅ Created chatroom {chatroom.id} for project {project_name}")
            return chatroom.id
        finally:
            db.close()
    
    def get_chatroom(self, chatroom_id: int) -> Optional['ChatroomInstance']:
        """获取聊天室"""
        return self.chatrooms.get(chatroom_id)
    
    async def send_message(self, chatroom_id: int, agent_id: Optional[int], 
                          content: str, message_type: str = "text",
                          metadata: Dict = None, agent_name: str = None) -> ChatroomMessage:
        """
        发送消息到聊天室
        
        Args:
            chatroom_id: 聊天室 ID
            agent_id: Agent ID（None 表示用户）
            content: 消息内容
            message_type: 消息类型
            metadata: 元数据
            agent_name: Agent 名称（可选，用于覆盖）
            
        Returns:
            发送的消息对象
        """
        from models.database import get_db, Message, Agent
        
        db = next(get_db())
        try:
            message = Message(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                content=content,
                message_type=message_type,
                metadata_json=json.dumps(metadata or {})
            )
            db.add(message)
            db.commit()
            db.refresh(message)
            
            # 获取 Agent 名称
            if agent_name is None and agent_id:
                agent = db.query(Agent).filter(Agent.id == agent_id).first()
                if agent:
                    agent_name = agent.name
            
            chat_message = ChatroomMessage(
                id=message.id,
                chatroom_id=message.chatroom_id,
                agent_id=message.agent_id,
                agent_name=agent_name,
                content=message.content,
                message_type=message.message_type,
                created_at=message.created_at,
                metadata=metadata or {}
            )
            
            # 添加到消息队列
            await self.message_queue.put(chat_message)
            
            return chat_message
        finally:
            db.close()
    
    async def get_messages(self, chatroom_id: int, limit: int = 50) -> List[ChatroomMessage]:
        """获取聊天室消息"""
        from models.database import get_db, Message, Agent
        
        db = next(get_db())
        try:
            messages = (
                db.query(Message)
                .join(Agent, Message.agent_id == Agent.id, isouter=True)
                .filter(Message.chatroom_id == chatroom_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )
            
            result = []
            for msg in reversed(messages):
                result.append(ChatroomMessage(
                    id=msg.id,
                    chatroom_id=msg.chatroom_id,
                    agent_id=msg.agent_id,
                    agent_name=msg.agent.name if msg.agent else None,
                    content=msg.content,
                    message_type=msg.message_type,
                    created_at=msg.created_at,
                    metadata=json.loads(msg.metadata_json or "{}")
                ))
            
            return result
        finally:
            db.close()
    
    async def process_user_message(self, chatroom_id: int, user_message: str) -> List[ChatroomMessage]:
        """
        处理用户消息，触发 Agent 协作
        
        Args:
            chatroom_id: 聊天室 ID
            user_message: 用户消息
            
        Returns:
            Agent 响应消息列表
        """
        responses = []
        
        # 发送用户消息
        user_msg = await self.send_message(chatroom_id, None, user_message, "text")
        responses.append(user_msg)
        
        # 获取聊天室的 Agent 列表
        chatroom = self.get_chatroom(chatroom_id)
        if not chatroom:
            return responses
        
        # TODO: 实现 Agent 协作逻辑
        # 这里简化为只让一个 Agent 响应
        # 实际应该根据消息内容路由到合适的 Agent
        
        return responses


class ChatroomInstance:
    """聊天室实例"""
    
    def __init__(self, id: int, project_id: int, project_name: str):
        self.id = id
        self.project_id = project_id
        self.project_name = project_name
        self.agents: List[int] = []  # Agent ID 列表
        self.created_at = datetime.now()
        self.is_active = True
    
    def add_agent(self, agent_id: int):
        """添加 Agent 到聊天室"""
        if agent_id not in self.agents:
            self.agents.append(agent_id)
    
    def remove_agent(self, agent_id: int):
        """从聊天室移除 Agent"""
        if agent_id in self.agents:
            self.agents.remove(agent_id)
    
    def get_agent_count(self) -> int:
        """获取 Agent 数量"""
        return len(self.agents)


# 全局聊天室管理器
chatroom_manager = ChatroomManager()
