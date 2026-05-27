"""
Pytest 共享 fixtures
"""
import pytest
import os
import sys
import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_APP_RESET_SKIP_RELOAD = {
    "services.task_run_control",
    "services.orchestration_recovery_lease",
}


def reset_app_modules(module_names):
    """Reload app modules for a new test environment without orphaning collected imports."""

    requested = [module_name for module_name in module_names if module_name not in _APP_RESET_SKIP_RELOAD]
    if "main" in requested and "services.runtime_lifecycle" not in requested:
        requested.append("services.runtime_lifecycle")
    if "models.database" in requested:
        from sqlalchemy.orm import clear_mappers

        clear_mappers()
    if "models.database" in requested and "models.audit" not in requested:
        requested.append("models.audit")
    ordered = sorted(
        requested,
        key=lambda module_name: (
            0 if module_name == "config"
            else 1 if module_name == "models.database"
            else 2 if module_name.startswith("models.")
            else 3 if module_name == "pipeline.config"
            else 3 if module_name.startswith("services.")
            else 4 if module_name.startswith("agents.") or module_name in {"tools", "llm.client", "chatrooms.manager", "pipeline.engine"}
            else 5 if module_name.startswith("routes.")
            else 6 if module_name == "main"
            else 7,
            module_name,
        ),
    )
    for module_name in ordered:
        if module_name in _APP_RESET_SKIP_RELOAD:
            continue
        module = sys.modules.get(module_name)
        if module is not None:
            importlib.reload(module)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """所有测试自动设置环境变量"""
    catown_home = tmp_path / "catown-home"
    monkeypatch.setenv("CATOWN_HOME", str(catown_home))
    monkeypatch.setenv("CATOWN_STATE_DIR", str(catown_home / "state"))
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.delenv("AGENT_CONFIG_FILE", raising=False)
    monkeypatch.delenv("PIPELINE_CONFIG_FILE", raising=False)
    monkeypatch.delenv("SKILLS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("SKILL_MARKETPLACES_CONFIG_FILE", raising=False)
    monkeypatch.delenv("CATOWN_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CATOWN_PROJECTS_ROOT", raising=False)
    monkeypatch.delenv("CATOWN_WORKSPACES_DIR", raising=False)


@pytest.fixture
def tmp_workspace(tmp_path):
    """提供临时工作目录（用于文件操作测试）"""
    return str(tmp_path)


@pytest.fixture
def mock_llm_client():
    """Mock LLM 客户端（通用）"""
    client = MagicMock()
    client.chat = AsyncMock(return_value="Mocked response.")
    client.chat_with_tools = AsyncMock(return_value={
        "content": "Mocked agent response.",
        "tool_calls": None
    })

    async def mock_stream(messages, tools=None):
        yield {"type": "content", "delta": "Hello "}
        yield {"type": "content", "delta": "world!"}
        yield {"type": "done", "full_content": "Hello world!", "tool_calls": None}

    client.chat_stream = mock_stream
    client.base_url = "http://localhost:9999/v1"
    client.model = "test-model"
    return client


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """创建全新的测试数据库（含所有表）"""
    db_path = str(tmp_path / "test_fresh.db")
    monkeypatch.setenv("DATABASE_URL", db_path)

    # 重新导入以使用新的 DATABASE_URL
    import importlib
    from sqlalchemy.orm import clear_mappers

    clear_mappers()
    import models.database as db_mod
    importlib.reload(db_mod)
    _reload_model_bound_service_modules(importlib)

    db_mod.Base.metadata.create_all(bind=db_mod.engine)
    return db_mod


def _reload_model_bound_service_modules(importlib_module):
    """Refresh service globals that hold SQLAlchemy model classes after DB reload."""

    module_names = [
        "services.runtime_lifecycle",
        "services.run_ledger",
        "services.runner_lifecycle",
        "services.approval_queue",
        "services.approval_audit",
        "services.telemetry_writer",
        "services.audit_recorder",
        "services.chat_publish",
        "services.chat_prompt_builder",
        "services.chat_timeline_projection",
        "services.task_activity_projection",
        "services.task_run_watchdog",
        "services.user_visible_step_projection",
        "services.monitor_projection",
        "services.agent_lifecycle_runtime",
        "services.agent_action_runtime",
        "services.collaboration_dispatch_runtime",
        "services.orchestration_events",
        "services.orchestration_handoffs",
        "services.orchestration_inbox",
        "services.orchestration_finalizer",
        "services.orchestration_guards",
        "services.orchestration_step_completion",
        "services.orchestration_agent_turn",
        "services.orchestration_step_runner",
        "services.orchestration_runtime_runner",
        "services.orchestration_recovery_prepare",
        "services.orchestration_recovery_runner",
        "services.task_run_watchdog",
        "services.orchestration_stream_runner",
    ]
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if module is not None:
            importlib_module.reload(module)


@pytest.fixture
def db_session(fresh_db):
    """提供数据库会话（自动关闭）"""
    db = fresh_db.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def populated_db(fresh_db):
    """预填充数据的数据库（含 Agent、项目、聊天室）"""
    db = fresh_db.SessionLocal()
    try:
        # 创建 agents
        assistant = fresh_db.Agent(
            name="assistant", role="通用助手",
            system_prompt="You are a helpful assistant.",
            tools='["web_search"]', is_active=True
        )
        coder = fresh_db.Agent(
            name="coder", role="代码专家",
            system_prompt="You are a coding expert.",
            tools='["execute_code", "web_search"]', is_active=True
        )
        db.add_all([assistant, coder])
        db.commit()
        db.refresh(assistant)
        db.refresh(coder)

        # 创建项目
        project = fresh_db.Project(name="Test Project", description="A test project", status="active")
        db.add(project)
        db.commit()
        db.refresh(project)

        # 创建聊天室
        chatroom = fresh_db.Chatroom(project_id=project.id)
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        # 分配 agent
        assignment1 = fresh_db.AgentAssignment(project_id=project.id, agent_id=assistant.id)
        assignment2 = fresh_db.AgentAssignment(project_id=project.id, agent_id=coder.id)
        db.add_all([assignment1, assignment2])
        db.commit()

        # 添加记忆
        memory = fresh_db.Memory(
            agent_id=assistant.id, memory_type="long_term",
            content="User prefers dark mode", importance=8
        )
        db.add(memory)
        db.commit()

        # 添加消息
        msg1 = fresh_db.Message(
            chatroom_id=chatroom.id, agent_id=None,
            content="Hello", message_type="text"
        )
        msg2 = fresh_db.Message(
            chatroom_id=chatroom.id, agent_id=assistant.id,
            content="Hi there!", message_type="text"
        )
        db.add_all([msg1, msg2])
        db.commit()

        return {
            "db": fresh_db,
            "session": db,
            "assistant": assistant,
            "coder": coder,
            "project": project,
            "chatroom": chatroom,
        }
    finally:
        db.close()
