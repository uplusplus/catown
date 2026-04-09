import logging
logger = logging.getLogger("catown.collaboration")
# -*- coding: utf-8 -*-
"""
Multi-Agent Collaboration Module

This module implements the collaboration mechanism between agents:
- Message routing between agents
- Task delegation
- Collaboration coordination
- Broadcasting to multiple agents
"""
from typing import List, Dict, Any, Optional, Set
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
from sqlalchemy.orm import Session
import asyncio
import json
import uuid
import re


class CollaborationMessageType(str, Enum):
    """Types of collaboration messages"""
    TASK_REQUEST = "task_request"      # Request another agent to perform a task
    TASK_RESPONSE = "task_response"    # Response to a task request
    BROADCAST = "broadcast"            # Broadcast message to all agents
    DIRECT = "direct"                  # Direct message to specific agent
    STATUS_UPDATE = "status_update"    # Agent status update
    COORDINATION = "coordination"      # Coordination message from leader


class TaskStatus(str, Enum):
    """Task status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    DELEGATED = "delegated"


class CollaborationTask(BaseModel):
    """Collaboration task"""
    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    created_by_agent_id: int
    assigned_to_agent_id: Optional[int] = None
    chatroom_id: int
    parent_task_id: Optional[str] = None
    subtasks: List[str] = []
    result: Optional[str] = None
    metadata: Dict[str, Any] = {}
    created_at: datetime = datetime.now()
    completed_at: Optional[datetime] = None


class CollaborationMessage(BaseModel):
    """Collaboration message between agents"""
    id: str
    message_type: CollaborationMessageType
    from_agent_id: int
    from_agent_name: str
    to_agent_id: Optional[int] = None
    to_agent_name: Optional[str] = None
    chatroom_id: int
    content: str
    task_id: Optional[str] = None
    metadata: Dict[str, Any] = {}
    created_at: datetime = datetime.now()


class CollaborationStrategy:
    """Base class for collaboration strategies"""
    
    async def select_agents(self, message: str, agents: List[Any], context: Dict = None) -> List[Any]:
        """Select which agents should respond"""
        raise NotImplementedError


class SingleAgentStrategy(CollaborationStrategy):
    """Default: Single agent responds based on @ mention or default"""
    
    async def select_agents(self, message: str, agents: List[Any], context: Dict = None) -> List[Any]:
        """Select single agent"""
        import re
        
        # Check for @ mentions
        mentions = re.findall(r'@(\w+)', message)
        if mentions:
            for mention in mentions:
                agent = next((a for a in agents if a.name == mention), None)
                if agent:
                    return [agent]
        
        # Default to "assistant" or first agent
        agent = next((a for a in agents if a.name == "assistant"), agents[0] if agents else None)
        return [agent] if agent else []


class MultiAgentStrategy(CollaborationStrategy):
    """Multiple agents collaborate on complex tasks"""
    
    def __init__(self, max_agents: int = 3):
        self.max_agents = max_agents
    
    async def select_agents(self, message: str, agents: List[Any], context: Dict = None) -> List[Any]:
        """Select multiple agents based on task type"""
        import re
        
        # Check for explicit @ mentions
        mentions = re.findall(r'@(\w+)', message)
        mentioned_agents = []
        for mention in mentions:
            agent = next((a for a in agents if a.name == mention), None)
            if agent:
                mentioned_agents.append(agent)
        
        if mentioned_agents:
            return mentioned_agents[:self.max_agents]
        
        # Auto-select based on keywords
        selected = []
        message_lower = message.lower()
        
        # Code-related tasks
        if any(kw in message_lower for kw in ['code', '编程', '程序', 'debug', '代码', 'implement', '实现']):
            dev = next((a for a in agents if a.name == 'developer'), None)
            if dev:
                selected.append(dev)
        
        # Analysis/research tasks
        if any(kw in message_lower for kw in ['research', '研究', 'investigate', '调查', 'analyze', '分析', '需求']):
            analyst = next((a for a in agents if a.name == 'analyst'), None)
            if analyst:
                selected.append(analyst)
        
        # Architecture tasks
        if any(kw in message_lower for kw in ['architecture', '架构', 'design', '设计', 'technical']):
            architect = next((a for a in agents if a.name == 'architect'), None)
            if architect:
                selected.append(architect)
        
        # Testing tasks
        if any(kw in message_lower for kw in ['test', '测试', 'check', '检查', 'review', '审核']):
            tester = next((a for a in agents if a.name == 'tester'), None)
            if tester:
                selected.append(tester)
        
        # If no specific agents selected, use default
        if not selected:
            assistant = next((a for a in agents if a.name == 'assistant'), None)
            if assistant:
                selected.append(assistant)
            elif agents:
                selected.append(agents[0])
        
        return selected[:self.max_agents]


class CollaborationManager:
    """Manager for agent collaboration"""
    
    def __init__(self, strategy: CollaborationStrategy = None):
        self.strategy = strategy or SingleAgentStrategy()
    
    def set_strategy(self, strategy: CollaborationStrategy):
        """Change collaboration strategy"""
        self.strategy = strategy
    
    async def coordinate(self, message: str, agents: List[Any], context: Dict = None) -> List[Dict]:
        """
        Coordinate agent responses
        
        Returns:
            List of agent responses
        """
        selected_agents = await self.strategy.select_agents(message, agents, context)
        return selected_agents


# Global collaboration manager
collaboration_manager = CollaborationManager()


class AgentCollaborator:
    """
    Agent collaboration handler
    
    Each agent has one collaborator instance that handles:
    - Sending messages to other agents
    - Receiving and processing messages from other agents
    - Task delegation and tracking
    - Broadcasting updates
    """
    
    def __init__(self, agent_id: int, agent_name: str, chatroom_id: int):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.chatroom_id = chatroom_id
        
        # Message queues
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.outbox: asyncio.Queue = asyncio.Queue()
        
        # Task tracking
        self.pending_tasks: Dict[str, CollaborationTask] = {}
        self.assigned_tasks: Dict[str, CollaborationTask] = {}
        
        # Message history
        self.message_history: List[CollaborationMessage] = []
        
        # Collaboration state
        self.is_active = True
        self.current_collaborators: Set[int] = set()
    
    async def send_message(
        self,
        to_agent_id: Optional[int],
        to_agent_name: Optional[str],
        content: str,
        message_type: CollaborationMessageType = CollaborationMessageType.DIRECT,
        task_id: Optional[str] = None,
        metadata: Dict = None
    ) -> CollaborationMessage:
        """Send a collaboration message"""
        message = CollaborationMessage(
            id=str(uuid.uuid4()),
            message_type=message_type,
            from_agent_id=self.agent_id,
            from_agent_name=self.agent_name,
            to_agent_id=to_agent_id,
            to_agent_name=to_agent_name,
            chatroom_id=self.chatroom_id,
            content=content,
            task_id=task_id,
            metadata=metadata or {}
        )
        
        await self.outbox.put(message)
        self.message_history.append(message)
        
        return message
    
    async def broadcast(self, content: str, task_id: Optional[str] = None, metadata: Dict = None) -> CollaborationMessage:
        """Broadcast a message to all agents in the chatroom"""
        return await self.send_message(
            to_agent_id=None,
            to_agent_name=None,
            content=content,
            message_type=CollaborationMessageType.BROADCAST,
            task_id=task_id,
            metadata=metadata
        )
    
    async def delegate_task(
        self,
        to_agent_id: int,
        to_agent_name: str,
        title: str,
        description: str,
        metadata: Dict = None
    ) -> CollaborationTask:
        """Delegate a task to another agent"""
        task = CollaborationTask(
            id=str(uuid.uuid4()),
            title=title,
            description=description,
            created_by_agent_id=self.agent_id,
            assigned_to_agent_id=to_agent_id,
            chatroom_id=self.chatroom_id,
            metadata=metadata or {}
        )
        
        self.pending_tasks[task.id] = task
        
        # Send task request message
        await self.send_message(
            to_agent_id=to_agent_id,
            to_agent_name=to_agent_name,
            content=f"Task: {title}\n\n{description}",
            message_type=CollaborationMessageType.TASK_REQUEST,
            task_id=task.id,
            metadata={"task": task.dict()}
        )
        
        return task
    
    async def receive_message(self, message: CollaborationMessage):
        """Receive a collaboration message"""
        await self.inbox.put(message)
        self.message_history.append(message)
        
        # Handle task-related messages
        if message.task_id:
            await self._handle_task_message(message)
    
    async def _handle_task_message(self, message: CollaborationMessage):
        """Handle task-related messages"""
        if message.message_type == CollaborationMessageType.TASK_REQUEST:
            # Received a task request - track as assigned
            task_data = message.metadata.get("task", {})
            task = CollaborationTask(**task_data)
            self.assigned_tasks[task.id] = task
            
        elif message.message_type == CollaborationMessageType.TASK_RESPONSE:
            # Received a task response - update pending task
            task_id = message.task_id
            if task_id in self.pending_tasks:
                task = self.pending_tasks[task_id]
                task.status = TaskStatus.COMPLETED
                task.result = message.content
                task.completed_at = datetime.now()
    
    async def complete_task(self, task_id: str, result: str):
        """Mark a task as completed and notify the requester"""
        if task_id in self.assigned_tasks:
            task = self.assigned_tasks[task_id]
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.now()
            
            # Send response to the task creator
            await self.send_message(
                to_agent_id=task.created_by_agent_id,
                to_agent_name=None,
                content=result,
                message_type=CollaborationMessageType.TASK_RESPONSE,
                task_id=task_id,
                metadata={"task": task.dict()}
            )
    
    def get_status(self) -> Dict[str, Any]:
        """Get collaboration status"""
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "chatroom_id": self.chatroom_id,
            "is_active": self.is_active,
            "pending_tasks": len(self.pending_tasks),
            "assigned_tasks": len(self.assigned_tasks),
            "message_count": len(self.message_history),
            "current_collaborators": list(self.current_collaborators)
        }


class CollaborationCoordinator:
    """
    Central coordinator for agent collaboration
    
    Responsibilities:
    - Route messages between agents
    - Track active collaborators
    - Coordinate multi-agent tasks
    - Broadcast system messages
    """
    
    def __init__(self):
        self.collaborators: Dict[int, AgentCollaborator] = {}
        self.chatroom_agents: Dict[int, Set[int]] = {}
        self.task_registry: Dict[str, CollaborationTask] = {}
        self._message_handlers: List[callable] = []
    
    def register_collaborator(self, collaborator: AgentCollaborator):
        """Register an agent collaborator"""
        self.collaborators[collaborator.agent_id] = collaborator
        
        chatroom_id = collaborator.chatroom_id
        if chatroom_id not in self.chatroom_agents:
            self.chatroom_agents[chatroom_id] = set()
        self.chatroom_agents[chatroom_id].add(collaborator.agent_id)
        
        logger.info(f"Registered collaborator: {collaborator.agent_name} (ID: {collaborator.agent_id})")
    
    def unregister_collaborator(self, agent_id: int):
        """Unregister an agent collaborator"""
        if agent_id in self.collaborators:
            collaborator = self.collaborators[agent_id]
            
            chatroom_id = collaborator.chatroom_id
            if chatroom_id in self.chatroom_agents:
                self.chatroom_agents[chatroom_id].discard(agent_id)
            
            del self.collaborators[agent_id]
            logger.info(f"Unregistered collaborator: {collaborator.agent_name}")
    
    def add_message_handler(self, handler: callable):
        """Add a message handler (e.g., for WebSocket broadcast)"""
        self._message_handlers.append(handler)
    
    async def route_message(self, message: CollaborationMessage):
        """Route a message to the appropriate recipient(s)"""
        # Store task if present
        if message.task_id and "task" in message.metadata:
            self.task_registry[message.task_id] = CollaborationTask(**message.metadata["task"])
        
        if message.message_type == CollaborationMessageType.BROADCAST:
            await self._broadcast_to_chatroom(message)
        elif message.to_agent_id:
            await self._deliver_to_agent(message.to_agent_id, message)
        
        # Notify message handlers
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                logger.error(f"Error in message handler: {e}")
    
    async def _broadcast_to_chatroom(self, message: CollaborationMessage):
        """Broadcast message to all agents in chatroom"""
        chatroom_id = message.chatroom_id
        if chatroom_id not in self.chatroom_agents:
            return
        
        for agent_id in self.chatroom_agents[chatroom_id]:
            if agent_id != message.from_agent_id:
                await self._deliver_to_agent(agent_id, message)
    
    async def _deliver_to_agent(self, agent_id: int, message: CollaborationMessage):
        """Deliver message to a specific agent"""
        if agent_id in self.collaborators:
            collaborator = self.collaborators[agent_id]
            await collaborator.receive_message(message)
    
    async def process_all_outboxes(self):
        """Process all collaborator outboxes"""
        for agent_id, collaborator in self.collaborators.items():
            while not collaborator.outbox.empty():
                message = await collaborator.outbox.get()
                await self.route_message(message)
    
    def get_chatroom_status(self, chatroom_id: int) -> Dict[str, Any]:
        """Get collaboration status for a chatroom"""
        agent_ids = self.chatroom_agents.get(chatroom_id, set())
        
        return {
            "chatroom_id": chatroom_id,
            "agent_count": len(agent_ids),
            "agents": [
                self.collaborators[aid].get_status()
                for aid in agent_ids
                if aid in self.collaborators
            ],
            "active_tasks": len([
                t for t in self.task_registry.values()
                if t.chatroom_id == chatroom_id and t.status == TaskStatus.IN_PROGRESS
            ])
        }
    
    def get_task_status(self, task_id: str) -> Optional[CollaborationTask]:
        """Get task status"""
        return self.task_registry.get(task_id)


# Global collaboration coordinator
collaboration_coordinator = CollaborationCoordinator()


# Background task to process message routing
async def collaboration_loop():
    """Background task to process collaboration messages"""
    while True:
        try:
            await collaboration_coordinator.process_all_outboxes()
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in collaboration loop: {e}")
            await asyncio.sleep(1)
