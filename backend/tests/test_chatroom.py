"""
聊天室管理器测试

覆盖 ChatroomManager 的 CRUD、消息发送、消息获取
"""
import pytest
import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestChatroomCreation:
    """聊天室创建测试"""

    @pytest.mark.asyncio
    async def test_create_chatroom(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="CR Project")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        manager = ChatroomManager()
        chatroom_id = await manager.create_chatroom(project.id, project.name)

        assert chatroom_id is not None
        assert chatroom_id > 0
        assert manager.get_chatroom(chatroom_id) is not None
        db.close()

    @pytest.mark.asyncio
    async def test_chatroom_instance_properties(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="Prop Project")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        manager = ChatroomManager()
        chatroom_id = await manager.create_chatroom(project.id, "Prop Project")

        instance = manager.get_chatroom(chatroom_id)
        assert instance.project_id == project.id
        assert instance.is_active is True
        assert instance.get_agent_count() == 0
        db.close()


class TestChatroomInstance:
    """ChatroomInstance 测试"""

    def test_add_remove_agent(self, fresh_db):
        from chatrooms.manager import ChatroomInstance

        inst = ChatroomInstance(id=1, project_id=1, project_name="Test")
        inst.add_agent(10)
        inst.add_agent(20)
        assert inst.get_agent_count() == 2

        inst.remove_agent(10)
        assert inst.get_agent_count() == 1

    def test_add_duplicate_agent(self, fresh_db):
        from chatrooms.manager import ChatroomInstance

        inst = ChatroomInstance(id=1, project_id=1, project_name="Test")
        inst.add_agent(10)
        inst.add_agent(10)
        assert inst.get_agent_count() == 1


class TestSendMessage:
    """消息发送测试"""

    @pytest.mark.asyncio
    async def test_send_user_message(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="MsgP")
        chatroom = fresh_db.Chatroom(project_id=1)
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)
        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        msg = await manager.send_message(
            chatroom_id=chatroom.id,
            agent_id=None,
            content="Hello world",
            message_type="text"
        )

        assert msg.id is not None
        assert msg.content == "Hello world"
        assert msg.agent_id is None
        assert msg.message_type == "text"
        db.close()

    @pytest.mark.asyncio
    async def test_send_agent_message(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        agent = fresh_db.Agent(name="sender", role="r", system_prompt="s")
        project = fresh_db.Project(name="AgentMsgP")
        db = fresh_db.SessionLocal()
        db.add_all([agent, project])
        db.commit()
        db.refresh(agent)
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        msg = await manager.send_message(
            chatroom_id=chatroom.id,
            agent_id=agent.id,
            content="Agent says hi",
            message_type="text",
            agent_name="sender"
        )

        assert msg.agent_name == "sender"
        assert msg.agent_id == agent.id
        db.close()

    @pytest.mark.asyncio
    async def test_send_message_with_metadata(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="MetaP")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        msg = await manager.send_message(
            chatroom_id=chatroom.id,
            agent_id=None,
            content="Meta msg",
            message_type="text",
            metadata={"key": "value"}
        )

        assert msg.metadata == {"key": "value"}
        db.close()


class TestGetMessages:
    """消息获取测试"""

    @pytest.mark.asyncio
    async def test_get_messages_empty(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="EmptyMsgP")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        msgs = await manager.get_messages(chatroom.id)
        assert msgs == []
        db.close()

    @pytest.mark.asyncio
    async def test_get_messages_order(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="OrderMsgP")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        for i in range(5):
            await manager.send_message(chatroom.id, None, f"msg_{i}", "text")

        msgs = await manager.get_messages(chatroom.id)
        assert len(msgs) == 5
        assert msgs[0].content == "msg_0"
        assert msgs[4].content == "msg_4"
        db.close()

    @pytest.mark.asyncio
    async def test_get_messages_limit(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="LimitP")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        for i in range(10):
            await manager.send_message(chatroom.id, None, f"msg_{i}", "text")

        msgs = await manager.get_messages(chatroom.id, limit=3)
        assert len(msgs) == 3
        # 最新的 3 条
        assert msgs[0].content == "msg_7"
        db.close()


class TestProcessUserMessage:
    """用户消息处理测试"""

    @pytest.mark.asyncio
    async def test_process_user_message(self, fresh_db):
        from chatrooms.manager import ChatroomManager

        project = fresh_db.Project(name="ProcP")
        db = fresh_db.SessionLocal()
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        manager = ChatroomManager()
        manager.chatrooms[chatroom.id] = manager.ChatroomInstance(
            id=chatroom.id, project_id=project.id, project_name=project.name
        ) if hasattr(manager, 'ChatroomInstance') else None

        # process_user_message 只保存用户消息（TODO 协作逻辑未完成）
        responses = await manager.process_user_message(chatroom.id, "Test message")
        assert len(responses) >= 1
        assert responses[0].content == "Test message"
        db.close()
