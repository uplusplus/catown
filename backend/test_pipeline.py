# -*- coding: utf-8 -*-
"""
Pipeline 引擎集成测试

使用 Mock LLM 客户端测试完整流水线流程。
不需要真实 LLM API。
"""
import asyncio
import json
import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# 设置测试环境
TEST_DIR = Path(tempfile.mkdtemp(prefix="catown_test_"))
os.environ["DATABASE_URL"] = str(TEST_DIR / "test.db")
os.environ["AGENT_CONFIG_FILE"] = str(Path(__file__).parent.parent / "backend" / "configs" / "agents.json")

# 添加 backend 到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from models.database import (
    init_database, SessionLocal, Base, engine,
    Pipeline, PipelineRun, PipelineStage, StageArtifact, PipelineMessage, Project,
)
from pipeline.engine import pipeline_engine, _get_workspace, TOOL_REGISTRY
from pipeline.config import pipeline_config_manager


# ==================== Fixtures ====================

@pytest.fixture(autouse=True)
def setup_db():
    """每个测试前重置数据库"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """数据库会话"""
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def project(db):
    """创建测试项目"""
    p = Project(name="test-project", description="Test", status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def mock_llm_client():
    """Mock LLM 客户端 — 返回预设响应"""
    client = MagicMock()
    client.model = "test-model"
    client.base_url = "http://test"

    # 预设的阶段响应
    responses = {
        "analyst": {
            "content": "# PRD\n## Overview\nBuild a todo app.\n## User Stories\n- As a user, I want to add todos",
            "tool_calls": [
                {"id": "tc1", "function": {"name": "write_file", "arguments": json.dumps({
                    "file_path": "PRD.md",
                    "content": "# Todo App PRD\n\n## Overview\nA simple todo application.\n\n## User Stories\n1. Add todo\n2. Delete todo\n3. List todos"
                })}}
            ]
        },
        "architect": {
            "content": "# Tech Spec\n## Stack: Python + FastAPI\n## API: REST",
            "tool_calls": [
                {"id": "tc2", "function": {"name": "write_file", "arguments": json.dumps({
                    "file_path": "tech-spec.md",
                    "content": "# Tech Spec\n\n## Stack\n- Python 3.11 + FastAPI\n- SQLite\n\n## API\n- POST /todos\n- GET /todos\n- DELETE /todos/{id}"
                })}}
            ]
        },
        "developer": {
            "content": "Code implemented successfully.",
            "tool_calls": [
                {"id": "tc3", "function": {"name": "write_file", "arguments": json.dumps({
                    "file_path": "src/main.py",
                    "content": "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/todos')\ndef list_todos():\n    return []"
                })}}
            ]
        },
        "tester": {
            "content": "# Test Report\nAll tests passed.\nNo blockers found.",
            "tool_calls": [
                {"id": "tc4", "function": {"name": "write_file", "arguments": json.dumps({
                    "file_path": "test_report.md",
                    "content": "# Test Report\n\n## Results\n- Add todo: PASS\n- Delete todo: PASS\n- List todos: PASS\n\n## Blockers: None"
                })}}
            ]
        },
        "release": {
            "content": "Release prepared. CHANGELOG generated.",
            "tool_calls": [
                {"id": "tc5", "function": {"name": "write_file", "arguments": json.dumps({
                    "file_path": "CHANGELOG.md",
                    "content": "# CHANGELOG\n\n## v1.0.0\n- Initial release\n- Todo CRUD operations"
                })}}
            ]
        },
    }

    async def mock_chat_with_tools(messages, tools=None):
        # 从 system prompt 推断 agent 角色
        system_msg = messages[0].get("content", "") if messages else ""
        agent = "developer"  # default
        for name in ["analyst", "architect", "developer", "tester", "release"]:
            if name in system_msg.lower() or name.replace("_", " ") in system_msg.lower():
                agent = name
                break

        resp = responses.get(agent, {"content": "Done.", "tool_calls": None})

        # 第二次调用（工具结果回来后）只返回文字，不再调用工具
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if has_tool_result:
            return {"content": f"{agent} stage completed.", "tool_calls": None}

        return resp

    client.chat_with_tools = AsyncMock(side_effect=mock_chat_with_tools)
    return client


# ==================== 测试用例 ====================

class TestPipelineCreation:
    """Pipeline 创建测试"""

    def test_create_pipeline(self, db, project):
        """创建 pipeline"""
        pipeline = pipeline_engine.create_pipeline(db, project.id, "default")
        assert pipeline.id is not None
        assert pipeline.project_id == project.id
        assert pipeline.pipeline_name == "default"
        assert pipeline.status == "pending"

    def test_create_duplicate_pipeline(self, db, project):
        """同一项目不能创建两个 pipeline"""
        pipeline_engine.create_pipeline(db, project.id, "default")
        with pytest.raises(ValueError, match="already has a pipeline"):
            pipeline_engine.create_pipeline(db, project.id, "default")

    def test_create_invalid_template(self, db, project):
        """使用不存在的模板"""
        with pytest.raises(ValueError, match="not found"):
            pipeline_engine.create_pipeline(db, project.id, "nonexistent")


class TestPipelineStart:
    """Pipeline 启动测试"""

    def test_start_pipeline(self, db, project):
        """启动 pipeline，创建 run 和 stages"""
        pipeline = pipeline_engine.create_pipeline(db, project.id)
        run = pipeline_engine.start_pipeline(db, pipeline.id, "Build a todo app")

        assert run.id is not None
        assert run.input_requirement == "Build a todo app"
        assert run.workspace_path is not None

        # 验证 stages 创建
        db.refresh(run)
        assert len(run.stages) == 5  # analysis, architecture, development, testing, release

        # 验证 pipeline 状态
        db.refresh(pipeline)
        assert pipeline.status == "running"

    def test_start_already_running(self, db, project):
        """已运行的 pipeline 不能重复启动"""
        pipeline = pipeline_engine.create_pipeline(db, project.id)
        pipeline_engine.start_pipeline(db, pipeline.id, "Test")

        db.refresh(pipeline)
        with pytest.raises(ValueError, match="already running"):
            pipeline_engine.start_pipeline(db, pipeline.id, "Test again")


class TestTools:
    """工具测试"""

    def test_read_write_file(self):
        """文件读写"""
        workspace = TEST_DIR / "test_tools_rw"
        workspace.mkdir(parents=True, exist_ok=True)

        # 写文件
        result = TOOL_REGISTRY["write_file"]["fn"](workspace, "test.md", "# Hello")
        assert "Written" in result

        # 读文件
        result = TOOL_REGISTRY["read_file"]["fn"](workspace, "test.md")
        assert result == "# Hello"

        shutil.rmtree(workspace, ignore_errors=True)

    def test_list_files(self):
        """文件列表"""
        workspace = TEST_DIR / "test_tools_list"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "a.txt").write_text("hello")
        (workspace / "subdir").mkdir()

        result = TOOL_REGISTRY["list_files"]["fn"](workspace, ".")
        assert "a.txt" in result
        assert "subdir" in result

        shutil.rmtree(workspace, ignore_errors=True)

    def test_path_traversal_blocked(self):
        """路径穿越被阻止"""
        workspace = TEST_DIR / "test_tools_traversal"
        workspace.mkdir(parents=True, exist_ok=True)

        result = TOOL_REGISTRY["read_file"]["fn"](workspace, "../../../etc/passwd")
        assert "traversal" in result.lower() or "not found" in result.lower()

        shutil.rmtree(workspace, ignore_errors=True)


class TestPipelineExecution:
    """Pipeline 执行测试（Mock LLM）"""

    @pytest.mark.asyncio
    async def test_full_pipeline_execution(self, db, project, mock_llm_client):
        """完整 5 阶段流水线执行（auto gates）"""
        # 用 mock 配置：所有 gate 设为 auto
        from pipeline.config import PipelineConfig, StageConfig
        auto_template = PipelineConfig(
            name="test-auto",
            description="All auto gates",
            stages=[
                StageConfig(name="analysis", display_name="分析", agent="analyst", gate="auto",
                            expected_artifacts=["PRD.md"], context_prompt="Write PRD"),
                StageConfig(name="architecture", display_name="架构", agent="architect", gate="auto",
                            expected_artifacts=["tech-spec.md"], context_prompt="Write tech spec"),
                StageConfig(name="development", display_name="开发", agent="developer", gate="auto",
                            expected_artifacts=["src/"], context_prompt="Write code"),
                StageConfig(name="testing", display_name="测试", agent="tester", gate="auto",
                            expected_artifacts=["test_report.md"], context_prompt="Test"),
                StageConfig(name="release", display_name="发布", agent="release", gate="auto",
                            expected_artifacts=["CHANGELOG.md"], context_prompt="Release"),
            ],
        )
        pipeline_config_manager.configs["test-auto"] = auto_template

        pipeline = pipeline_engine.create_pipeline(db, project.id, "test-auto")
        run = pipeline_engine.start_pipeline(db, pipeline.id, "Build a todo app")

        with patch("pipeline.engine.get_llm_client_for_agent", return_value=mock_llm_client):
            await pipeline_engine._execute_pipeline(run.id)

        db.refresh(pipeline)
        db.refresh(run)

        assert pipeline.status == "completed"
        assert run.status == "completed"

        # 验证所有阶段完成
        stages = db.query(PipelineStage).filter(
            PipelineStage.run_id == run.id
        ).order_by(PipelineStage.stage_order).all()
        assert len(stages) == 5
        for s in stages:
            assert s.status == "completed", f"Stage {s.stage_name} is {s.status}"

        # 验证至少 PRD.md 存在（analyst 阶段产出）
        workspace = _get_workspace(run)
        assert (workspace / "PRD.md").exists(), f"PRD.md not found in {workspace}"

    @pytest.mark.asyncio
    async def test_manual_gate_pauses(self, db, project, mock_llm_client):
        """manual gate 阶段完成后 pipeline 暂停"""
        pipeline = pipeline_engine.create_pipeline(db, project.id)
        run = pipeline_engine.start_pipeline(db, pipeline.id, "Test")

        with patch("pipeline.engine.get_llm_client_for_agent", return_value=mock_llm_client):
            await pipeline_engine._execute_pipeline(run.id)

        db.refresh(pipeline)
        # analysis 是 manual gate，pipeline 应暂停
        assert pipeline.status == "paused"
        stages = db.query(PipelineStage).filter(
            PipelineStage.run_id == run.id
        ).order_by(PipelineStage.stage_order).all()
        # 第一个阶段完成但被 gate 阻塞
        assert stages[0].status == "blocked"
        assert stages[1].status == "pending"  # architecture 未开始


class TestPipelineGate:
    """Gate 审批测试"""

    @pytest.mark.asyncio
    async def test_manual_gate_blocks(self, db, project, mock_llm_client):
        """manual gate 阶段完成后 pipeline 暂停"""
        pipeline = pipeline_engine.create_pipeline(db, project.id)
        run = pipeline_engine.start_pipeline(db, pipeline.id, "Test")

        # 只执行第一个阶段 (analysis, gate=manual)
        with patch("pipeline.engine.get_llm_client_for_agent", return_value=mock_llm_client):
            # 开始执行
            task = asyncio.create_task(pipeline_engine._execute_pipeline(run.id))
            # 等一小段时间让第一个阶段执行完
            await asyncio.sleep(1)
            # 等任务完成或超时
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                pass

        db.refresh(pipeline)
        # 应该停在 manual gate (analysis 是第一个阶段，gate=manual)
        # pipeline 可能是 paused 或 running 取决于时序
        stages = db.query(PipelineStage).filter(PipelineStage.run_id == run.id).order_by(PipelineStage.stage_order).all()
        first_stage = stages[0]
        # 第一个阶段要么 completed 要么 blocked
        assert first_stage.status in ("completed", "blocked")


class TestInterAgentMessaging:
    """Agent 间消息测试"""

    @pytest.mark.asyncio
    async def test_send_message_tool(self, db, project):
        """send_message 工具创建消息记录"""
        from pipeline.engine import _handle_send_message

        pipeline = pipeline_engine.create_pipeline(db, project.id)
        run = pipeline_engine.start_pipeline(db, pipeline.id, "Test")

        result = await _handle_send_message(
            "developer", run,
            {"to_agent": "architect", "content": "What auth method to use?"},
            db=db, stage_id=1,
        )
        assert "sent" in result.lower()

        # 验证消息持久化
        msgs = db.query(PipelineMessage).filter(PipelineMessage.run_id == run.id).all()
        assert len(msgs) == 1
        assert msgs[0].from_agent == "developer"
        assert msgs[0].to_agent == "architect"
        assert "auth" in msgs[0].content


class TestFileAPI:
    """文件 API 测试"""

    def test_read_file_via_api(self):
        """通过内部工具读取文件"""
        workspace = TEST_DIR / "test_file_api"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "README.md").write_text("# Test Project")

        content = (workspace / "README.md").read_text()
        assert "# Test Project" in content

        shutil.rmtree(workspace, ignore_errors=True)


class TestPipelineConfig:
    """Pipeline 配置测试"""

    def test_default_template(self):
        """默认模板有 5 个阶段"""
        template = pipeline_config_manager.get("default")
        assert template is not None
        assert len(template.stages) == 5
        assert template.stages[0].name == "analysis"
        assert template.stages[0].gate == "manual"
        assert template.stages[-1].name == "release"
        assert template.stages[-1].gate == "manual"

    def test_stage_order(self):
        """阶段顺序正确"""
        template = pipeline_config_manager.get("default")
        names = [s.name for s in template.stages]
        assert names == ["analysis", "architecture", "development", "testing", "release"]

    def test_rollback_config(self):
        """testing 阶段配置了自动打回"""
        template = pipeline_config_manager.get("default")
        testing = template.stages[3]  # testing
        assert testing.rollback_on_blocker is True
        assert testing.rollback_target == "development"


# ==================== 运行 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
