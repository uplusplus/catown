"""
协作工具测试

覆盖 delegate_task / broadcast_message / check_task_status / list_collaborators / send_direct_message
"""
import pytest
import sys
import os
import json
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def coordinator():
    """创建带注册协作者的 CollaborationCoordinator"""
    from agents.collaboration import CollaborationCoordinator, AgentCollaborator
    coord = CollaborationCoordinator()
    coord.register_collaborator(AgentCollaborator(agent_id=1, agent_name="assistant", chatroom_id=100))
    coord.register_collaborator(AgentCollaborator(agent_id=2, agent_name="coder", chatroom_id=100))
    coord.register_collaborator(AgentCollaborator(agent_id=3, agent_name="reviewer", chatroom_id=200))
    return coord


class TestDelegateTask:
    """DelegateTaskTool 测试"""

    @pytest.mark.asyncio
    async def test_delegate_success(self, coordinator):
        from tools.collaboration_tools import DelegateTaskTool
        tool = DelegateTaskTool(collaboration_coordinator=coordinator)

        result = await tool.execute(
            target_agent_name="coder",
            task_title="Write a function",
            task_description="Implement fibonacci",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert "delegated" in result.lower()
        assert "coder" in result.lower()

    @pytest.mark.asyncio
    async def test_delegate_to_nonexistent_agent(self, coordinator):
        from tools.collaboration_tools import DelegateTaskTool
        tool = DelegateTaskTool(collaboration_coordinator=coordinator)

        result = await tool.execute(
            target_agent_name="nonexistent",
            task_title="T",
            task_description="D",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_delegate_without_coordinator(self):
        from tools.collaboration_tools import DelegateTaskTool
        tool = DelegateTaskTool(collaboration_coordinator=None)

        result = await tool.execute(
            target_agent_name="coder",
            task_title="T", task_description="D",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_delegate_registers_task_in_coordinator(self, coordinator):
        from tools.collaboration_tools import DelegateTaskTool
        tool = DelegateTaskTool(collaboration_coordinator=coordinator)

        await tool.execute(
            target_agent_name="coder",
            task_title="Build feature",
            task_description="Do it",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert len(coordinator.task_registry) == 1
        task = list(coordinator.task_registry.values())[0]
        assert task.title == "Build feature"

    @pytest.mark.asyncio
    async def test_delegate_triggers_chat_visible_execution(self, coordinator, monkeypatch):
        from tools.collaboration_tools import DelegateTaskTool
        from agents.collaboration import TaskStatus

        tool = DelegateTaskTool(collaboration_coordinator=coordinator)

        published_cards = []
        saved_messages = []
        published_messages = []
        triggered_runs = []
        spawned_coroutines = []

        async def fake_store_runtime_card(chatroom_id, payload):
            published_cards.append((chatroom_id, payload))
            return None

        async def fake_send_message(chatroom_id, agent_id, content, message_type="text", metadata=None, agent_name=None):
            saved_messages.append(
                {
                    "chatroom_id": chatroom_id,
                    "agent_id": agent_id,
                    "content": content,
                    "message_type": message_type,
                    "metadata": metadata,
                    "agent_name": agent_name,
                }
            )
            return SimpleNamespace(
                id=99,
                content=content,
                message_type=message_type,
                agent_name=agent_name,
                created_at=datetime.now(),
            )

        async def fake_publish_saved_chat_message(db, chatroom_id, **kwargs):
            published_messages.append((chatroom_id, kwargs))

        async def fake_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, extra_context="", **kwargs):
            triggered_runs.append(
                {
                    "chatroom_id": chatroom_id,
                    "user_message": user_message,
                    "client_turn_id": client_turn_id,
                    "extra_context": extra_context,
                }
            )

        monkeypatch.setattr("tools.collaboration_tools.chatroom_manager.send_message", fake_send_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.publish_saved_chat_message", fake_publish_saved_chat_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.store_runtime_card", fake_store_runtime_card, raising=False)
        monkeypatch.setattr("routes.api.trigger_agent_response", fake_trigger_agent_response, raising=False)
        monkeypatch.setattr("asyncio.create_task", lambda coro: spawned_coroutines.append(coro) or coro)

        result = await tool.execute(
            target_agent_name="coder",
            task_title="Write a function",
            task_description="Implement fibonacci",
            context="Need chat-visible execution.",
            agent_id=1,
            agent_name="assistant",
            chatroom_id=100,
        )
        for coroutine in spawned_coroutines:
            await coroutine

        assert "delegated" in result.lower()
        task = list(coordinator.task_registry.values())[0]
        assert task.status == TaskStatus.COMPLETED
        assert published_cards[0][1]["type"] == "agent_message"
        assert published_cards[0][1]["from_agent"] == "assistant"
        assert published_cards[0][1]["to_agent"] == "coder"
        assert len(saved_messages) == 1
        assert "@coder" in saved_messages[0]["content"]
        assert saved_messages[0]["metadata"]["delegated_task"]["task_id"] == task.id
        assert len(published_messages) == 1
        assert len(triggered_runs) == 1
        assert triggered_runs[0]["chatroom_id"] == 100
        assert triggered_runs[0]["client_turn_id"] == f"delegate-{task.id}"
        assert "@coder" in triggered_runs[0]["user_message"]
        assert task.result == "Completed in chat window. See delegated turn output."

    @pytest.mark.asyncio
    async def test_delegate_backfills_real_result_when_chat_response_exists(self, coordinator, monkeypatch, fresh_db):
        from tools.collaboration_tools import DelegateTaskTool
        from agents.collaboration import TaskStatus

        tool = DelegateTaskTool(collaboration_coordinator=coordinator)
        spawned_coroutines = []

        async def fake_send_message(chatroom_id, agent_id, content, message_type="text", metadata=None, agent_name=None):
            return SimpleNamespace(
                id=100,
                content=content,
                message_type=message_type,
                agent_name=agent_name,
                created_at=datetime.now(),
            )

        async def fake_publish_saved_chat_message(db, chatroom_id, **kwargs):
            return None

        async def fake_store_runtime_card(chatroom_id, payload):
            return None

        async def fake_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, extra_context="", **kwargs):
            db = fresh_db.SessionLocal()
            try:
                db.add(fresh_db.Message(
                    chatroom_id=chatroom_id,
                    agent_id=2,
                    content="Real delegated result from coder.",
                    message_type="text",
                    metadata_json=json.dumps({"client_turn_id": client_turn_id}),
                ))
                db.commit()
            finally:
                db.close()
            return None

        monkeypatch.setattr("tools.collaboration_tools.chatroom_manager.send_message", fake_send_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.publish_saved_chat_message", fake_publish_saved_chat_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.store_runtime_card", fake_store_runtime_card, raising=False)
        monkeypatch.setattr("routes.api.trigger_agent_response", fake_trigger_agent_response, raising=False)
        monkeypatch.setattr("asyncio.create_task", lambda coro: spawned_coroutines.append(coro) or coro)

        result = await tool.execute(
            target_agent_name="coder",
            task_title="Collect result",
            task_description="Return a concrete answer",
            context="Need final answer text.",
            agent_id=1,
            agent_name="assistant",
            chatroom_id=100,
        )

        for coroutine in spawned_coroutines:
            await coroutine

        task = list(coordinator.task_registry.values())[0]
        assert "delegated" in result.lower()
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "Real delegated result from coder."

    @pytest.mark.asyncio
    async def test_delegate_keeps_task_in_progress_when_followup_waits_for_approval(self, coordinator, monkeypatch):
        from tools.collaboration_tools import DelegateTaskTool
        from agents.collaboration import TaskStatus

        tool = DelegateTaskTool(collaboration_coordinator=coordinator)
        spawned_coroutines = []

        async def fake_send_message(chatroom_id, agent_id, content, message_type="text", metadata=None, agent_name=None):
            return SimpleNamespace(
                id=101,
                content=content,
                message_type=message_type,
                agent_name=agent_name,
                created_at=datetime.now(),
            )

        async def fake_publish_saved_chat_message(db, chatroom_id, **kwargs):
            return None

        async def fake_store_runtime_card(chatroom_id, payload):
            return None

        async def fake_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, extra_context="", **kwargs):
            return {
                "completed": False,
                "awaiting_tool_approval": True,
                "task_run_id": 999,
            }

        monkeypatch.setattr("tools.collaboration_tools.chatroom_manager.send_message", fake_send_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.publish_saved_chat_message", fake_publish_saved_chat_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.store_runtime_card", fake_store_runtime_card, raising=False)
        monkeypatch.setattr("routes.api.trigger_agent_response", fake_trigger_agent_response, raising=False)
        monkeypatch.setattr("asyncio.create_task", lambda coro: spawned_coroutines.append(coro) or coro)

        result = await tool.execute(
            target_agent_name="coder",
            task_title="Wait for approval",
            task_description="Needs approval continuation",
            context="Should remain active.",
            agent_id=1,
            agent_name="assistant",
            chatroom_id=100,
        )
        for coroutine in spawned_coroutines:
            await coroutine

        task = list(coordinator.task_registry.values())[0]
        assert "delegated" in result.lower()
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.result == "Waiting for approval."

    @pytest.mark.asyncio
    async def test_delegate_marks_task_run_failed_when_cancelled_during_reload(self, coordinator, monkeypatch, fresh_db):
        from tools.collaboration_tools import DelegateTaskTool
        from agents.collaboration import TaskStatus

        tool = DelegateTaskTool(collaboration_coordinator=coordinator)
        spawned_coroutines = []

        async def fake_send_message(chatroom_id, agent_id, content, message_type="text", metadata=None, agent_name=None):
            return SimpleNamespace(
                id=102,
                content=content,
                message_type=message_type,
                agent_name=agent_name,
                created_at=datetime.now(),
            )

        async def fake_publish_saved_chat_message(db, chatroom_id, **kwargs):
            return None

        async def fake_store_runtime_card(chatroom_id, payload):
            return None

        async def fake_trigger_agent_response(chatroom_id, user_message, client_turn_id=None, extra_context="", **kwargs):
            db = fresh_db.SessionLocal()
            try:
                db.add(fresh_db.TaskRun(
                    chatroom_id=chatroom_id,
                    project_id=None,
                    client_turn_id=client_turn_id,
                    run_kind="project_single_agent",
                    status="running",
                    title="Delegated task",
                    user_request=user_message,
                    initiator="user",
                    target_agent_name="coder",
                ))
                db.commit()
            finally:
                db.close()
            raise asyncio.CancelledError()

        monkeypatch.setattr("tools.collaboration_tools.chatroom_manager.send_message", fake_send_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.publish_saved_chat_message", fake_publish_saved_chat_message, raising=False)
        monkeypatch.setattr("tools.collaboration_tools.store_runtime_card", fake_store_runtime_card, raising=False)
        monkeypatch.setattr("routes.api.trigger_agent_response", fake_trigger_agent_response, raising=False)
        monkeypatch.setattr("asyncio.create_task", lambda coro: spawned_coroutines.append(coro) or coro)

        result = await tool.execute(
            target_agent_name="coder",
            task_title="Cancelled run",
            task_description="Should be terminalized on cancellation",
            context="Server reload simulation.",
            agent_id=1,
            agent_name="assistant",
            chatroom_id=100,
        )
        for coroutine in spawned_coroutines:
            await coroutine

        task = list(coordinator.task_registry.values())[0]
        db = fresh_db.SessionLocal()
        try:
            task_run = db.query(fresh_db.TaskRun).filter(
                fresh_db.TaskRun.client_turn_id == f"delegate-{task.id}"
            ).first()
            assert task_run is not None
            assert task_run.status == "failed"
            assert "interrupted" in (task_run.summary or "").lower()
        finally:
            db.close()

        assert "delegated" in result.lower()
        assert task.status == TaskStatus.FAILED
        assert "interrupted" in (task.result or "").lower()

    def test_schema(self):
        from tools.collaboration_tools import DelegateTaskTool
        tool = DelegateTaskTool()
        schema = tool.get_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "target_agent_name" in props
        assert "task_title" in props
        assert "task_description" in props


class TestBroadcastMessage:
    """BroadcastMessageTool 测试"""

    @pytest.mark.asyncio
    async def test_broadcast_success(self, coordinator):
        from tools.collaboration_tools import BroadcastMessageTool
        tool = BroadcastMessageTool(collaboration_coordinator=coordinator)

        result = await tool.execute(
            message="Hello everyone!",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert "sent" in result.lower()

    @pytest.mark.asyncio
    async def test_broadcast_without_coordinator(self):
        from tools.collaboration_tools import BroadcastMessageTool
        tool = BroadcastMessageTool(collaboration_coordinator=None)
        result = await tool.execute(message="hi")
        assert "error" in result.lower() or "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_broadcast_uses_lifecycle_runtime(self, coordinator, monkeypatch):
        from tools.collaboration_tools import BroadcastMessageTool

        async def fake_broadcast_action(**kwargs):
            assert kwargs["chatroom_id"] == 100
            return "[Broadcast] lifecycle"

        monkeypatch.setattr("tools.collaboration_tools.run_broadcast_agent_action", fake_broadcast_action)

        tool = BroadcastMessageTool(collaboration_coordinator=coordinator)
        result = await tool.execute(
            message="Hello everyone!",
            agent_id=1,
            agent_name="assistant",
            chatroom_id=100,
        )

        assert result == "[Broadcast] lifecycle"


class TestCheckTaskStatus:
    """CheckTaskStatusTool 测试"""

    @pytest.mark.asyncio
    async def test_check_existing_task(self, coordinator):
        from agents.collaboration import CollaborationTask, TaskStatus, uuid
        from tools.collaboration_tools import CheckTaskStatusTool

        task_id = str(uuid.uuid4())
        coordinator.task_registry[task_id] = CollaborationTask(
            id=task_id, title="My Task", description="desc",
            status=TaskStatus.COMPLETED, created_by_agent_id=1,
            assigned_to_agent_id=2, chatroom_id=100,
            result="Done!"
        )

        tool = CheckTaskStatusTool(collaboration_coordinator=coordinator)
        result = await tool.execute(task_id=task_id)
        assert "completed" in result.lower()
        assert "done!" in result.lower()

    @pytest.mark.asyncio
    async def test_check_nonexistent_task(self, coordinator):
        from tools.collaboration_tools import CheckTaskStatusTool
        tool = CheckTaskStatusTool(collaboration_coordinator=coordinator)
        result = await tool.execute(task_id="nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_check_without_coordinator(self):
        from tools.collaboration_tools import CheckTaskStatusTool
        tool = CheckTaskStatusTool(collaboration_coordinator=None)
        result = await tool.execute(task_id="x")
        assert "error" in result.lower() or "not available" in result.lower()


class TestCoordinatorStatus:
    def test_pending_task_count_only_counts_active_statuses(self, coordinator):
        from agents.collaboration import CollaborationTask, TaskStatus, uuid

        task_defs = [
            ("pending", TaskStatus.PENDING),
            ("delegated", TaskStatus.DELEGATED),
            ("in_progress", TaskStatus.IN_PROGRESS),
            ("completed", TaskStatus.COMPLETED),
            ("failed", TaskStatus.FAILED),
        ]
        for title, status in task_defs:
            task_id = str(uuid.uuid4())
            coordinator.task_registry[task_id] = CollaborationTask(
                id=task_id,
                title=title,
                description=title,
                status=status,
                created_by_agent_id=1,
                assigned_to_agent_id=2,
                chatroom_id=100,
            )

        assert coordinator.pending_task_count() == 3
        assert coordinator.pending_task_count(chatroom_id=100) == 3
        assert coordinator.pending_task_count(chatroom_id=999) == 0


class TestListCollaborators:
    """ListCollaboratorsTool 测试"""

    @pytest.mark.asyncio
    async def test_list_with_collaborators(self, coordinator):
        from tools.collaboration_tools import ListCollaboratorsTool
        tool = ListCollaboratorsTool(collaboration_coordinator=coordinator)
        result = await tool.execute(chatroom_id=100)
        assert "assistant" in result
        assert "coder" in result
        assert "reviewer" not in result  # different chatroom

    @pytest.mark.asyncio
    async def test_list_empty_chatroom_from_db(self, coordinator, fresh_db):
        from tools.collaboration_tools import ListCollaboratorsTool
        # chatroom 999 没有注册的协作者，应 fallback 到 DB
        db = fresh_db.SessionLocal()
        project = fresh_db.Project(name="DB Fallback Project", status="active")
        db.add(project)
        db.commit()
        db.refresh(project)
        chatroom = fresh_db.Chatroom(project_id=project.id, title="DB Fallback Chat", session_type="project-bound")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        agent = fresh_db.Agent(name="db_agent", agent_type="db_agent", role="r", soul="{}", config="{}", is_active=True)
        db.add(agent)
        db.commit()
        db.refresh(agent)
        db.add(fresh_db.AgentAssignment(project_id=project.id, agent_id=agent.id))
        db.commit()

        tool = ListCollaboratorsTool(collaboration_coordinator=coordinator)
        result = await tool.execute(chatroom_id=chatroom.id)
        assert "db_agent" in result.lower()
        db.close()

    @pytest.mark.asyncio
    async def test_list_without_coordinator(self):
        from tools.collaboration_tools import ListCollaboratorsTool
        tool = ListCollaboratorsTool(collaboration_coordinator=None)
        result = await tool.execute()
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_list_uses_lifecycle_runtime(self, coordinator, monkeypatch):
        from tools.collaboration_tools import ListCollaboratorsTool

        monkeypatch.setattr(
            "tools.collaboration_tools.run_list_collaborators_action",
            lambda **kwargs: "[List Collaborators] lifecycle coder",
        )

        tool = ListCollaboratorsTool(collaboration_coordinator=coordinator)
        result = await tool.execute(chatroom_id=100)

        assert "coder" in result


class TestSendDirectMessage:
    """SendDirectMessageTool 测试"""

    @pytest.mark.asyncio
    async def test_dm_success(self, coordinator):
        from tools.collaboration_tools import SendDirectMessageTool
        tool = SendDirectMessageTool(collaboration_coordinator=coordinator)
        result = await tool.execute(
            target_agent_name="coder",
            message="Hey coder, need help",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert "sent" in result.lower()

    @pytest.mark.asyncio
    async def test_dm_to_nonexistent(self, coordinator):
        from tools.collaboration_tools import SendDirectMessageTool
        tool = SendDirectMessageTool(collaboration_coordinator=coordinator)
        result = await tool.execute(
            target_agent_name="ghost", message="hi",
            agent_id=1, agent_name="assistant", chatroom_id=100
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_dm_without_coordinator(self):
        from tools.collaboration_tools import SendDirectMessageTool
        tool = SendDirectMessageTool(collaboration_coordinator=None)
        result = await tool.execute(target_agent_name="x", message="hi")
        assert "error" in result.lower() or "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_dm_uses_lifecycle_runtime(self, coordinator, monkeypatch):
        from tools.collaboration_tools import SendDirectMessageTool

        async def fake_direct_action(**kwargs):
            assert kwargs["target_agent_name"] == "coder"
            return "[Direct Message] lifecycle"

        monkeypatch.setattr("tools.collaboration_tools.run_direct_message_agent_action", fake_direct_action)

        tool = SendDirectMessageTool(collaboration_coordinator=coordinator)
        result = await tool.execute(
            target_agent_name="coder",
            message="Hey coder",
            agent_id=1,
            agent_name="assistant",
            chatroom_id=100,
        )

        assert result == "[Direct Message] lifecycle"


class TestListAgents:
    @pytest.mark.asyncio
    async def test_list_external_agents(self, fresh_db):
        from tools.collaboration_tools import ListAgentsTool

        db = fresh_db.SessionLocal()
        try:
            project = fresh_db.Project(name="Invite Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)

            chatroom = fresh_db.Chatroom(project_id=project.id, title="Invite Chat", session_type="project-bound")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            in_room = fresh_db.Agent(name="analyst", agent_type="analyst", role="Analyst", soul="{}", config="{}", is_active=True)
            external = fresh_db.Agent(name="security", agent_type="security", role="Security", soul="{}", config="{}", is_active=True)
            db.add_all([in_room, external])
            db.commit()
            db.refresh(in_room)
            db.refresh(external)
            db.add(fresh_db.AgentAssignment(project_id=project.id, agent_id=in_room.id))
            db.commit()

            tool = ListAgentsTool()
            result = await tool.execute(chatroom_id=chatroom.id)
        finally:
            db.close()

        assert "security" in result.lower()
        assert "analyst" not in result.lower()

    @pytest.mark.asyncio
    async def test_list_agents_uses_lifecycle_runtime(self, monkeypatch):
        from tools.collaboration_tools import ListAgentsTool

        monkeypatch.setattr(
            "tools.collaboration_tools.run_list_agents_action",
            lambda **kwargs: "[Directory] security",
        )

        tool = ListAgentsTool()
        result = await tool.execute(chatroom_id=100)

        assert "security" in result.lower()


class TestInviteAgent:
    @pytest.mark.asyncio
    async def test_invite_agent_adds_assignment(self, fresh_db):
        from agents.collaboration import collaboration_coordinator
        from tools.collaboration_tools import InviteAgentTool

        db = fresh_db.SessionLocal()
        try:
            project = fresh_db.Project(name="Join Project", status="active")
            db.add(project)
            db.commit()
            db.refresh(project)
            project_id = project.id

            chatroom = fresh_db.Chatroom(project_id=project.id, title="Join Chat", session_type="project-bound")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            chatroom_id = chatroom.id

            target = fresh_db.Agent(name="tester", agent_type="tester", role="Tester", soul="{}", config="{}", is_active=True)
            db.add(target)
            db.commit()
            db.refresh(target)
            target_id = target.id
        finally:
            db.close()

        tool = InviteAgentTool()
        result = await tool.execute(agent_name="tester", chatroom_id=chatroom_id)

        db = fresh_db.SessionLocal()
        try:
            assignment = db.query(fresh_db.AgentAssignment).filter(
                fresh_db.AgentAssignment.project_id == project_id,
                fresh_db.AgentAssignment.agent_id == target_id,
            ).first()
            assert assignment is not None
        finally:
            db.close()

        assert "joined this room" in result.lower()
        assert target_id in collaboration_coordinator.collaborators

    @pytest.mark.asyncio
    async def test_invite_agent_uses_lifecycle_runtime(self, monkeypatch):
        from tools.collaboration_tools import InviteAgentTool

        monkeypatch.setattr(
            "tools.collaboration_tools.run_invite_agent_action",
            lambda **kwargs: "[Invite] lifecycle",
        )

        tool = InviteAgentTool()
        result = await tool.execute(agent_name="tester", chatroom_id=100)

        assert result == "[Invite] lifecycle"
