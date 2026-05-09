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
import os
import uuid
import re

from agents.identity import DEFAULT_AGENT_TYPE, find_agent_by_type

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
    STALLED = "stalled"
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


def delegated_task_client_turn_id(task_id: str) -> str:
    return f"delegate-{task_id}"


def normalize_delegated_task_id(task_id: str | None) -> str:
    value = str(task_id or "").strip()
    if value.startswith("delegate-"):
        value = value[len("delegate-"):].strip()
    return value


def delegated_task_id_from_client_turn_id(client_turn_id: str | None) -> str | None:
    value = str(client_turn_id or "").strip()
    if not value.startswith("delegate-"):
        return None
    task_id = value[len("delegate-"):].strip()
    return task_id or None


_DELEGATED_TASK_PLACEHOLDER_RESULT = "Completed in chat window. See delegated turn output."
_WAITING_FOR_APPROVAL_RESULT = "Waiting for approval."
_RUNNING_FALLBACK_RESULT = "Task is running."
_STALLED_TASK_IDLE_SECONDS = 120


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
                agent = find_agent_by_type(agents, mention)
                if agent:
                    return [agent]
        
        # Default to valet or first agent
        agent = find_agent_by_type(agents, DEFAULT_AGENT_TYPE) or (agents[0] if agents else None)
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
            agent = find_agent_by_type(agents, mention)
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
            valet = find_agent_by_type(agents, DEFAULT_AGENT_TYPE)
            if valet:
                selected.append(valet)
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

    def pending_task_count(self, chatroom_id: int | None = None) -> int:
        """Count tasks that still need attention."""
        active_statuses = {
            TaskStatus.PENDING,
            TaskStatus.DELEGATED,
            TaskStatus.IN_PROGRESS,
        }
        return sum(
            1
            for task in self.task_registry.values()
            if task.status in active_statuses
            and (chatroom_id is None or task.chatroom_id == chatroom_id)
        )
    
    def get_task_status(self, task_id: str) -> Optional[CollaborationTask]:
        """Get task status"""
        return self.task_registry.get(task_id)


def refresh_collaboration_task_from_runtime(task: CollaborationTask | None) -> CollaborationTask | None:
    """Best-effort sync from persisted chat/task-run state back into a collaboration task."""
    if task is None:
        return None

    try:
        from models.database import SessionLocal, Message, TaskRun, ApprovalQueueItem
    except Exception:
        return task

    placeholder_result = _DELEGATED_TASK_PLACEHOLDER_RESULT
    client_turn_id = delegated_task_client_turn_id(task.id)

    db = SessionLocal()
    try:
        final_message = (
            db.query(Message)
            .filter(Message.chatroom_id == task.chatroom_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .all()
        )
        for message in final_message:
            try:
                metadata = json.loads(getattr(message, "metadata_json", "") or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if (metadata.get("client_turn_id") or "") != client_turn_id:
                continue
            if getattr(message, "message_type", "") != "text":
                continue
            if getattr(message, "agent_id", None) != task.assigned_to_agent_id:
                continue
            content = (getattr(message, "content", "") or "").strip()
            if not content:
                continue
            task.result = content
            task.status = TaskStatus.COMPLETED
            task.completed_at = task.completed_at or getattr(message, "created_at", None) or datetime.now()
            return task

        task_run = (
            db.query(TaskRun)
            .filter(
                TaskRun.chatroom_id == task.chatroom_id,
                TaskRun.client_turn_id == client_turn_id,
            )
            .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            .first()
        )
        if task_run is None:
            return task

        pending_approval_count = (
            db.query(ApprovalQueueItem)
            .filter(
                ApprovalQueueItem.task_run_id == task_run.id,
                ApprovalQueueItem.status == "pending",
            )
            .count()
        )

        normalized_run_status = (task_run.status or "").strip().lower()
        current_result = (task.result or "").strip()
        use_placeholder = not current_result or current_result == placeholder_result

        if normalized_run_status in {"failed", "cancelled"}:
            task.status = TaskStatus.FAILED
            if use_placeholder and getattr(task_run, "summary", None):
                task.result = task_run.summary
            task.completed_at = task.completed_at or getattr(task_run, "completed_at", None) or datetime.now()
            return task

        if pending_approval_count > 0:
            task.status = TaskStatus.IN_PROGRESS
            if use_placeholder or current_result.startswith(_WAITING_FOR_APPROVAL_RESULT):
                pending_item = (
                    db.query(ApprovalQueueItem)
                    .filter(
                        ApprovalQueueItem.task_run_id == task_run.id,
                        ApprovalQueueItem.status == "pending",
                    )
                    .order_by(ApprovalQueueItem.created_at.desc(), ApprovalQueueItem.id.desc())
                    .first()
                )
                target_name = getattr(pending_item, "target_name", None) if pending_item is not None else None
                task.result = (
                    f"Waiting for approval: {target_name}"
                    if target_name
                    else "Waiting for approval."
                )
            return task

        if normalized_run_status == "completed":
            task.status = TaskStatus.COMPLETED
            if use_placeholder and getattr(task_run, "summary", None):
                task.result = task_run.summary
            task.completed_at = task.completed_at or getattr(task_run, "completed_at", None) or datetime.now()
            return task

        if normalized_run_status == "running":
            stalled_snapshot = _delegated_task_stalled_snapshot(db, task_run)
            task.status = TaskStatus.STALLED if stalled_snapshot is not None else TaskStatus.IN_PROGRESS
            runtime_activity = _summarize_running_task_activity(db, task_run)
            if runtime_activity:
                task.result = (
                    f"Stalled: {runtime_activity[len('Running: '):]}"
                    if stalled_snapshot is not None and runtime_activity.startswith("Running: ")
                    else runtime_activity
                )
            elif use_placeholder and getattr(task_run, "summary", None):
                task.result = task_run.summary
            elif not current_result:
                task.result = "Task appears stalled." if stalled_snapshot is not None else _RUNNING_FALLBACK_RESULT
            if stalled_snapshot is not None:
                if not str(task.result or "").strip().lower().startswith("stalled:"):
                    card = _latest_task_run_runtime_card(db, task_run)
                    if isinstance(card, dict):
                        arguments = _json_dict(card.get("arguments"))
                        detail = _describe_tool_activity(str(card.get("tool") or "").strip(), arguments)
                        if detail:
                            task.result = f"Stalled: {detail}"
                    if not str(task.result or "").strip().lower().startswith("stalled:"):
                        task.result = "Task appears stalled."
                task.metadata = {
                    **(task.metadata or {}),
                    "stalled_snapshot": stalled_snapshot,
                }
            return task

        return task
    except Exception:
        return task
    finally:
        db.close()


def collaboration_task_result_details(task: CollaborationTask | None) -> dict[str, Any]:
    """Derive a structured status/result summary for delegated tasks."""
    if task is None:
        return {}

    result_text = str(task.result or "").strip()
    lowered = result_text.lower()
    details: dict[str, Any] = {
        "ran_tests": None,
        "command": None,
        "blocked": None,
        "blocked_reason": None,
        "passed_count": None,
        "failed_count": None,
        "error_summary": None,
        "next_step": None,
        "phase": None,
        "tail_output": None,
        "duration_ms": None,
    }

    if "waiting for approval" in lowered:
        details["blocked"] = True
        details["blocked_reason"] = result_text
        details["phase"] = "blocked"
    elif "approved " in lowered and "run_shell" in lowered:
        details["blocked"] = False
        details["ran_tests"] = True if "pytest" in lowered else None
    elif "delegated execution failed:" in lowered:
        details["blocked"] = False
        details["error_summary"] = result_text
        details["phase"] = "failed"
    elif result_text and result_text != _DELEGATED_TASK_PLACEHOLDER_RESULT:
        details["blocked"] = False
        if "pytest" in lowered or "test" in lowered:
            details["ran_tests"] = True
        if lowered.startswith("running:"):
            details["phase"] = "running"

    command_match = re.search(r"(python\s+-m\s+pytest[^\n\r]*)", result_text, re.IGNORECASE)
    if command_match:
        details["command"] = command_match.group(1).strip()
        details["ran_tests"] = True

    pass_fail_match = re.search(r"(\d+)\s+passed,\s+(\d+)\s+failed", lowered)
    if pass_fail_match:
        details["passed_count"] = int(pass_fail_match.group(1))
        details["failed_count"] = int(pass_fail_match.group(2))
        details["ran_tests"] = True
    else:
        passed_match = re.search(r"(\d+)\s+passed", lowered)
        failed_match = re.search(r"(\d+)\s+failed", lowered)
        if passed_match:
            details["passed_count"] = int(passed_match.group(1))
            details["ran_tests"] = True
        if failed_match:
            details["failed_count"] = int(failed_match.group(1))
            details["ran_tests"] = True

    if task.status == TaskStatus.FAILED and not details["error_summary"]:
        details["error_summary"] = result_text or "Task failed."
        details["phase"] = "failed"

    stalled_snapshot = task.metadata.get("stalled_snapshot") if isinstance(task.metadata, dict) else None
    if isinstance(stalled_snapshot, dict):
        details["phase"] = "stalled"
        details["blocked"] = False if details["blocked"] is None else details["blocked"]
        details["stalled"] = True
        details["idle_seconds"] = stalled_snapshot.get("idle_seconds")
        details["last_activity_at"] = stalled_snapshot.get("last_activity_at")
        if not details.get("error_summary"):
            details["error_summary"] = "Task appears stalled: no recent runtime activity and no live process detected."

    if task.status == TaskStatus.COMPLETED:
        details["phase"] = details["phase"] or "completed"
        if details["failed_count"]:
            details["next_step"] = "Inspect failed tests and logs."
        elif details["ran_tests"]:
            details["next_step"] = "Review the delegated test output."
    elif task.status == TaskStatus.STALLED:
        details["phase"] = "stalled"
        details["next_step"] = "Inspect the delegated task; it may need retry or manual recovery."
    elif task.status in {TaskStatus.IN_PROGRESS, TaskStatus.DELEGATED}:
        details["phase"] = details["phase"] or "running"
        if details["blocked"]:
            details["next_step"] = "Resolve the approval request so execution can continue."
        else:
            details["next_step"] = "Wait for the delegated agent to finish and report back."

    return details


def collaboration_task_response_payload(task: CollaborationTask | None) -> dict[str, Any]:
    if task is None:
        return {}
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assigned_to": task.assigned_to_agent_id,
        "result": task.result,
        "result_details": collaboration_task_result_details(task),
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _summarize_running_task_activity(db: Session, task_run: Any) -> str | None:
    try:
        from models.database import ApprovalQueueItem, TaskRunEvent
    except Exception:
        return None

    latest_event = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.task_run_id == task_run.id)
        .order_by(TaskRunEvent.event_index.desc(), TaskRunEvent.id.desc())
        .first()
    )
    latest_queue_item = (
        db.query(ApprovalQueueItem)
        .filter(ApprovalQueueItem.task_run_id == task_run.id)
        .order_by(ApprovalQueueItem.created_at.desc(), ApprovalQueueItem.id.desc())
        .first()
    )

    if latest_event is not None:
        payload = _json_dict(getattr(latest_event, "payload_json", None))
        if latest_event.event_type == "approval_queue_item_resolved":
            target_name = str(payload.get("target_name") or getattr(latest_queue_item, "target_name", None) or "").strip()
            action_taken = str(payload.get("action_taken") or "").strip().lower()
            if action_taken == "queue_resolved_only":
                detail = _describe_queue_item_request(latest_queue_item)
                if target_name and detail:
                    return f"Approved {target_name}; now executing {detail}"
                if detail:
                    return f"Approved request; now executing {detail}"
                if target_name:
                    return f"Approved {target_name}; follow-up is running."
            resolved_status = str(payload.get("status") or "").strip().lower()
            if resolved_status == "approved" and target_name:
                return f"Approved {target_name}; follow-up is running."

        if latest_event.event_type == "approval_queue_item_followup_triggered":
            tool_name = str(payload.get("tool_name") or getattr(latest_queue_item, "target_name", None) or "").strip()
            detail = _describe_queue_item_request(latest_queue_item)
            if tool_name and detail:
                return f"Continuing after approval: {detail}"
            if tool_name:
                return f"Continuing after approval of {tool_name}."
            return "Continuing approved follow-up."

        if latest_event.event_type == "tool_round_recorded":
            detail = _describe_latest_tool_round(payload)
            if detail:
                return detail

        if latest_event.event_type == "tool_call_started":
            tool_name = str(payload.get("tool_name") or "").strip()
            arguments = _json_dict(payload.get("arguments"))
            detail = _describe_tool_activity(tool_name, arguments)
            if detail:
                return f"Running: {detail}"
            if tool_name:
                return f"Running: {tool_name}"

        if latest_event.event_type == "agent_turn_started":
            agent_name = str(getattr(latest_event, "agent_name", "") or "").strip() or str(getattr(task_run, "target_agent_name", "") or "Agent")
            if latest_queue_item is not None and str(getattr(latest_queue_item, "status", "") or "").strip().lower() == "approved":
                detail = _describe_queue_item_request(latest_queue_item)
                if detail:
                    return f"{agent_name} is running {detail}."
            return f"{agent_name} is working on the task."

    if latest_queue_item is not None and str(getattr(latest_queue_item, "status", "") or "").strip().lower() == "approved":
        detail = _describe_queue_item_request(latest_queue_item)
        if detail:
            return f"Approved request; now executing {detail}"

    return None


def _latest_task_run_runtime_card(db: Session, task_run: Any) -> dict[str, Any] | None:
    try:
        from models.database import Message
    except Exception:
        return None

    rows = (
        db.query(Message)
        .filter(Message.chatroom_id == task_run.chatroom_id, Message.message_type == "runtime_card")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(40)
        .all()
    )
    task_turn_id = str(getattr(task_run, "client_turn_id", "") or "").strip()
    for row in rows:
        metadata = _json_dict(getattr(row, "metadata_json", None))
        card = metadata.get("card") if isinstance(metadata.get("card"), dict) else {}
        card_turn_id = str(card.get("client_turn_id") or metadata.get("client_turn_id") or "").strip()
        card_run_id = card.get("run_id") or metadata.get("task_run_id")
        if card_run_id == getattr(task_run, "id", None):
            return card
        if task_turn_id and card_turn_id == task_turn_id:
            return card
    return None


def enrich_collaboration_task_result_details(task: CollaborationTask | None) -> dict[str, Any]:
    details = collaboration_task_result_details(task)
    if task is None:
        return details
    task_run_id = task.metadata.get("task_run_id") if isinstance(task.metadata, dict) else None
    if not task_run_id:
        return details
    try:
        from models.database import SessionLocal, TaskRun
    except Exception:
        return details
    db = SessionLocal()
    try:
        task_run = db.query(TaskRun).filter(TaskRun.id == int(task_run_id)).first()
        if task_run is None:
            return details
        card = _latest_task_run_runtime_card(db, task_run)
        if not card:
            return details
        tool_name = str(card.get("tool") or "").strip()
        command = None
        arguments = _json_dict(card.get("arguments"))
        if tool_name == "run_shell":
            command = str(arguments.get("command") or "").strip() or None
        if command:
            details["command"] = command
            if "pytest" in command.lower():
                details["ran_tests"] = True
        result_text = str(card.get("result") or "").strip()
        if result_text:
            details["tail_output"] = result_text
        duration_ms = card.get("duration_ms")
        if isinstance(duration_ms, int):
            details["duration_ms"] = duration_ms
        if details.get("stalled") is True:
            details["phase"] = "stalled"
            return details
        status = str(card.get("status") or "").strip().lower()
        blocked = bool(card.get("blocked"))
        if blocked:
            details["phase"] = "blocked"
        elif status == "running":
            details["phase"] = "running"
        elif status:
            details["phase"] = status
        return details
    except Exception:
        return details
    finally:
        db.close()


def _process_is_alive(pid: Any) -> bool:
    try:
        normalized = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized <= 0:
        return False
    try:
        os.kill(normalized, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _delegated_task_stalled_snapshot(db: Session, task_run: Any) -> dict[str, Any] | None:
    if task_run is None:
        return None
    try:
        from models.database import ApprovalQueueItem, Message, TaskRunEvent
    except Exception:
        return None

    pending_approval_count = (
        db.query(ApprovalQueueItem)
        .filter(
            ApprovalQueueItem.task_run_id == task_run.id,
            ApprovalQueueItem.status == "pending",
        )
        .count()
    )
    if pending_approval_count > 0:
        return None

    latest_event = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.task_run_id == task_run.id)
        .order_by(TaskRunEvent.created_at.desc(), TaskRunEvent.id.desc())
        .first()
    )
    latest_runtime_message = (
        db.query(Message)
        .filter(Message.chatroom_id == task_run.chatroom_id, Message.message_type == "runtime_card")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(40)
        .all()
    )

    latest_runtime_card = None
    task_turn_id = str(getattr(task_run, "client_turn_id", "") or "").strip()
    for row in latest_runtime_message:
        metadata = _json_dict(getattr(row, "metadata_json", None))
        card = metadata.get("card") if isinstance(metadata.get("card"), dict) else {}
        card_turn_id = str(card.get("client_turn_id") or metadata.get("client_turn_id") or "").strip()
        card_run_id = card.get("run_id") or metadata.get("task_run_id")
        if card_run_id == getattr(task_run, "id", None) or (task_turn_id and card_turn_id == task_turn_id):
            latest_runtime_card = {
                "message": row,
                "card": card,
            }
            break

    pid = None
    if latest_runtime_card is not None:
        pid = latest_runtime_card["card"].get("pid")
        if _process_is_alive(pid):
            return None

    last_activity_at = None
    if latest_runtime_card is not None:
        last_activity_at = getattr(latest_runtime_card["message"], "created_at", None)
    if last_activity_at is None and latest_event is not None:
        last_activity_at = getattr(latest_event, "created_at", None)
    if last_activity_at is None:
        last_activity_at = getattr(task_run, "updated_at", None) or getattr(task_run, "created_at", None)
    if last_activity_at is None:
        return None

    idle_seconds = max(0, int((datetime.now(last_activity_at.tzinfo) - last_activity_at).total_seconds()))
    if idle_seconds < _STALLED_TASK_IDLE_SECONDS:
        return None

    return {
        "idle_seconds": idle_seconds,
        "last_activity_at": last_activity_at.isoformat() if hasattr(last_activity_at, "isoformat") else str(last_activity_at),
        "pid": pid,
    }


def _describe_latest_tool_round(payload: dict[str, Any]) -> str | None:
    turn_state = payload.get("turn_local_state") if isinstance(payload.get("turn_local_state"), dict) else {}
    tool_results = turn_state.get("tool_results") if isinstance(turn_state.get("tool_results"), list) else []
    if tool_results:
        latest = tool_results[-1] if isinstance(tool_results[-1], dict) else {}
        tool_name = str(latest.get("tool_name") or "").strip()
        arguments = _json_dict(latest.get("arguments"))
        detail = _describe_tool_activity(tool_name, arguments)
        if detail:
            return f"Latest step: {detail}"
        if tool_name:
            return f"Latest step: ran {tool_name}."

    blocked_tools = payload.get("blocked_tools") if isinstance(payload.get("blocked_tools"), list) else []
    if blocked_tools:
        latest_blocked = blocked_tools[-1] if isinstance(blocked_tools[-1], dict) else {}
        tool_name = str(latest_blocked.get("tool_name") or "").strip()
        arguments = _json_dict(latest_blocked.get("arguments"))
        detail = _describe_tool_activity(tool_name, arguments)
        if detail:
            return f"Blocked while trying to run {detail}"
        if tool_name:
            return f"Blocked while trying to run {tool_name}."
    return None


def _describe_queue_item_request(item: Any) -> str | None:
    if item is None:
        return None
    request_payload = _json_dict(getattr(item, "request_payload_json", None))
    tool_name = str(request_payload.get("tool_name") or getattr(item, "target_name", None) or "").strip()
    arguments = _json_dict(request_payload.get("arguments"))
    detail = _describe_tool_activity(tool_name, arguments)
    if detail:
        return detail
    if tool_name:
        return f"{tool_name}"
    return None


def _describe_tool_activity(tool_name: str, arguments: dict[str, Any]) -> str | None:
    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name:
        return None
    if normalized_tool_name == "run_shell":
        command = str(arguments.get("command") or "").strip()
        cwd = str(arguments.get("cwd") or "").strip()
        if command and cwd:
            return f"run_shell `{command}` in `{cwd}`"
        if command:
            return f"run_shell `{command}`"
    if normalized_tool_name == "execute_code":
        language = str(arguments.get("language") or "").strip()
        if language:
            return f"execute_code ({language})"
    if arguments:
        preview = json.dumps(arguments, ensure_ascii=False)
        if len(preview) > 160:
            preview = preview[:157].rstrip() + "..."
        return f"{normalized_tool_name} {preview}"
    return normalized_tool_name


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def rebuild_collaboration_task_from_runtime(task_id: str) -> CollaborationTask | None:
    """Reconstruct a delegated collaboration task from persisted chat/task-run state."""
    normalized_task_id = normalize_delegated_task_id(task_id)
    if not normalized_task_id:
        return None

    client_turn_id = delegated_task_client_turn_id(normalized_task_id)
    try:
        from models.database import SessionLocal, Message, TaskRun
    except Exception:
        return None

    db = SessionLocal()
    try:
        task_run = (
            db.query(TaskRun)
            .filter(TaskRun.client_turn_id == client_turn_id)
            .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            .first()
        )
        if task_run is None:
            return None

        delegated_message = (
            db.query(Message)
            .filter(Message.chatroom_id == task_run.chatroom_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )

        delegated_task_metadata: dict[str, Any] = {}
        for message in delegated_message:
            try:
                metadata = json.loads(getattr(message, "metadata_json", "") or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if (metadata.get("client_turn_id") or "") != client_turn_id:
                continue
            delegated_task_metadata = metadata.get("delegated_task") if isinstance(metadata.get("delegated_task"), dict) else {}
            if delegated_task_metadata:
                break

        task = CollaborationTask(
            id=normalized_task_id,
            title=str(delegated_task_metadata.get("task_title") or task_run.title or "Delegated task"),
            description=str(
                delegated_task_metadata.get("task_description")
                or task_run.user_request
                or task_run.title
                or "Delegated task"
            ),
            status=TaskStatus.DELEGATED,
            created_by_agent_id=0,
            assigned_to_agent_id=None,
            chatroom_id=task_run.chatroom_id,
            result="Completed in chat window. See delegated turn output.",
            metadata={
                "context": delegated_task_metadata.get("context") or "",
                "delegator": delegated_task_metadata.get("delegator") or "",
                "reconstructed": True,
                "task_run_id": task_run.id,
            },
            created_at=getattr(task_run, "created_at", None) or datetime.now(),
            completed_at=getattr(task_run, "completed_at", None),
        )
        target_name = str(delegated_task_metadata.get("target_agent_name") or task_run.target_agent_name or "").strip()
        if target_name:
            assignee = (
                db.query(Message)
                .filter(Message.chatroom_id == task_run.chatroom_id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .first()
            )
            if assignee is not None and getattr(assignee, "agent_id", None):
                task.created_by_agent_id = int(getattr(assignee, "agent_id", 0) or 0)

        return refresh_collaboration_task_from_runtime(task)
    except Exception:
        return None
    finally:
        db.close()


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
