"""
数据库模型测试

覆盖 Agent / Project / Chatroom / Message / Memory 表的 CRUD 和关系
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAgentModel:
    """Agent 表测试"""

    def test_create_agent(self, db_session, fresh_db):
        agent = fresh_db.Agent(
            name="test_agent", role="测试员",
            system_prompt="You test things.", tools='["web_search"]', is_active=True
        )
        db_session.add(agent)
        db_session.commit()
        db_session.refresh(agent)

        assert agent.id is not None
        assert agent.name == "test_agent"
        assert agent.role == "测试员"
        assert agent.is_active is True
        assert agent.created_at is not None

    def test_agent_unique_name(self, db_session, fresh_db):
        a1 = fresh_db.Agent(name="dup", role="r1", system_prompt="s1")
        db_session.add(a1)
        db_session.commit()

        a2 = fresh_db.Agent(name="dup", role="r2", system_prompt="s2")
        db_session.add(a2)
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_agent_inactive(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="inactive", role="x", system_prompt="x", is_active=False)
        db_session.add(agent)
        db_session.commit()
        found = db_session.query(fresh_db.Agent).filter(fresh_db.Agent.name == "inactive").first()
        assert found.is_active is False

    def test_agent_query_active(self, db_session, fresh_db):
        for i in range(3):
            db_session.add(fresh_db.Agent(name=f"agent_{i}", role="r", system_prompt="s", is_active=i != 2))
        db_session.commit()

        active = db_session.query(fresh_db.Agent).filter(fresh_db.Agent.is_active == True).all()
        assert len(active) == 2


class TestProjectModel:
    """Project 表测试"""

    def test_create_project(self, db_session, fresh_db):
        project = fresh_db.Project(name="My Project", description="Desc", status="active")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.id is not None
        assert project.name == "My Project"
        assert project.status == "active"
        assert project.created_at is not None

    def test_project_default_status(self, db_session, fresh_db):
        project = fresh_db.Project(name="Default Status")
        db_session.add(project)
        db_session.commit()
        assert project.status == "active"


class TestChatroomModel:
    """Chatroom 表测试"""

    def test_create_chatroom(self, db_session, fresh_db):
        project = fresh_db.Project(name="P")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db_session.add(chatroom)
        db_session.commit()
        db_session.refresh(chatroom)

        assert chatroom.id is not None
        assert chatroom.project_id == project.id

    def test_chatroom_project_relationship(self, db_session, fresh_db):
        project = fresh_db.Project(name="Rel Project")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db_session.add(chatroom)
        db_session.commit()

        found = db_session.query(fresh_db.Project).filter(fresh_db.Project.id == project.id).first()
        assert found.chatroom is not None
        assert found.chatroom.id == chatroom.id

    def test_chatroom_unique_per_project(self, db_session, fresh_db):
        project = fresh_db.Project(name="UniP")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        c1 = fresh_db.Chatroom(project_id=project.id)
        db_session.add(c1)
        db_session.commit()

        c2 = fresh_db.Chatroom(project_id=project.id)
        db_session.add(c2)
        with pytest.raises(Exception):  # IntegrityError (unique constraint)
            db_session.commit()


class TestMessageModel:
    """Message 表测试"""

    def test_create_user_message(self, db_session, fresh_db):
        project = fresh_db.Project(name="MP")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db_session.add(chatroom)
        db_session.commit()
        db_session.refresh(chatroom)

        msg = fresh_db.Message(
            chatroom_id=chatroom.id, agent_id=None,
            content="Hello!", message_type="text"
        )
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        assert msg.id is not None
        assert msg.agent_id is None  # 用户消息
        assert msg.content == "Hello!"

    def test_create_agent_message(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="msg_agent", role="r", system_prompt="s")
        project = fresh_db.Project(name="MP2")
        db_session.add_all([agent, project])
        db_session.commit()
        db_session.refresh(agent)
        db_session.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db_session.add(chatroom)
        db_session.commit()
        db_session.refresh(chatroom)

        msg = fresh_db.Message(
            chatroom_id=chatroom.id, agent_id=agent.id,
            content="Agent reply", message_type="text"
        )
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        assert msg.agent is not None
        assert msg.agent.name == "msg_agent"

    def test_messages_order(self, db_session, fresh_db):
        project = fresh_db.Project(name="OrderP")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db_session.add(chatroom)
        db_session.commit()
        db_session.refresh(chatroom)

        for i in range(5):
            db_session.add(fresh_db.Message(
                chatroom_id=chatroom.id, content=f"msg_{i}", message_type="text"
            ))
        db_session.commit()

        msgs = db_session.query(fresh_db.Message).filter(
            fresh_db.Message.chatroom_id == chatroom.id
        ).order_by(fresh_db.Message.created_at.asc()).all()

        assert len(msgs) == 5
        assert msgs[0].content == "msg_0"
        assert msgs[4].content == "msg_4"


class TestMemoryModel:
    """Memory 表测试"""

    def test_create_memory(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="mem_agent", role="r", system_prompt="s")
        db_session.add(agent)
        db_session.commit()
        db_session.refresh(agent)

        mem = fresh_db.Memory(
            agent_id=agent.id, memory_type="long_term",
            content="User likes coffee", importance=7
        )
        db_session.add(mem)
        db_session.commit()
        db_session.refresh(mem)

        assert mem.id is not None
        assert mem.importance == 7
        assert mem.created_at is not None

    def test_memory_query_by_importance(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="imp_agent", role="r", system_prompt="s")
        db_session.add(agent)
        db_session.commit()
        db_session.refresh(agent)

        for i in range(1, 6):
            db_session.add(fresh_db.Memory(
                agent_id=agent.id, memory_type="long_term",
                content=f"Memory {i}", importance=i * 2
            ))
        db_session.commit()

        top = db_session.query(fresh_db.Memory).filter(
            fresh_db.Memory.agent_id == agent.id
        ).order_by(fresh_db.Memory.importance.desc()).limit(3).all()

        assert len(top) == 3
        assert top[0].importance == 10
        assert top[2].importance == 6

    def test_memory_agent_relationship(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="rel_agent", role="r", system_prompt="s")
        db_session.add(agent)
        db_session.commit()
        db_session.refresh(agent)

        db_session.add(fresh_db.Memory(agent_id=agent.id, memory_type="short_term", content="m1"))
        db_session.add(fresh_db.Memory(agent_id=agent.id, memory_type="long_term", content="m2"))
        db_session.commit()

        found = db_session.query(fresh_db.Agent).filter(fresh_db.Agent.id == agent.id).first()
        assert len(found.memories) == 2


class TestAgentAssignment:
    """AgentAssignment 表测试"""

    def test_assign_agent_to_project(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="assign_agent", role="r", system_prompt="s")
        project = fresh_db.Project(name="AssignP")
        db_session.add_all([agent, project])
        db_session.commit()
        db_session.refresh(agent)
        db_session.refresh(project)

        assignment = fresh_db.AgentAssignment(project_id=project.id, agent_id=agent.id)
        db_session.add(assignment)
        db_session.commit()

        found = db_session.query(fresh_db.AgentAssignment).filter(
            fresh_db.AgentAssignment.project_id == project.id
        ).all()
        assert len(found) == 1

    def test_multiple_agents_in_project(self, db_session, fresh_db):
        project = fresh_db.Project(name="MultiP")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        for i in range(4):
            agent = fresh_db.Agent(name=f"multi_{i}", role="r", system_prompt="s")
            db_session.add(agent)
            db_session.commit()
            db_session.refresh(agent)
            db_session.add(fresh_db.AgentAssignment(project_id=project.id, agent_id=agent.id))
        db_session.commit()

        count = db_session.query(fresh_db.AgentAssignment).filter(
            fresh_db.AgentAssignment.project_id == project.id
        ).count()
        assert count == 4


class TestCascadeDelete:
    """删除测试（模型未配置级联，验证实际行为）"""

    def test_delete_agent_removes_from_db(self, db_session, fresh_db):
        agent = fresh_db.Agent(name="del_agent", role="r", system_prompt="s")
        db_session.add(agent)
        db_session.commit()
        db_session.refresh(agent)
        agent_id = agent.id

        db_session.delete(agent)
        db_session.commit()

        remaining = db_session.query(fresh_db.Agent).filter(
            fresh_db.Agent.id == agent_id
        ).all()
        assert len(remaining) == 0

    def test_delete_project_removes_from_db(self, db_session, fresh_db):
        project = fresh_db.Project(name="DelP")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        db_session.delete(project)
        db_session.commit()

        remaining = db_session.query(fresh_db.Project).filter(
            fresh_db.Project.name == "DelP"
        ).all()
        assert len(remaining) == 0

    def test_delete_message_removes_from_db(self, db_session, fresh_db):
        project = fresh_db.Project(name="MsgDelP")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        chatroom = fresh_db.Chatroom(project_id=project.id)
        db_session.add(chatroom)
        db_session.commit()
        db_session.refresh(chatroom)

        msg = fresh_db.Message(chatroom_id=chatroom.id, content="bye", message_type="text")
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        db_session.delete(msg)
        db_session.commit()

        remaining = db_session.query(fresh_db.Message).filter(
            fresh_db.Message.id == msg.id
        ).all()
        assert len(remaining) == 0
