"""
协作工具测试

覆盖 delegate_task / broadcast_message / check_task_status / list_collaborators / send_direct_message
"""
import pytest
import sys
import os
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
        db.add(fresh_db.Agent(name="db_agent", role="r", system_prompt="s"))
        db.commit()

        tool = ListCollaboratorsTool(collaboration_coordinator=coordinator)
        result = await tool.execute(chatroom_id=999)
        assert "db_agent" in result
        db.close()

    @pytest.mark.asyncio
    async def test_list_without_coordinator(self):
        from tools.collaboration_tools import ListCollaboratorsTool
        tool = ListCollaboratorsTool(collaboration_coordinator=None)
        result = await tool.execute()
        assert "not available" in result.lower()


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
