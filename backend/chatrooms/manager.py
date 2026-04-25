# -*- coding: utf-8 -*-
"""
聊天室管理器
"""
from typing import Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel
import asyncio
import json
import logging

from agents.identity import DEFAULT_AGENT_TYPE, find_agent_by_type
from services.chat_prompt_builder import assemble_chat_messages

logger = logging.getLogger("catown.chatroom")


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
    
    async def create_chatroom(
        self,
        project_id: Optional[int],
        project_name: str,
        title: Optional[str] = None,
        session_type: str = "project-bound",
        is_visible_in_chat_list: bool = False,
        source_chatroom_id: Optional[int] = None,
    ) -> int:
        """创建新的聊天室"""
        from models.database import get_db, Chatroom
        
        db = next(get_db())
        try:
            chatroom = Chatroom(
                project_id=project_id,
                title=title or project_name,
                session_type=session_type,
                is_visible_in_chat_list=is_visible_in_chat_list,
                source_chatroom_id=source_chatroom_id,
            )
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
                    agent_name = agent.type
            
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
                .filter(Message.message_type.notin_(["runtime_card", "runtime_event"]))
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
                    agent_name=msg.agent.type if msg.agent else None,
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

        流程：
        1. 保存用户消息
        2. 获取聊天室关联的项目和 Agents
        3. 解析 @mention，选择目标 Agent
        4. 调用 LLM 生成响应
        5. 保存 Agent 响应
        """
        import re
        responses = []

        # 发送用户消息
        user_msg = await self.send_message(chatroom_id, None, user_message, "text")
        responses.append(user_msg)

        # 获取聊天室的 Agent 列表
        chatroom = self.get_chatroom(chatroom_id)
        if not chatroom:
            return responses

        # 获取数据库中的项目和 Agent
        from models.database import get_db, Project, Agent, AgentAssignment, Chatroom as ChatroomDB

        db = next(get_db())
        try:
            # 查找聊天室关联的项目；可通过 source_chatroom_id 继承主项目上下文
            db_chatroom = db.query(ChatroomDB).filter(ChatroomDB.id == chatroom_id).first()
            if not db_chatroom:
                return responses

            project_id = db_chatroom.project_id
            parent_chatroom = db_chatroom
            visited_ids = set()
            while not project_id and parent_chatroom and parent_chatroom.source_chatroom_id:
                if parent_chatroom.source_chatroom_id in visited_ids:
                    break
                visited_ids.add(parent_chatroom.source_chatroom_id)
                parent_chatroom = db.query(ChatroomDB).filter(
                    ChatroomDB.id == parent_chatroom.source_chatroom_id
                ).first()
                project_id = parent_chatroom.project_id if parent_chatroom else None

            if not project_id:
                return responses

            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                return responses

            # 获取项目关联的 Agents
            assignments = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == project.id
            ).all()
            agent_ids = [a.agent_id for a in assignments]
            agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

            if not agents:
                return responses

            # 注册为协作者
            from agents.collaboration import collaboration_coordinator, AgentCollaborator
            for agent in agents:
                if agent.id not in collaboration_coordinator.collaborators:
                    collaborator = AgentCollaborator(
                        agent_id=agent.id,
                        agent_name=agent.type,
                        chatroom_id=chatroom_id
                    )
                    collaboration_coordinator.register_collaborator(collaborator)

            # 解析 @mention
            mentioned_names = re.findall(r'@(\w+)', user_message)

            # 多 Agent 协作
            if len(mentioned_names) > 1:
                for agent_name in mentioned_names:
                    agent = find_agent_by_type(agents, agent_name)
                    if agent:
                        resp_content = await self._call_agent_llm(agent, user_message, chatroom_id, db)
                        if resp_content:
                            agent_msg = await self.send_message(
                                chatroom_id, agent.id, resp_content, "agent_response",
                                agent_name=agent.name
                            )
                            responses.append(agent_msg)
                return responses

            # 单 Agent 响应
            target_agent = None
            if mentioned_names:
                target_agent = find_agent_by_type(agents, mentioned_names[0])
            if not target_agent:
                target_agent = find_agent_by_type(agents, DEFAULT_AGENT_TYPE) or agents[0]

            if target_agent:
                resp_content = await self._call_agent_llm(target_agent, user_message, chatroom_id, db)
                if resp_content:
                    agent_msg = await self.send_message(
                        chatroom_id, target_agent.id, resp_content, "agent_response",
                        agent_name=target_agent.name
                    )
                    responses.append(agent_msg)

            return responses
        finally:
            db.close()

    async def _call_agent_llm(self, agent, user_message: str, chatroom_id: int, db) -> Optional[str]:
        """调用 Agent 的 LLM 生成响应"""
        from llm.client import get_llm_client_for_agent
        from models.database import Agent, AgentAssignment, Chatroom as ChatroomDB, Message, Project

        try:
            llm_client = get_llm_client_for_agent(agent.type)

            # 构建消息历史
            db_chatroom = db.query(ChatroomDB).filter(ChatroomDB.id == chatroom_id).first()
            project = None
            source_chatroom = None
            agents = []
            if db_chatroom:
                if db_chatroom.project_id:
                    project = db.query(Project).filter(Project.id == db_chatroom.project_id).first()
                    assignments = db.query(AgentAssignment).filter(
                        AgentAssignment.project_id == db_chatroom.project_id
                    ).all()
                    assigned_ids = [assignment.agent_id for assignment in assignments]
                    if assigned_ids:
                        agents = db.query(Agent).filter(Agent.id.in_(assigned_ids)).all()
                if db_chatroom.source_chatroom_id:
                    source_chatroom = (
                        db.query(ChatroomDB)
                        .filter(ChatroomDB.id == db_chatroom.source_chatroom_id)
                        .first()
                    )

            recent_messages = await self.get_messages(chatroom_id, limit=20)

            raw_tools = getattr(agent, "tools", None)
            if isinstance(raw_tools, str):
                try:
                    parsed_tools = json.loads(raw_tools or "[]")
                except (TypeError, json.JSONDecodeError):
                    parsed_tools = []
                tool_names = [str(tool) for tool in parsed_tools if tool]
            elif isinstance(raw_tools, list):
                tool_names = [str(tool) for tool in raw_tools if tool]
            else:
                tool_names = []

            messages = assemble_chat_messages(
                db=db,
                agent=agent,
                agent_name=getattr(agent, "name", "Agent"),
                model_id=getattr(llm_client, "model", ""),
                chatroom=db_chatroom,
                project=project,
                agents=agents,
                recent_messages=recent_messages,
                user_message=user_message,
                available_tools=tool_names,
                selector_profile="fallback_chat",
            )

            response = await llm_client.chat(messages)
            return response
        except Exception as e:
            logger.error(f"LLM call failed for agent {agent.name}: {e}")
            return None


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
