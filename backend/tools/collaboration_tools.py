# -*- coding: utf-8 -*-
"""
Collaboration Tools

Tools that enable agents to collaborate with each other:
- delegate_task: Delegate a task to another agent
- broadcast_message: Send a message to all agents
- check_task_status: Check status of a delegated task
- list_collaborators: List available collaborators
"""
from .base import BaseTool
from typing import Optional, Dict, Any, List
import json


class DelegateTaskTool(BaseTool):
    """Tool for delegating tasks to other agents"""
    
    name = "delegate_task"
    description = "Delegate a task to another agent for collaboration. Use this when you need another agent's specialized skills."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(
        self,
        target_agent_name: str,
        task_title: str,
        task_description: str,
        context: str = "",
        **kwargs
    ) -> str:
        """
        Delegate a task to another agent
        
        Args:
            target_agent_name: Name of the agent to delegate to (e.g., 'coder', 'researcher', 'reviewer')
            task_title: Short title for the task
            task_description: Detailed description of the task
            context: Additional context or data needed for the task
            
        Returns:
            Delegation status and task ID
        """
        # Get current agent info from kwargs
        current_agent_id = kwargs.get('agent_id', 0)
        current_agent_name = kwargs.get('agent_name', 'unknown')
        chatroom_id = kwargs.get('chatroom_id', 0)
        
        # Find target agent
        target_agent_id = None
        if self.coordinator:
            for aid, collab in self.coordinator.collaborators.items():
                if collab.agent_name == target_agent_name:
                    target_agent_id = aid
                    break

        if not target_agent_id:
            # 从全局数据库查找并自动注册为协作者
            try:
                from models.database import get_db, Agent as DBAgent
                from agents.collaboration import AgentCollaborator
                db = next(get_db())
                try:
                    db_agent = db.query(DBAgent).filter(
                        DBAgent.name == target_agent_name,
                        DBAgent.is_active == True
                    ).first()
                    if db_agent and self.coordinator:
                        collaborator = AgentCollaborator(
                            agent_id=db_agent.id,
                            agent_name=db_agent.name,
                            chatroom_id=chatroom_id
                        )
                        self.coordinator.register_collaborator(collaborator)
                        target_agent_id = db_agent.id
                finally:
                    db.close()
            except Exception:
                pass

        if not target_agent_id:
            available = []
            if self.coordinator:
                available = [c.agent_name for c in self.coordinator.collaborators.values()]
            return f"[Delegate Task] Error: Agent '{target_agent_name}' not found. Available agents: {available}"
        
        # Create task
        from agents.collaboration import CollaborationTask, TaskStatus, uuid, datetime
        task = CollaborationTask(
            id=str(uuid.uuid4()),
            title=task_title,
            description=task_description,
            status=TaskStatus.DELEGATED,
            created_by_agent_id=current_agent_id,
            assigned_to_agent_id=target_agent_id,
            chatroom_id=chatroom_id,
            metadata={"context": context, "delegator": current_agent_name}
        )
        
        # Register task
        if self.coordinator:
            self.coordinator.task_registry[task.id] = task
            
            # Notify target agent
            from agents.collaboration import CollaborationMessage, CollaborationMessageType
            message = CollaborationMessage(
                id=str(uuid.uuid4()),
                message_type=CollaborationMessageType.TASK_REQUEST,
                from_agent_id=current_agent_id,
                from_agent_name=current_agent_name,
                to_agent_id=target_agent_id,
                to_agent_name=target_agent_name,
                chatroom_id=chatroom_id,
                content=f"**Task: {task_title}**\n\n{task_description}\n\nContext: {context}",
                task_id=task.id,
                metadata={"task": task.dict()}
            )
            
            # Route message
            await self.coordinator.route_message(message)
        
        return f"[Delegate Task] Task '{task_title}' delegated to {target_agent_name}. Task ID: {task.id}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "Name of the agent to delegate to (analyst, architect, developer, tester, release, assistant)"
                },
                "task_title": {
                    "type": "string",
                    "description": "Short title for the delegated task"
                },
                "task_description": {
                    "type": "string",
                    "description": "Detailed description of what needs to be done"
                },
                "context": {
                    "type": "string",
                    "description": "Additional context or data for the task",
                    "default": ""
                }
            },
            "required": ["target_agent_name", "task_title", "task_description"]
        }


class BroadcastMessageTool(BaseTool):
    """Tool for broadcasting messages to all agents"""
    
    name = "broadcast_message"
    description = "Send a message to all agents in the current chatroom. Use for sharing information or requesting help from any available agent."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, message: str, **kwargs) -> str:
        """
        Broadcast a message to all agents
        
        Args:
            message: The message to broadcast
            
        Returns:
            Broadcast status
        """
        current_agent_id = kwargs.get('agent_id', 0)
        current_agent_name = kwargs.get('agent_name', 'unknown')
        chatroom_id = kwargs.get('chatroom_id', 0)
        
        if not self.coordinator:
            return "[Broadcast] Error: Collaboration coordinator not available"
        
        from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid
        
        # Create broadcast message
        broadcast = CollaborationMessage(
            id=str(uuid.uuid4()),
            message_type=CollaborationMessageType.BROADCAST,
            from_agent_id=current_agent_id,
            from_agent_name=current_agent_name,
            chatroom_id=chatroom_id,
            content=message
        )
        
        # Route message
        await self.coordinator.route_message(broadcast)
        
        # Count recipients
        agent_ids = self.coordinator.chatroom_agents.get(chatroom_id, set())
        recipient_count = len([aid for aid in agent_ids if aid != current_agent_id])
        
        return f"[Broadcast] Message sent to {recipient_count} other agent(s) in chatroom"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to broadcast to all agents"
                }
            },
            "required": ["message"]
        }


class CheckTaskStatusTool(BaseTool):
    """Tool for checking status of delegated tasks"""
    
    name = "check_task_status"
    description = "Check the status of a delegated task. Use this to see if a task you delegated has been completed."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, task_id: str, **kwargs) -> str:
        """
        Check task status
        
        Args:
            task_id: The ID of the task to check
            
        Returns:
            Task status information
        """
        if not self.coordinator:
            return "[Check Task] Error: Collaboration coordinator not available"
        
        task = self.coordinator.get_task_status(task_id)
        
        if not task:
            return f"[Check Task] Error: Task '{task_id}' not found"
        
        status_emoji = {
            "pending": "⏳",
            "in_progress": "🔄",
            "completed": "✅",
            "failed": "❌",
            "delegated": "📤"
        }.get(task.status, "❓")
        
        result = f"[Check Task] Task: {task.title}\n"
        result += f"Status: {status_emoji} {task.status}\n"
        result += f"Assigned to: Agent #{task.assigned_to_agent_id}\n"
        
        if task.result:
            result += f"Result: {task.result[:500]}...\n" if len(task.result) > 500 else f"Result: {task.result}\n"
        
        if task.completed_at:
            result += f"Completed at: {task.completed_at}\n"
        
        return result
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to check"
                }
            },
            "required": ["task_id"]
        }


class ListCollaboratorsTool(BaseTool):
    """Tool for listing available collaborators"""
    
    name = "list_collaborators"
    description = "List all available agents for collaboration in the current chatroom. Use this to see who you can delegate tasks to."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, **kwargs) -> str:
        """
        List available collaborators

        Returns:
            List of available agents with their capabilities
        """
        chatroom_id = kwargs.get('chatroom_id', 0)

        if not self.coordinator:
            return "[List Collaborators] Error: Collaboration coordinator not available"

        agent_ids = self.coordinator.chatroom_agents.get(chatroom_id, set())

        # 如果聊天室没有注册的协作者，查询当前房间（项目）关联的 Agent
        if not agent_ids:
            try:
                from models.database import get_db, Agent as DBAgent, Chatroom, AgentAssignment
                db = next(get_db())
                try:
                    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
                    if chatroom and chatroom.project_id:
                        assignments = db.query(AgentAssignment).filter(
                            AgentAssignment.project_id == chatroom.project_id
                        ).all()
                        assigned_ids = [a.agent_id for a in assignments]
                        room_agents = db.query(DBAgent).filter(
                            DBAgent.id.in_(assigned_ids), DBAgent.is_active == True
                        ).all() if assigned_ids else []
                    else:
                        room_agents = []

                    if room_agents:
                        result = f"[List Collaborators] {len(room_agents)} agent(s) in this room:\n"
                        for a in room_agents:
                            tools = a.tools if isinstance(a.tools, str) else str(a.tools)
                            result += f"  - **{a.name}** (role: {a.role}, tools: {tools})\n"
                        result += "\nTip: Use @agent_name to directly invoke an agent, or delegate_task to assign work."
                        return result
                finally:
                    db.close()
            except Exception:
                pass
            return "[List Collaborators] No other agents available in this chatroom"

        result = f"[List Collaborators] {len(agent_ids)} agent(s) in chatroom:\n"

        for aid in agent_ids:
            if aid in self.coordinator.collaborators:
                collab = self.coordinator.collaborators[aid]
                status = "🟢 active" if collab.is_active else "🔴 inactive"
                pending = len(collab.assigned_tasks)
                result += f"  - **{collab.agent_name}** (ID: {aid}): {status}, {pending} pending tasks\n"

        return result
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


class SendDirectMessageTool(BaseTool):
    """Tool for sending direct messages to specific agents"""
    
    name = "send_direct_message"
    description = "Send a private message to a specific agent. Use for direct communication without task delegation."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, target_agent_name: str, message: str, **kwargs) -> str:
        """
        Send a direct message to another agent
        
        Args:
            target_agent_name: Name of the target agent
            message: The message to send
            
        Returns:
            Send status
        """
        current_agent_id = kwargs.get('agent_id', 0)
        current_agent_name = kwargs.get('agent_name', 'unknown')
        chatroom_id = kwargs.get('chatroom_id', 0)
        
        if not self.coordinator:
            return "[Direct Message] Error: Collaboration coordinator not available"
        
        # Find target agent
        target_agent_id = None
        for aid, collab in self.coordinator.collaborators.items():
            if collab.agent_name == target_agent_name:
                target_agent_id = aid
                break
        
        if not target_agent_id:
            return f"[Direct Message] Error: Agent '{target_agent_name}' not found"
        
        from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid
        
        # Create direct message
        direct_msg = CollaborationMessage(
            id=str(uuid.uuid4()),
            message_type=CollaborationMessageType.DIRECT,
            from_agent_id=current_agent_id,
            from_agent_name=current_agent_name,
            to_agent_id=target_agent_id,
            to_agent_name=target_agent_name,
            chatroom_id=chatroom_id,
            content=message
        )
        
        # Route message
        await self.coordinator.route_message(direct_msg)
        
        return f"[Direct Message] Sent to {target_agent_name}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "Name of the agent to send message to"
                },
                "message": {
                    "type": "string",
                    "description": "The message content"
                }
            },
            "required": ["target_agent_name", "message"]
        }


class ListDirectoryTool(BaseTool):
    """Tool for listing agents in the system directory that are NOT in the current room"""

    name = "list_directory"
    description = (
        "List agents in the system directory that are NOT in the current room. "
        "Use this to find agents you can invite to join the current project."
    )

    async def execute(self, **kwargs) -> str:
        chatroom_id = kwargs.get('chatroom_id', 0)

        from models.database import get_db, Agent as DBAgent, Chatroom, AgentAssignment
        db = next(get_db())
        try:
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom or not chatroom.project_id:
                return "[Directory] Error: No project associated with this chatroom"

            # 当前房间内的 agent ids
            assignments = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == chatroom.project_id
            ).all()
            room_agent_ids = {a.agent_id for a in assignments}

            # 系统中所有活跃 agent，排除已在房间内的
            all_agents = db.query(DBAgent).filter(DBAgent.is_active == True).all()
            external = [a for a in all_agents if a.id not in room_agent_ids]

            if not external:
                return "[Directory] All system agents are already in this room."

            result = f"[Directory] {len(external)} agent(s) available to invite:\n"
            for a in external:
                result += f"  - **{a.name}** (role: {a.role})\n"
            result += "\nUse invite_agent(agent_name) to add one to this room."
            return result
        finally:
            db.close()

    def _get_parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}


class InviteAgentTool(BaseTool):
    """Tool for inviting an agent to join the current room"""

    name = "invite_agent"
    description = (
        "Invite an agent to join the current room/project. "
        "The agent will be added to the team and available for collaboration."
    )

    async def execute(self, agent_name: str, **kwargs) -> str:
        chatroom_id = kwargs.get('chatroom_id', 0)

        from models.database import get_db, Agent as DBAgent, Chatroom, AgentAssignment
        db = next(get_db())
        try:
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom or not chatroom.project_id:
                return "[Invite] Error: No project associated with this chatroom"

            # 查找目标 agent
            target = db.query(DBAgent).filter(
                DBAgent.name == agent_name, DBAgent.is_active == True
            ).first()
            if not target:
                return f"[Invite] Error: Agent '{agent_name}' not found in the system."

            # 检查是否已在房间内
            existing = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == chatroom.project_id,
                AgentAssignment.agent_id == target.id
            ).first()
            if existing:
                return f"[Invite] Agent '{agent_name}' is already in this room."

            # 创建分配
            assignment = AgentAssignment(project_id=chatroom.project_id, agent_id=target.id)
            db.add(assignment)
            db.commit()

            # 注册到协作协调器
            try:
                from agents.collaboration import collaboration_coordinator, AgentCollaborator
                collaborator = AgentCollaborator(
                    agent_id=target.id,
                    agent_name=target.name,
                    chatroom_id=chatroom_id
                )
                collaboration_coordinator.register_collaborator(collaborator)
            except Exception:
                pass

            return f"[Invite] ✅ Agent '{agent_name}' (role: {target.role}) has joined this room."
        finally:
            db.close()

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to invite (e.g. 'security_auditor')"
                }
            },
            "required": ["agent_name"]
        }
