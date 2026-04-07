# -*- coding: utf-8 -*-
"""
Pipeline 引擎

核心职责：
- Pipeline 生命周期管理（创建、启动、暂停、恢复、完成）
- 阶段流转（auto/manual gate）
- Agent 调度（LLM 调用 + 工具执行循环）
- 错误恢复（自动重试 + 超时 + 打回）
- Agent 间消息路由
"""
import asyncio
import json
import logging
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from models.database import (
    SessionLocal, Pipeline, PipelineRun, PipelineStage,
    StageArtifact, PipelineMessage, Project,
)
from pipeline.config import pipeline_config_manager, StageConfig
from llm.client import get_llm_client_for_agent, LLMClient

logger = logging.getLogger("catown.pipeline.engine")

# ==================== 工具定义 ====================
# 每个 Agent 可用的工具，由 agents.json 的 tools 字段 + 此处的实现映射决定

AGENT_TOOLS: Dict[str, List[str]] = {}  # 运行时从 agents.json 填充


def _load_agent_tools():
    """从 agents.json 加载每个 Agent 的工具列表"""
    global AGENT_TOOLS
    config_file = os.environ.get("AGENT_CONFIG_FILE", "configs/agents.json")
    if not os.path.exists(config_file):
        return
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, cfg in data.get("agents", {}).items():
            AGENT_TOOLS[name] = cfg.get("tools", [])
    except Exception as e:
        logger.warning(f"Failed to load agent tools: {e}")


_load_agent_tools()

# ==================== 工具执行 ====================

TOOLS_DIR = Path(__file__).parent.parent / "tools"


def _get_workspace(run: PipelineRun) -> Path:
    """获取 pipeline run 的 workspace 目录"""
    ws = Path(run.workspace_path) if run.workspace_path else Path("data") / "workspaces" / f"run_{run.id}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _tool_read_file(workspace: Path, file_path: str) -> str:
    """读取 workspace 内的文件"""
    target = workspace / file_path
    if not target.resolve().is_relative_to(workspace.resolve()):
        return "Error: path traversal detected"
    if not target.exists():
        return f"Error: file not found: {file_path}"
    return target.read_text(encoding="utf-8", errors="replace")


def _tool_write_file(workspace: Path, file_path: str, content: str) -> str:
    """写入 workspace 内的文件"""
    target = workspace / file_path
    if not target.resolve().is_relative_to(workspace.resolve()):
        return "Error: path traversal detected"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {file_path} ({len(content)} chars)"


def _tool_list_files(workspace: Path, dir_path: str = ".") -> str:
    """列出 workspace 内的文件"""
    target = workspace / dir_path
    if not target.exists():
        return f"Error: directory not found: {dir_path}"
    entries = []
    for p in sorted(target.iterdir()):
        rel = p.relative_to(workspace)
        entries.append(f"{'[DIR]' if p.is_dir() else '[FILE]'} {rel}")
    return "\n".join(entries) or "(empty)"


def _tool_execute_code(workspace: Path, code: str, language: str = "python") -> str:
    """执行代码（仅 python）"""
    if language != "python":
        return f"Error: unsupported language: {language}"
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=str(workspace)) as f:
        f.write(code)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workspace),
        )
        output = ""
        if result.stdout:
            output += f"stdout:\n{result.stdout}"
        if result.stderr:
            output += f"\nstderr:\n{result.stderr}"
        output += f"\nexit_code: {result.returncode}"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "Error: execution timed out (30s)"
    except Exception as e:
        return f"Error: {e}"
    finally:
        os.unlink(tmp_path)


def _tool_web_search(workspace: Path, query: str) -> str:
    """网络搜索（占位实现，后续接入真实搜索）"""
    return f"[Web search placeholder] Query: {query}\n(Search not yet implemented in pipeline engine)"


def _tool_send_message_placeholder(workspace: Path, **kwargs) -> str:
    """占位 — 实际由 _execute_tool 特殊处理"""
    return "Error: send_message not configured"


TOOL_REGISTRY: Dict[str, Any] = {
    "read_file": {"fn": _tool_read_file, "params": ["file_path"], "desc": "Read a file from workspace"},
    "write_file": {"fn": _tool_write_file, "params": ["file_path", "content"], "desc": "Write content to a file in workspace"},
    "list_files": {"fn": _tool_list_files, "params": ["dir_path?"], "desc": "List files in workspace directory"},
    "execute_code": {"fn": _tool_execute_code, "params": ["code", "language?"], "desc": "Execute code (python)"},
    "web_search": {"fn": _tool_web_search, "params": ["query"], "desc": "Search the web"},
    "send_message": {"fn": _tool_send_message_placeholder, "params": ["to_agent", "content", "message_type?"], "desc": "Send a message to another agent in this pipeline (e.g. ask architect for clarification)"},
}


def _build_tools_for_agent(agent_name: str) -> List[Dict]:
    """为指定 Agent 构建 OpenAI function calling 工具列表"""
    allowed = AGENT_TOOLS.get(agent_name, list(TOOL_REGISTRY.keys()))
    tools = []
    for tool_name in allowed:
        info = TOOL_REGISTRY.get(tool_name)
        if not info:
            continue
        properties = {}
        required = []
        for p in info["params"]:
            optional = p.endswith("?")
            pname = p.rstrip("?")
            properties[pname] = {"type": "string", "description": pname}
            if not optional:
                required.append(pname)
        tools.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": info["desc"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return tools


async def _execute_tool(agent_name: str, run: PipelineRun, tool_name: str, arguments: Dict[str, str], db=None, stage_id: int = None) -> str:
    """执行单个工具调用"""
    # send_message 需要特殊处理（需要 db 访问）
    if tool_name == "send_message":
        return await _handle_send_message(agent_name, run, arguments, db, stage_id)

    info = TOOL_REGISTRY.get(tool_name)
    if not info:
        return f"Error: unknown tool: {tool_name}"
    workspace = _get_workspace(run)
    try:
        return info["fn"](workspace, **arguments)
    except Exception as e:
        return f"Tool error ({tool_name}): {e}"


async def _handle_send_message(
    from_agent: str,
    run: PipelineRun,
    arguments: Dict[str, str],
    db=None,
    stage_id: int = None,
) -> str:
    """处理 Agent 间消息发送"""
    to_agent = arguments.get("to_agent", "")
    content = arguments.get("content", "")
    message_type = arguments.get("message_type", "AGENT_QUESTION")

    if not to_agent or not content:
        return "Error: send_message requires 'to_agent' and 'content'"

    if db:
        msg = PipelineMessage(
            run_id=run.id,
            stage_id=stage_id,
            message_type=message_type,
            from_agent=from_agent,
            to_agent=to_agent,
            content=content,
        )
        db.add(msg)
        db.commit()

    # 加入待投递队列
    _interagent_message_queue.append({
        "run_id": run.id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "content": content,
        "message_type": message_type,
    })

    # 广播事件
    await event_bus.emit("agent_message", {
        "run_id": run.id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "content": content[:500],
    })

    logger.info(f"Agent message: {from_agent} → {to_agent}: {content[:100]}")
    return f"Message sent to {to_agent}"


# Agent 间消息队列：run_id, from_agent, to_agent, content, message_type
_interagent_message_queue: List[Dict[str, Any]] = []


def _pop_messages_for_agent(run_id: int, agent_name: str) -> List[Dict[str, Any]]:
    """取出并移除发给指定 Agent 的待投递消息"""
    global _interagent_message_queue
    messages = []
    remaining = []
    for msg in _interagent_message_queue:
        if msg["run_id"] == run_id and msg["to_agent"] == agent_name:
            messages.append(msg)
        else:
            remaining.append(msg)
    _interagent_message_queue = remaining
    return messages


# ==================== 事件回调 ====================

class PipelineEventBus:
    """Pipeline 事件总线，用于 WebSocket 广播"""

    def __init__(self):
        self._listeners: List[Callable] = []

    def on(self, callback: Callable):
        self._listeners.append(callback)

    async def emit(self, event_type: str, data: Dict[str, Any]):
        for cb in self._listeners:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event_type, data)
                else:
                    cb(event_type, data)
            except Exception as e:
                logger.error(f"Event listener error: {e}")


event_bus = PipelineEventBus()


# ==================== 引擎 ====================

class PipelineEngine:
    """Pipeline 引擎 — 管理 pipeline 的完整生命周期"""

    def __init__(self):
        # pipeline_id → asyncio.Task，用于跟踪正在运行的 pipeline
        self._running_tasks: Dict[int, asyncio.Task] = {}
        # pipeline_id → "pause" / "rollback:{target}" 指令
        self._pending_commands: Dict[int, str] = {}

    # ---- 生命周期 ----

    def create_pipeline(self, db: Session, project_id: int, pipeline_name: str = "default") -> Pipeline:
        """创建 Pipeline（关联项目）"""
        # 检查项目是否已有 pipeline
        existing = db.query(Pipeline).filter(Pipeline.project_id == project_id).first()
        if existing:
            raise ValueError(f"Project {project_id} already has a pipeline (id={existing.id})")

        template = pipeline_config_manager.get(pipeline_name)
        if not template:
            raise ValueError(f"Pipeline template '{pipeline_name}' not found")

        pipeline = Pipeline(
            project_id=project_id,
            pipeline_name=pipeline_name,
            status="pending",
            current_stage_index=0,
        )
        db.add(pipeline)
        db.commit()
        db.refresh(pipeline)

        logger.info(f"Pipeline created: id={pipeline.id}, project={project_id}, template={pipeline_name}")
        return pipeline

    def start_pipeline(self, db: Session, pipeline_id: int, requirement: str) -> PipelineRun:
        """启动 Pipeline（创建 Run 并异步执行）"""
        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")
        if pipeline.status == "running":
            raise ValueError("Pipeline is already running")

        template = pipeline_config_manager.get(pipeline.pipeline_name)
        if not template:
            raise ValueError(f"Pipeline template '{pipeline.pipeline_name}' not found")

        # 创建 run
        run_number = len(pipeline.runs) + 1
        workspace_path = str(
            Path("data") / "workspaces" / f"project_{pipeline.project_id}" / f"run_{run_number}"
        )
        run = PipelineRun(
            pipeline_id=pipeline_id,
            run_number=run_number,
            status="running",
            input_requirement=requirement,
            workspace_path=workspace_path,
            started_at=datetime.now(),
        )
        db.add(run)
        db.flush()

        # 创建所有阶段记录
        for i, stage_cfg in enumerate(template.stages):
            stage = PipelineStage(
                run_id=run.id,
                stage_name=stage_cfg.name,
                display_name=stage_cfg.display_name,
                stage_order=i,
                agent_name=stage_cfg.agent,
                status="pending" if i > 0 else "pending",  # 引擎启动后设为 running
                gate_type=stage_cfg.gate,
            )
            db.add(stage)

        # 更新 pipeline 状态
        pipeline.status = "running"
        pipeline.current_stage_index = 0
        pipeline.updated_at = datetime.now()

        db.commit()
        db.refresh(run)

        # 异步执行
        task = asyncio.create_task(self._execute_pipeline(run.id))
        self._running_tasks[pipeline_id] = task

        logger.info(f"Pipeline started: pipeline={pipeline_id}, run={run.id}")
        return run

    async def pause(self, db: Session, pipeline_id: int):
        """暂停 Pipeline"""
        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")
        if pipeline.status != "running":
            raise ValueError(f"Cannot pause: status is {pipeline.status}")

        self._pending_commands[pipeline_id] = "pause"
        pipeline.status = "paused"
        pipeline.updated_at = datetime.now()
        db.commit()
        logger.info(f"Pipeline {pipeline_id} paused")

    async def resume(self, db: Session, pipeline_id: int):
        """恢复暂停的 Pipeline"""
        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")
        if pipeline.status != "paused":
            raise ValueError(f"Cannot resume: status is {pipeline.status}")

        pipeline.status = "running"
        pipeline.updated_at = datetime.now()
        db.commit()

        # 获取当前运行中的 run
        run = (
            db.query(PipelineRun)
            .filter(PipelineRun.pipeline_id == pipeline_id)
            .order_by(PipelineRun.run_number.desc())
            .first()
        )
        if run:
            run.status = "running"
            db.commit()
            task = asyncio.create_task(self._execute_pipeline(run.id))
            self._running_tasks[pipeline_id] = task

        logger.info(f"Pipeline {pipeline_id} resumed")

    async def approve(self, db: Session, pipeline_id: int):
        """审批通过当前 Gate"""
        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        run = self._get_active_run(db, pipeline_id)
        if not run:
            raise ValueError("No active run found")

        stage = self._get_current_stage(db, run, pipeline.current_stage_index)
        if not stage:
            raise ValueError("No current stage found")
        if stage.gate_type != "manual":
            raise ValueError(f"Stage '{stage.stage_name}' is not a manual gate")
        if stage.status != "blocked":
            raise ValueError(f"Stage status is '{stage.status}', expected 'blocked'")

        # 批准 → 继续
        stage.status = "completed"
        db.commit()
        logger.info(f"Gate approved: pipeline={pipeline_id}, stage={stage.stage_name}")

        # 恢复执行
        if pipeline.status == "paused":
            pipeline.status = "running"
            run.status = "running"
            db.commit()
            task = asyncio.create_task(self._execute_pipeline(run.id))
            self._running_tasks[pipeline_id] = task

    async def reject(self, db: Session, pipeline_id: int, rollback_to: str = None):
        """拒绝当前 Gate（打回指定阶段重做）"""
        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        run = self._get_active_run(db, pipeline_id)
        stage = self._get_current_stage(db, run, pipeline.current_stage_index)

        if not stage or stage.status != "blocked":
            raise ValueError("No blocked stage to reject")

        template = pipeline_config_manager.get(pipeline.pipeline_name)
        target_name = rollback_to or self._find_previous_stage_name(template, stage.stage_name)

        if not target_name:
            raise ValueError("Cannot determine rollback target")

        # 找到目标阶段的 index
        target_index = None
        for i, s in enumerate(template.stages):
            if s.name == target_name:
                target_index = i
                break

        if target_index is None:
            raise ValueError(f"Rollback target '{target_name}' not found in template")

        # 重置目标及后续阶段
        stages = (
            db.query(PipelineStage)
            .filter(PipelineStage.run_id == run.id)
            .order_by(PipelineStage.stage_order)
            .all()
        )
        for s in stages:
            if s.stage_order >= target_index:
                s.status = "pending"
                s.output_summary = None
                s.error_message = None
                s.started_at = None
                s.completed_at = None

        stage.status = "rejected"

        pipeline.current_stage_index = target_index
        pipeline.status = "running"
        run.status = "running"
        db.commit()

        # 恢复执行
        task = asyncio.create_task(self._execute_pipeline(run.id))
        self._running_tasks[pipeline_id] = task

        logger.info(f"Gate rejected: pipeline={pipeline_id}, rolling back to {target_name}")

    async def instruct(self, db: Session, pipeline_id: int, agent_name: str, message: str):
        """向指定 Agent 发送指令（BOSS 介入）"""
        run = self._get_active_run(db, pipeline_id)
        if not run:
            raise ValueError("No active run")

        # 存为 HUMAN_INSTRUCT 消息
        msg = PipelineMessage(
            run_id=run.id,
            stage_id=None,
            message_type="HUMAN_INSTRUCT",
            from_agent="BOSS",
            to_agent=agent_name,
            content=message,
        )
        db.add(msg)
        db.commit()
        logger.info(f"BOSS instruction sent to {agent_name} in pipeline {pipeline_id}")

    # ---- 核心执行 ----

    async def _execute_pipeline(self, run_id: int):
        """执行整个 Pipeline（在异步任务中运行）"""
        db = SessionLocal()
        try:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if not run:
                logger.error(f"Run {run_id} not found")
                return

            pipeline = db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()
            template = pipeline_config_manager.get(pipeline.pipeline_name)

            await event_bus.emit("pipeline_started", {
                "pipeline_id": pipeline.id,
                "run_id": run.id,
            })

            # 从当前阶段开始执行
            while pipeline.current_stage_index < len(template.stages):
                # 检查暂停
                if pipeline.status == "paused":
                    logger.info(f"Pipeline {pipeline.id} is paused, stopping execution")
                    return

                # 检查中断指令
                cmd = self._pending_commands.pop(pipeline.id, None)
                if cmd == "pause":
                    pipeline.status = "paused"
                    run.status = "paused"
                    db.commit()
                    await event_bus.emit("pipeline_paused", {"pipeline_id": pipeline.id})
                    return

                idx = pipeline.current_stage_index
                stage_cfg = template.stages[idx]

                # 获取 DB stage 记录
                stage = (
                    db.query(PipelineStage)
                    .filter(PipelineStage.run_id == run.id, PipelineStage.stage_order == idx)
                    .first()
                )

                # 执行阶段
                success = await self._execute_stage(db, pipeline, run, stage, stage_cfg, template)

                if not success:
                    # 阶段失败 → pipeline 失败
                    pipeline.status = "failed"
                    run.status = "failed"
                    run.completed_at = datetime.now()
                    db.commit()
                    await event_bus.emit("pipeline_failed", {
                        "pipeline_id": pipeline.id,
                        "run_id": run.id,
                        "failed_stage": stage_cfg.name,
                    })
                    return

                # 检查阶段是否被阻塞在 gate
                stage = db.query(PipelineStage).filter(PipelineStage.id == stage.id).first()
                if stage.status == "blocked":
                    # 等待人工审批，暂停 pipeline
                    pipeline.status = "paused"
                    run.status = "paused"
                    db.commit()
                    await event_bus.emit("pipeline_blocked", {
                        "pipeline_id": pipeline.id,
                        "run_id": run.id,
                        "stage": stage.stage_name,
                    })
                    return

                # 进入下一阶段
                pipeline.current_stage_index += 1
                pipeline.updated_at = datetime.now()
                db.commit()

            # 所有阶段完成
            pipeline.status = "completed"
            run.status = "completed"
            run.completed_at = datetime.now()
            pipeline.updated_at = datetime.now()
            db.commit()

            await event_bus.emit("pipeline_completed", {
                "pipeline_id": pipeline.id,
                "run_id": run.id,
            })
            logger.info(f"Pipeline {pipeline.id} completed!")

        except Exception as e:
            logger.error(f"Pipeline execution error: {e}\n{traceback.format_exc()}")
            try:
                run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                if run:
                    pipeline = db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()
                    if pipeline:
                        pipeline.status = "failed"
                    run.status = "failed"
                    run.completed_at = datetime.now()
                    db.commit()
                    await event_bus.emit("pipeline_failed", {
                        "pipeline_id": pipeline.id,
                        "run_id": run.id,
                        "error": str(e),
                    })
            except Exception:
                pass
        finally:
            db.close()
            # 清理 task 引用
            to_remove = []
            for pid, task in self._running_tasks.items():
                if task.done():
                    to_remove.append(pid)
            for pid in to_remove:
                del self._running_tasks[pid]

    async def _execute_stage(
        self,
        db: Session,
        pipeline: Pipeline,
        run: PipelineRun,
        stage: PipelineStage,
        stage_cfg: StageConfig,
        template,
    ) -> bool:
        """
        执行单个阶段

        Returns: True=成功(或进入gate阻塞), False=失败
        """
        max_retries = 3
        timeout_seconds = stage_cfg.timeout_minutes * 60

        # 构建阶段上下文
        context = self._build_stage_context(db, run, stage_cfg, template)

        stage.status = "running"
        stage.started_at = datetime.now()
        stage.input_context = json.dumps({"context_length": len(context)}, ensure_ascii=False)
        db.commit()

        await event_bus.emit("stage_started", {
            "pipeline_id": pipeline.id,
            "run_id": run.id,
            "stage": stage_cfg.name,
            "display_name": stage_cfg.display_name,
            "agent": stage_cfg.agent,
        })

        for attempt in range(1, max_retries + 1):
            try:
                # 检查超时
                if stage.started_at:
                    elapsed = (datetime.now() - stage.started_at).total_seconds()
                    if elapsed > timeout_seconds:
                        raise TimeoutError(f"Stage timed out after {timeout_seconds}s")

                # 调用 Agent LLM
                summary = await asyncio.wait_for(
                    self._run_agent_stage(db, pipeline, run, stage, stage_cfg, context),
                    timeout=timeout_seconds,
                )

                # 记录产出物
                workspace = _get_workspace(run)
                self._record_artifacts(db, stage, workspace, stage_cfg.expected_artifacts)

                stage.status = "completed"
                stage.output_summary = summary[:2000] if summary else "(no output)"
                stage.completed_at = datetime.now()
                db.commit()

                # Git commit（如果有 git repo）
                self._git_commit(run, stage_cfg.name)

                await event_bus.emit("stage_completed", {
                    "pipeline_id": pipeline.id,
                    "run_id": run.id,
                    "stage": stage_cfg.name,
                    "summary": stage.output_summary,
                })

                # 检查 gate
                if stage_cfg.gate == "manual":
                    stage.status = "blocked"
                    db.commit()
                    logger.info(f"Stage '{stage_cfg.name}' completed, blocked at manual gate")

                return True

            except asyncio.TimeoutError:
                logger.warning(f"Stage '{stage_cfg.name}' timed out (attempt {attempt}/{max_retries})")
                stage.retry_count = attempt
                if attempt >= max_retries:
                    stage.status = "failed"
                    stage.error_message = f"Timeout after {max_retries} attempts"
                    stage.completed_at = datetime.now()
                    db.commit()
                    return False
                db.commit()
                await asyncio.sleep(2 ** attempt)  # 指数退避

            except Exception as e:
                logger.error(f"Stage '{stage_cfg.name}' error (attempt {attempt}/{max_retries}): {e}")
                stage.retry_count = attempt
                if attempt >= max_retries:
                    stage.status = "failed"
                    stage.error_message = str(e)[:1000]
                    stage.completed_at = datetime.now()
                    db.commit()

                    # 检查打回逻辑（testing → development）
                    if stage_cfg.rollback_on_blocker and stage_cfg.rollback_target:
                        logger.info(f"Rolling back to '{stage_cfg.rollback_target}'")
                        return await self._handle_rollback(
                            db, pipeline, run, stage, stage_cfg, template
                        )
                    return False
                db.commit()
                await asyncio.sleep(2 ** attempt)

        return False

    def _build_stage_context(
        self, db: Session, run: PipelineRun, stage_cfg: StageConfig, template
    ) -> str:
        """构建阶段的上下文文本"""
        parts = []
        parts.append(f"# Stage: {stage_cfg.display_name} ({stage_cfg.name})")
        parts.append(f"Agent: {stage_cfg.agent}")
        parts.append(f"Workspace: {run.workspace_path}")
        parts.append("")

        # 原始需求
        if run.input_requirement:
            parts.append("## Original Requirement")
            parts.append(run.input_requirement)
            parts.append("")

        # 前一阶段的产出摘要
        stages = (
            db.query(PipelineStage)
            .filter(PipelineStage.run_id == run.id, PipelineStage.status == "completed")
            .order_by(PipelineStage.stage_order)
            .all()
        )
        for s in stages:
            if s.output_summary:
                parts.append(f"## Output from: {s.stage_name} ({s.display_name})")
                parts.append(s.output_summary)
                parts.append("")

        # 阶段指令
        if stage_cfg.context_prompt:
            parts.append("## Your Task")
            parts.append(stage_cfg.context_prompt)
            parts.append("")

        # 告知产出物位置
        if stage_cfg.expected_artifacts:
            parts.append("## Expected Artifacts")
            for a in stage_cfg.expected_artifacts:
                parts.append(f"- {a}")
            parts.append("")

        return "\n".join(parts)

    async def _run_agent_stage(
        self,
        db: Session,
        pipeline: Pipeline,
        run: PipelineRun,
        stage: PipelineStage,
        stage_cfg: StageConfig,
        context: str,
    ) -> str:
        """
        运行单个 Agent 的 LLM 对话循环

        返回最终输出摘要
        """
        llm_client = get_llm_client_for_agent(stage_cfg.agent)
        tools = _build_tools_for_agent(stage_cfg.agent)

        # 加载 Agent 的 system_prompt
        system_prompt = self._get_agent_system_prompt(stage_cfg.agent)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ]

        # 检查是否有 BOSS 指令
        pending_instructions = self._get_pending_instructions(db, run, stage_cfg.agent)
        for instr in pending_instructions:
            messages.append({"role": "user", "content": f"[BOSS Instruction]: {instr}"})

        max_turns = 20  # 最大对话轮次
        final_content = ""

        for turn in range(max_turns):
            # 检查是否有新的 BOSS 指令
            new_instr = self._get_pending_instructions(db, run, stage_cfg.agent)
            for instr in new_instr:
                messages.append({"role": "user", "content": f"[BOSS Instruction]: {instr}"})

            # 检查是否有来自其他 Agent 的消息
            inter_msgs = _pop_messages_for_agent(run.id, stage_cfg.agent)
            for im in inter_msgs:
                messages.append({
                    "role": "user",
                    "content": f"[Message from {im['from_agent']}]: {im['content']}",
                })

            # 调用 LLM
            response = await llm_client.chat_with_tools(messages, tools=tools if tools else None)

            content = response.get("content") or ""
            tool_calls = response.get("tool_calls")

            if content:
                final_content = content
                messages.append({"role": "assistant", "content": content})

                # 广播 Agent 输出
                await event_bus.emit("agent_output", {
                    "pipeline_id": pipeline.id,
                    "run_id": run.id,
                    "stage": stage_cfg.name,
                    "agent": stage_cfg.agent,
                    "content": content,
                })

                # 持久化消息
                msg = PipelineMessage(
                    run_id=run.id,
                    stage_id=stage.id,
                    message_type="AGENT_OUTPUT",
                    from_agent=stage_cfg.agent,
                    to_agent=None,
                    content=content[:5000],
                )
                db.add(msg)
                db.commit()

            # 处理工具调用
            if tool_calls:
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        fn_args = {}

                    tool_result = await _execute_tool(stage_cfg.agent, run, fn_name, fn_args, db=db, stage_id=stage.id)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": tool_result[:8000],
                    })

                    # 广播工具调用
                    await event_bus.emit("tool_call", {
                        "pipeline_id": pipeline.id,
                        "run_id": run.id,
                        "stage": stage_cfg.name,
                        "agent": stage_cfg.agent,
                        "tool": fn_name,
                        "result_preview": tool_result[:200],
                    })

                continue  # 继续循环让 LLM 处理工具结果

            # 没有工具调用也没有内容 → Agent 已完成
            if not content and not tool_calls:
                break

            # 如果有内容但没有工具调用，认为 Agent 输出完成
            if content and not tool_calls:
                break

        return final_content

    async def _handle_rollback(
        self,
        db: Session,
        pipeline: Pipeline,
        run: PipelineRun,
        failed_stage: PipelineStage,
        stage_cfg: StageConfig,
        template,
    ) -> bool:
        """处理阶段打回"""
        target_name = stage_cfg.rollback_target
        if not target_name:
            return False

        target_index = None
        for i, s in enumerate(template.stages):
            if s.name == target_name:
                target_index = i
                break

        if target_index is None:
            logger.error(f"Rollback target '{target_name}' not found")
            return False

        # 重置目标阶段及后续阶段
        stages = (
            db.query(PipelineStage)
            .filter(PipelineStage.run_id == run.id)
            .order_by(PipelineStage.stage_order)
            .all()
        )
        for s in stages:
            if s.stage_order >= target_index:
                s.status = "pending"
                s.output_summary = None
                s.error_message = None
                s.started_at = None
                s.completed_at = None
                s.retry_count = 0

        # 设置 pipeline 回到目标阶段
        pipeline.current_stage_index = target_index
        db.commit()

        # 持久化打回消息
        msg = PipelineMessage(
            run_id=run.id,
            stage_id=failed_stage.id,
            message_type="STAGE_OUTPUT",
            from_agent=stage_cfg.agent,
            to_agent=target_name,
            content=f"Stage '{stage_cfg.name}' failed, rolling back to '{target_name}'",
        )
        db.add(msg)
        db.commit()

        await event_bus.emit("stage_rollback", {
            "pipeline_id": pipeline.id,
            "run_id": run.id,
            "from_stage": stage_cfg.name,
            "to_stage": target_name,
        })

        # 返回 False 但 _execute_stage 会在 pipeline.current_stage_index 被重置后
        # 从新位置继续。这里需要特殊处理：让外层循环重新从 target_index 开始
        # 实际上我们需要返回 True 并在外层通过 current_stage_index 控制
        return True  # 外层循环会从 current_stage_index 重新开始

    # ---- 辅助方法 ----

    def _get_active_run(self, db: Session, pipeline_id: int) -> Optional[PipelineRun]:
        """获取最新的活跃 run"""
        return (
            db.query(PipelineRun)
            .filter(PipelineRun.pipeline_id == pipeline_id)
            .order_by(PipelineRun.run_number.desc())
            .first()
        )

    def _get_current_stage(
        self, db: Session, run: PipelineRun, index: int
    ) -> Optional[PipelineStage]:
        """获取当前阶段"""
        return (
            db.query(PipelineStage)
            .filter(PipelineStage.run_id == run.id, PipelineStage.stage_order == index)
            .first()
        )

    def _get_agent_system_prompt(self, agent_name: str) -> str:
        """从 agents.json 获取 Agent 的 system_prompt"""
        config_file = os.environ.get("AGENT_CONFIG_FILE", "configs/agents.json")
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            agent_data = data.get("agents", {}).get(agent_name, {})
            return agent_data.get("system_prompt", f"You are {agent_name}, a helpful AI assistant.")
        except Exception:
            return f"You are {agent_name}, a helpful AI assistant."

    def _get_pending_instructions(
        self, db: Session, run: PipelineRun, agent_name: str
    ) -> List[str]:
        """获取待处理的 BOSS 指令"""
        msgs = (
            db.query(PipelineMessage)
            .filter(
                PipelineMessage.run_id == run.id,
                PipelineMessage.message_type == "HUMAN_INSTRUCT",
                PipelineMessage.to_agent == agent_name,
            )
            .order_by(PipelineMessage.created_at)
            .all()
        )
        return [m.content for m in msgs]

    def _record_artifacts(
        self,
        db: Session,
        stage: PipelineStage,
        workspace: Path,
        expected: List[str],
    ):
        """记录阶段产出物"""
        for expected_path in expected:
            full_path = workspace / expected_path
            if full_path.exists():
                artifact_type = "directory" if full_path.is_dir() else "file"
                summary = None
                if artifact_type == "file":
                    try:
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        summary = content[:500]
                    except Exception:
                        pass
                elif artifact_type == "directory":
                    files = list(full_path.rglob("*"))
                    summary = f"{len(files)} files"

                artifact = StageArtifact(
                    stage_id=stage.id,
                    artifact_type=artifact_type,
                    file_path=expected_path,
                    summary=summary,
                )
                db.add(artifact)
        db.commit()

    def _git_commit(self, run: PipelineRun, stage_name: str):
        """尝试 Git commit"""
        workspace = _get_workspace(run)
        git_dir = workspace / ".git"
        if not git_dir.exists():
            return
        try:
            import subprocess
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(workspace),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", f"[pipeline] stage: {stage_name} completed", "--allow-empty"],
                cwd=str(workspace),
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            logger.debug(f"Git commit skipped: {e}")

    def _find_previous_stage_name(self, template, current_name: str) -> Optional[str]:
        """找到前一个阶段名"""
        names = [s.name for s in template.stages]
        try:
            idx = names.index(current_name)
            return names[idx - 1] if idx > 0 else None
        except ValueError:
            return None


# 全局引擎实例
pipeline_engine = PipelineEngine()
