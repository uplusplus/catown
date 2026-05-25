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
import shutil
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from config import settings
from skills import load_skill_registry, write_workspace_skill_packages
from tools.base import build_tool_policy_pack, build_tool_policy_payload, permissions_auto_approve_all_enabled
from models.database import (
    SessionLocal, ApprovalQueueItem, Chatroom, Pipeline, PipelineRun, PipelineStage,
    StageArtifact, PipelineMessage, Project,
)
from models.audit import LLMCall, ToolCall, Event
from pipeline.config import pipeline_config_manager, StageConfig
from llm.client import get_llm_client_for_agent, LLMClient
from services.context_builder import (
    ContextSelector,
    assemble_messages,
    build_base_system_prompt,
    build_operating_developer_context,
    build_runtime_user_fragments,
    build_stage_developer_context,
    build_stage_summaries_fragment,
    build_turn_state_developer_fragments,
    build_turn_state_user_fragments,
    summarize_for_context,
)
from services.chat_prompt_builder import resolve_llm_context_window
from services.model_context import context_window_from_provider
from services.approval_queue import (
    create_approval_queue_item,
    find_pending_queue_item,
    resolve_approval_queue_item,
)
from services.approval_replay import (
    build_approval_queue_item_created_event_payload,
    build_approval_queue_item_resolved_event_payload,
    build_pipeline_gate_request_key,
    build_pipeline_gate_request_payload,
    build_pipeline_gate_resolution_payload,
    load_approval_queue_request_payload,
    parse_replay_arguments,
    build_replay_tool_result_record,
    resolve_pipeline_replay_run_id,
    resolve_pipeline_replay_stage_id,
    resolve_replay_arguments_text,
    resolve_replay_tool_name,
)
from services.artifact_contract_policy import (
    ArtifactContractPolicyDecision,
    ArtifactContractPolicyViolation,
    validate_artifact_contract_for_policy,
)
from services.artifact_history import archive_workspace_artifact_snapshot
from services.artifact_contracts import parse_artifact_contract
from services.artifact_publication import ArtifactPublicationPolicyResult
from services.policy_decision_contracts import (
    build_policy_decision_gate_result,
    dump_policy_decision,
    project_policy_decision,
)
from services.turn_state import (
    TurnContextState,
    build_tool_result_record,
    build_turn_state_from_checkpoint_snapshot,
    normalize_tool_call,
)
from services.run_ledger import (
    append_policy_decision_event_from_result_payload,
    append_task_event,
    build_task_run_checkpoint_snapshot,
    describe_checkpoint_continuation_state,
    complete_task_run,
    create_task_run,
    get_task_run,
    update_task_run,
)
from models.enums import EventType
from services.nonstream_turn_executor import execute_non_stream_turn_loop
from services.runner_policy import (
    compile_pipeline_run_policy,
    compile_pipeline_stage_policy,
    find_stage_policy,
)
from services.runner_lifecycle import (
    complete_agent_turn as record_agent_turn_completed,
    record_tool_round as record_runner_tool_round,
    start_tool_call as record_tool_call_started,
    start_agent_turn as record_agent_turn_started,
)
from services.runtime_event_helpers import build_context_compaction_callback, build_runtime_event_payload
from services.tool_governance import build_blocked_tool_result, build_structured_tool_result, classify_tool_result, tool_manual_approval_reason, tool_result_succeeded
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec
from services.workflow_spec_policy import validate_and_project_workflow_spec_for_execution
from services.pipeline_inbox import (
    consume_legacy_instruction_texts_for_agent,
    enqueue_message_delivery,
    pop_instruction_texts_for_agent,
    pop_messages_for_agent,
)

logger = logging.getLogger("catown.pipeline.engine")
PIPELINE_TASK_RUN_KIND = "pipeline_run"

# ==================== 工具定义 ====================
# 每个 Agent 可用的工具，由 agents.json 的 tools 字段 + 此处的实现映射决定

AGENT_TOOLS: Dict[str, List[str]] = {}  # 运行时从 agents.json 填充


def _load_agent_tools():
    """从 agents.json 加载每个 Agent 的工具列表"""
    global AGENT_TOOLS
    config_file = settings.AGENT_CONFIG_FILE
    if not os.path.exists(config_file):
        return
    try:
        with open(config_file, "r", encoding="utf-8-sig") as f:
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
    ws = Path(run.workspace_path) if run.workspace_path else settings.WORKSPACES_DIR / f"run_{run.id}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _resolve_pipeline_chatroom_id(db: Session, project_id: int | None) -> int | None:
    if not project_id:
        return None
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        return None
    if project.default_chatroom_id:
        return project.default_chatroom_id
    chatroom = (
        db.query(Chatroom)
        .filter(Chatroom.project_id == project.id)
        .order_by(Chatroom.id.asc())
        .first()
    )
    return chatroom.id if chatroom is not None else None


def _ensure_pipeline_task_run(
    db: Session,
    *,
    pipeline: Pipeline,
    run: PipelineRun,
    requirement: str,
    initial_agent_name: str | None = None,
) -> Any | None:
    if getattr(run, "task_run_id", None):
        return get_task_run(db, run.task_run_id)

    chatroom_id = _resolve_pipeline_chatroom_id(db, pipeline.project_id)
    if chatroom_id is None:
        logger.warning(
            "Pipeline run %s has no project chatroom; skipping task-run ledger bridge",
            run.id,
        )
        return None

    title = f"Pipeline #{run.run_number}: {(requirement or '').strip() or pipeline.pipeline_name}"
    task_run = create_task_run(
        db,
        chatroom_id=chatroom_id,
        project_id=pipeline.project_id,
        origin_message_id=None,
        client_turn_id=None,
        run_kind=PIPELINE_TASK_RUN_KIND,
        user_request=requirement or pipeline.pipeline_name,
        initiator="user",
        target_agent_name=initial_agent_name,
        title=title[:160],
    )
    run.task_run_id = task_run.id
    db.add(run)
    db.commit()
    db.refresh(run)
    return task_run


def _pipeline_task_run(db: Session, run: PipelineRun | None) -> Any | None:
    if run is None or not getattr(run, "task_run_id", None):
        return None
    return get_task_run(db, run.task_run_id)


def _append_pipeline_task_event(
    db: Session,
    run: PipelineRun | None,
    event_type: str,
    *,
    agent_name: str | None = None,
    summary: str = "",
    payload: Any = None,
    target_agent_name: str | None = None,
    run_summary: str | None = None,
) -> Any | None:
    task_run = _pipeline_task_run(db, run)
    if task_run is None:
        return None
    if target_agent_name is not None or run_summary is not None:
        update_task_run(
            db,
            task_run,
            target_agent_name=target_agent_name if target_agent_name is not None else task_run.target_agent_name,
            summary=run_summary if run_summary is not None else task_run.summary,
        )
    return append_task_event(
        db,
        task_run,
        event_type,
        agent_name=agent_name,
        summary=summary,
        payload=payload,
    )


def _build_pipeline_context_compaction_callback(
    db: Session,
    run: PipelineRun | None,
    *,
    agent_name: str,
    extra_payload: Optional[Dict[str, Any]] = None,
):
    if run is None:
        return lambda diagnostics: None

    payload = {"pipeline_run_id": run.id}
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)

    return build_context_compaction_callback(
        emit_event=lambda event_type, summary, event_payload: _append_pipeline_task_event(
            db,
            run,
            event_type,
            agent_name=agent_name,
            summary=summary,
            payload=event_payload,
            target_agent_name=agent_name,
        ),
        agent_name=agent_name,
        extra_payload=payload,
        summary_noun="pipeline context",
    )


def _pipeline_runner_policy(
    pipeline: Pipeline,
    template: Any | None,
):
    stages = list(getattr(template, "stages", []) or [])
    stage_tool_packs = {
        _clean_stage_name(stage_cfg): _pipeline_stage_tool_policy_pack(getattr(stage_cfg, "agent", None))
        for stage_cfg in stages
    }
    return compile_pipeline_run_policy(
        pipeline_name=getattr(pipeline, "pipeline_name", None),
        project_id=getattr(pipeline, "project_id", None),
        stages=stages,
        stage_tool_packs=stage_tool_packs,
    )


def _pipeline_workflow_policy_result(pipeline: Pipeline, template: Any | None) -> Any | None:
    if template is None:
        return None
    if hasattr(template, "model_dump"):
        template_payload = template.model_dump(mode="json")
    elif isinstance(template, dict):
        template_payload = dict(template)
    else:
        return None
    workflow_spec = compile_pipeline_template_to_workflow_spec(
        getattr(pipeline, "pipeline_name", None) or "default",
        template_payload,
    )
    return validate_and_project_workflow_spec_for_execution(workflow_spec)


def _stage_artifact_contract(
    *,
    artifact: StageArtifact,
    stage: PipelineStage,
    stage_policy: Any,
    run: PipelineRun,
) -> Any:
    artifact_path = str(getattr(artifact, "file_path", "") or "").strip()
    artifact_kind = str(getattr(artifact, "artifact_type", "") or "").strip().lower()
    common_payload = {
        "kind": "artifact_contract",
        "version": 1,
        "artifact_id": f"stage-artifact-{artifact.id}",
        "artifact_type": f"workspace.{artifact_kind or 'file'}",
        "title": artifact_path or f"Stage artifact {artifact.id}",
        "summary": getattr(artifact, "summary", None),
        "producer": {
            "agent_name": getattr(stage_policy, "agent_name", None),
            "agent_type": getattr(stage_policy, "agent_name", None),
            "stage_name": getattr(stage_policy, "stage_name", None) or getattr(stage, "stage_name", None),
            "task_run_id": getattr(run, "task_run_id", None),
            "pipeline_run_id": getattr(run, "id", None),
            "pipeline_stage_id": getattr(stage, "id", None),
        },
        "source_input_refs": [f"pipeline_stage:{getattr(stage, 'id', '')}"],
        "metadata": {
            "pipeline_run_id": getattr(run, "id", None),
            "pipeline_stage_id": getattr(stage, "id", None),
            "stage_name": getattr(stage_policy, "stage_name", None),
        },
    }
    if artifact_kind == "directory":
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "workspace_directory",
                "directory_path": artifact_path.rstrip("/") + "/",
            }
        )
    return parse_artifact_contract(
        {
            **common_payload,
            "mode": "workspace_file",
            "file_path": artifact_path,
            "media_type": None,
        }
    )


def _record_stage_artifact_policy_decisions(
    db: Session,
    *,
    run: PipelineRun,
    stage: PipelineStage,
    stage_policy: Any,
    runner_policy: Any,
    artifacts: list[StageArtifact],
) -> list[dict[str, Any]]:
    task_run = _pipeline_task_run(db, run)
    if task_run is None:
        return []
    gate_results: list[dict[str, Any]] = []
    for artifact in artifacts:
        contract = _stage_artifact_contract(
            artifact=artifact,
            stage=stage,
            stage_policy=stage_policy,
            run=run,
        )
        decision = validate_artifact_contract_for_policy(
            contract=contract,
            policy=runner_policy,
            stage_name=getattr(stage_policy, "stage_name", None),
        )
        result = ArtifactPublicationPolicyResult(contract=contract, decision=decision)
        result_payload = result.to_payload()
        append_policy_decision_event_from_result_payload(
            db,
            task_run,
            result_payload,
            agent_name=getattr(stage_policy, "agent_name", None),
        )
        gate_result = result_payload.get("policy_decision_gate_result")
        if isinstance(gate_result, dict):
            gate_results.append(gate_result)
    gate_results.extend(_record_missing_stage_artifact_policy_decisions(
        db,
        task_run=task_run,
        stage_policy=stage_policy,
        runner_policy=runner_policy,
        expected_artifacts=list(getattr(getattr(stage_policy, "delivery", None), "expected_artifacts", []) or []),
        recorded_artifacts=artifacts,
    ))
    return gate_results


def _record_missing_stage_artifact_policy_decisions(
    db: Session,
    *,
    task_run: Any,
    stage_policy: Any,
    runner_policy: Any,
    expected_artifacts: list[str],
    recorded_artifacts: list[StageArtifact],
) -> list[dict[str, Any]]:
    gate_results: list[dict[str, Any]] = []
    recorded_paths = {
        _normalize_artifact_path(getattr(artifact, "file_path", None))
        for artifact in recorded_artifacts
    }
    for index, expected_artifact in enumerate(expected_artifacts):
        expected_path = _normalize_artifact_path(expected_artifact)
        if not expected_path or expected_path in recorded_paths:
            continue
        artifact_type = "workspace.directory" if expected_path.endswith("/") else "workspace.file"
        decision = ArtifactContractPolicyDecision(
            artifact_id=f"missing-stage-artifact-{getattr(stage_policy, 'stage_name', 'stage')}-{index}",
            artifact_type=artifact_type,
            accepted=False,
            stage_name=getattr(stage_policy, "stage_name", None),
            violations=[
                ArtifactContractPolicyViolation(
                    code="artifact_missing",
                    field="file_path",
                    message=(
                        f"Expected artifact '{expected_path}' was not found for "
                        f"stage '{getattr(stage_policy, 'stage_name', 'stage')}'."
                    ),
                )
            ],
            metadata={
                "artifact_path": expected_path,
                "policy_source": getattr(runner_policy, "source", None),
                "pipeline_name": getattr(runner_policy, "pipeline_name", None),
                "stage_count": getattr(runner_policy, "stage_count", None),
            },
        )
        policy_decision = project_policy_decision(decision)
        gate_result = build_policy_decision_gate_result(policy_decision)
        append_policy_decision_event_from_result_payload(
            db,
            task_run,
            {
                "policy_decision": dump_policy_decision(policy_decision),
                "policy_decision_gate_result": gate_result,
            },
            agent_name=getattr(stage_policy, "agent_name", None),
        )
        gate_results.append(gate_result)
    return gate_results


def _normalize_artifact_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _queue_pipeline_gate_approval(
    db: Session,
    *,
    pipeline: Pipeline,
    run: PipelineRun,
    stage: PipelineStage,
    stage_policy: Any | None,
) -> Any | None:
    task_run = _pipeline_task_run(db, run)
    if task_run is None:
        return None
    return create_approval_queue_item(
        db,
        task_run=task_run,
        chatroom_id=task_run.chatroom_id,
        project_id=task_run.project_id,
        queue_kind="approval",
        source="pipeline_gate",
        title=f"Approve pipeline gate: {stage.display_name}",
        summary=f"Pipeline is waiting for approval at {stage.display_name}.",
        agent_name=stage.agent_name,
        target_kind="pipeline_gate",
        target_name=stage.stage_name,
        request_key=build_pipeline_gate_request_key(pipeline_run_id=run.id, stage_name=stage.stage_name),
        request_payload=build_pipeline_gate_request_payload(
            pipeline_id=pipeline.id,
            pipeline_run_id=run.id,
            pipeline_stage_id=stage.id,
            stage_name=stage.stage_name,
            display_name=stage.display_name,
            stage_policy=stage_policy,
        ),
        pipeline_run_id=run.id,
        pipeline_stage_id=stage.id,
    )


def _resolve_pipeline_gate_queue_item(
    db: Session,
    *,
    run: PipelineRun,
    stage: PipelineStage,
    status: str,
    resolution_note: str,
) -> Any | None:
    queue_item = find_pending_queue_item(
        db,
        request_key=build_pipeline_gate_request_key(pipeline_run_id=run.id, stage_name=stage.stage_name),
        pipeline_run_id=run.id,
        pipeline_stage_id=stage.id,
        target_kind="pipeline_gate",
        target_name=stage.stage_name,
    )
    return resolve_approval_queue_item(
        db,
        queue_item,
        status=status,
        resolved_by="user",
        resolution_note=resolution_note,
        resolution_payload=build_pipeline_gate_resolution_payload(
            pipeline_run_id=run.id,
            pipeline_stage_id=stage.id,
            stage_name=stage.stage_name,
            display_name=stage.display_name,
        ),
    )


def _find_latest_pending_pipeline_tool_queue_item(
    db: Session,
    *,
    run: PipelineRun,
    stage: PipelineStage,
) -> Any | None:
    return (
        db.query(ApprovalQueueItem)
        .filter(
            ApprovalQueueItem.pipeline_run_id == run.id,
            ApprovalQueueItem.pipeline_stage_id == stage.id,
            ApprovalQueueItem.target_kind == "tool",
            ApprovalQueueItem.status == "pending",
        )
        .order_by(ApprovalQueueItem.id.desc())
        .first()
    )


def _clean_stage_name(stage_cfg: Any) -> str:
    return str(getattr(stage_cfg, "name", "") or "").strip()


def _pipeline_stage_tool_policy_pack(agent_name: str | None) -> dict[str, Any]:
    normalized_agent_name = str(agent_name or "").strip()
    tool_names = AGENT_TOOLS.get(normalized_agent_name, list(TOOL_REGISTRY.keys()))
    return build_tool_policy_pack(
        tool_names,
        description_map={
            tool_name: str((TOOL_REGISTRY.get(tool_name) or {}).get("desc", "") or "")
            for tool_name in tool_names
        },
    )


def _validate_path(workspace: Path, target: Path, allow_catown: bool = False) -> Optional[str]:
    """
    统一路径校验：symlink 解析 + 目录穿越检测 + .catown 保护。
    返回错误消息字符串，None 表示校验通过。
    """
    try:
        real_target = target.resolve()
        real_workspace = workspace.resolve()
    except (OSError, RuntimeError) as e:
        return f"Error: path resolution failed: {e}"

    # 检查是否在 workspace 内（处理 symlink 攻击）
    try:
        real_target.relative_to(real_workspace)
    except ValueError:
        return "Error: path traversal detected (access outside workspace)"

    # 检查是否试图访问 .catown 目录（项目元数据，Agent 不可读写）
    if not allow_catown:
        try:
            rel = real_target.relative_to(real_workspace)
            if str(rel) == ".catown" or str(rel).startswith(".catown" + os.sep):
                return "Error: access to .catown/ directory is restricted (project metadata)"
        except ValueError:
            pass

    return None


def _tool_read_file(workspace: Path, file_path: str) -> str:
    """读取 workspace 内的文件"""
    target = workspace / file_path
    err = _validate_path(workspace, target)
    if err:
        return err
    if not target.exists():
        return f"Error: file not found: {file_path}"
    if not target.is_file():
        return f"Error: not a file: {file_path}"
    return target.read_text(encoding="utf-8", errors="replace")


def _tool_write_file(workspace: Path, file_path: str, content: str) -> str:
    """写入 workspace 内的文件"""
    target = workspace / file_path
    # 校验目标路径
    err = _validate_path(workspace, target)
    if err:
        return err
    # 校验父目录路径（防止 mkdir 创建非法目录）
    parent_err = _validate_path(workspace, target.parent)
    if parent_err:
        return parent_err
    target.parent.mkdir(parents=True, exist_ok=True)
    archive_workspace_artifact_snapshot(workspace, file_path, next_content=content)
    target.write_text(content, encoding="utf-8")
    return f"Written: {file_path} ({len(content)} chars)"


def _tool_list_files(workspace: Path, dir_path: str = ".") -> str:
    """列出 workspace 内的文件"""
    target = workspace / dir_path
    err = _validate_path(workspace, target)
    if err:
        return err
    if not target.exists():
        return f"Error: directory not found: {dir_path}"
    if not target.is_dir():
        return f"Error: not a directory: {dir_path}"
    entries = []
    for p in sorted(target.iterdir()):
        # 跳过 .catown 目录
        try:
            rel = p.relative_to(workspace.resolve())
            if str(rel) == ".catown" or str(rel).startswith(".catown" + os.sep):
                continue
        except ValueError:
            pass
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
        # 验证临时文件在 workspace 内
        err = _validate_path(workspace, Path(tmp_path))
        if err:
            os.unlink(tmp_path)
            return err
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
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _tool_run_shell(workspace: Path, command: str, cwd: str = ".", timeout_seconds: int = 20) -> str:
    """运行真实 shell 命令（限定在 workspace 内）"""
    normalized_command = str(command or "").strip()
    if not normalized_command:
        return "Error: command is required"

    requested = Path(str(cwd or ".").strip() or ".")
    target_cwd = (requested if requested.is_absolute() else (workspace / requested)).resolve()
    err = _validate_path(workspace, target_cwd)
    if err:
        return err
    if not target_cwd.exists():
        return f"Error: directory not found: {cwd}"
    if not target_cwd.is_dir():
        return f"Error: not a directory: {cwd}"

    timeout = max(1, min(int(timeout_seconds or 20), 60))
    if os.name == "nt":
        shell_cmd = [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c", normalized_command]
    else:
        shell_bin = os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh")
        if not shell_bin:
            return "Error: no supported shell found"
        shell_cmd = [shell_bin, "-lc", normalized_command]

    try:
        result = subprocess.run(
            shell_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(target_cwd),
            env={**os.environ, "TERM": "dumb"},
        )
    except subprocess.TimeoutExpired:
        return f"Error: execution timed out ({timeout}s)"
    except Exception as e:
        return f"Error: {e}"

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    output = "\n".join(part for part in [stdout, stderr] if part).strip()[:50000]
    if result.returncode == 0:
        return output or "(no output)"
    if not output:
        output = f"Command exited with status {result.returncode}."
    return f"exit_code: {result.returncode}\n{output}"


def _tool_web_search(workspace: Path, query: str) -> str:
    """网络搜索（DuckDuckGo Instant Answer API）"""
    import urllib.request
    import urllib.parse

    try:
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1
        })

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        results = []

        if data.get("Abstract"):
            results.append(f"**Summary**: {data['Abstract']}")
            if data.get("AbstractURL"):
                results.append(f"Source: {data['AbstractURL']}")

        if data.get("RelatedTopics"):
            results.append("\n**Related Topics**:")
            for i, topic in enumerate(data["RelatedTopics"][:5]):
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(f"  {i+1}. {topic['Text'][:200]}")

        if data.get("Answer"):
            results.append(f"\n**Answer**: {data['Answer']}")

        if results:
            return "\n".join(results)
        else:
            return f"[Web Search] No instant answer found for '{query}'. Try a more specific query."

    except Exception as e:
        return f"[Web Search] Error: {str(e)}"


def _tool_send_message_placeholder(workspace: Path, **kwargs) -> str:
    """Fallback — 实际调用由 _execute_tool 路由到 _handle_send_message"""
    return "Error: send_message should be handled by _handle_send_message"


TOOL_REGISTRY: Dict[str, Any] = {
    "read_file": {"fn": _tool_read_file, "params": ["file_path"], "desc": "Read a file from workspace"},
    "write_file": {"fn": _tool_write_file, "params": ["file_path", "content"], "desc": "Write content to a file in workspace"},
    "list_files": {"fn": _tool_list_files, "params": ["dir_path?"], "desc": "List files in workspace directory"},
    "execute_code": {"fn": _tool_execute_code, "params": ["code", "language?"], "desc": "Execute code (python)"},
    "run_shell": {"fn": _tool_run_shell, "params": ["command", "cwd?", "timeout_seconds?"], "desc": "Run a shell command in the workspace"},
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


async def _execute_tool(agent_name: str, run: PipelineRun, tool_name: str, arguments: Dict[str, str], db=None, stage_id: int = None) -> Any:
    """执行单个工具调用"""

    # === 白名单校验：Agent 仅能调用 agents.json 中声明的工具 ===
    # None = Agent 未在 agents.json 中配置（允许全部，向后兼容）
    # []   = Agent 已配置但无任何工具（拒绝全部）
    # [...] = Agent 的工具白名单
    if agent_name in AGENT_TOOLS:
        allowed_tools = AGENT_TOOLS[agent_name]
        if tool_name not in allowed_tools:
            logger.warning(f"Agent '{agent_name}' attempted unauthorized tool: '{tool_name}' (allowed: {allowed_tools})")
            result_text = build_blocked_tool_result(
                "approval_blocked",
                tool_name,
                f"Agent '{agent_name}' is not authorized to use tool '{tool_name}'. Allowed tools: {allowed_tools}",
            )
            return build_structured_tool_result(
                tool_name=tool_name,
                result_text=result_text,
                success=False,
                status="approval_blocked",
                blocked=True,
                blocked_kind="approval",
                blocked_reason=result_text,
            )

    # send_message 需要特殊处理（需要 db 访问）
    if tool_name == "send_message":
        return await _handle_send_message(agent_name, run, arguments, db, stage_id)

    info = TOOL_REGISTRY.get(tool_name)
    if not info:
        return f"Error: unknown tool: {tool_name}"
    tool_policy = build_tool_policy_payload(tool_name, description=str(info.get("desc") or ""))
    approval_reason = tool_manual_approval_reason(
        tool_policy,
        tool_name=tool_name,
        arguments=arguments,
    )
    if approval_reason:
        reason = approval_reason
        result_text = build_blocked_tool_result("approval_blocked", tool_name, reason)
        return build_structured_tool_result(
            tool_name=tool_name,
            result_text=result_text,
            success=False,
            status="approval_blocked",
            blocked=True,
            blocked_kind="approval",
            blocked_reason=result_text,
        )
    workspace = _get_workspace(run)
    try:
        result = info["fn"](workspace, **arguments)
        if isinstance(result, dict) and result.get("__catown_tool_result__") is True:
            return result
        classification = classify_tool_result(tool_name, result)
        return build_structured_tool_result(
            tool_name=tool_name,
            result_text=result,
            success=bool(classification.get("success")),
            status=str(classification.get("status") or "succeeded"),
            blocked=bool(classification.get("blocked")),
            blocked_kind=classification.get("blocked_kind"),
            blocked_reason=classification.get("blocked_reason"),
        )
    except Exception as e:
        return build_structured_tool_result(
            tool_name=tool_name,
            result_text=f"Tool error ({tool_name}): {e}",
            success=False,
            status="failed",
        )


async def replay_blocked_tool_queue_item(db: Session, queue_item: Any):
    """Replay a previously blocked pipeline tool call after approval."""
    request_payload = load_approval_queue_request_payload(getattr(queue_item, "request_payload_json", None))

    tool_name = resolve_replay_tool_name(queue_item, request_payload)
    arguments_text = resolve_replay_arguments_text(request_payload)
    if not tool_name:
        return build_replay_tool_result_record(
            queue_item,
            tool_name=getattr(queue_item, "target_name", "tool"),
            arguments=arguments_text,
            result="Error: blocked pipeline tool replay is missing tool_name.",
            success=False,
            request_payload=request_payload,
        )

    run_id = resolve_pipeline_replay_run_id(queue_item, request_payload)
    if not run_id:
        return build_replay_tool_result_record(
            queue_item,
            tool_name=tool_name,
            arguments=arguments_text,
            result="Error: blocked pipeline tool replay is missing pipeline_run_id.",
            success=False,
            request_payload=request_payload,
        )

    run = db.query(PipelineRun).filter(PipelineRun.id == int(run_id)).first()
    if run is None:
        return build_replay_tool_result_record(
            queue_item,
            tool_name=tool_name,
            arguments=arguments_text,
            result=f"Error: pipeline run not found for blocked tool replay ({run_id}).",
            success=False,
            request_payload=request_payload,
        )

    loaded_arguments, arguments_error = parse_replay_arguments(arguments_text)
    if arguments_error is not None:
        return build_replay_tool_result_record(
            queue_item,
            tool_name=tool_name,
            arguments=arguments_text,
            result=f"Error: invalid blocked pipeline tool replay arguments: {arguments_error}",
            success=False,
            request_payload=request_payload,
        )

    tool_result = await _execute_tool(
        str(getattr(queue_item, "agent_name", "") or ""),
        run,
        tool_name,
        loaded_arguments,
        db=db,
        stage_id=resolve_pipeline_replay_stage_id(queue_item, request_payload),
    )
    return build_replay_tool_result_record(
        queue_item,
        tool_name=tool_name,
        arguments=arguments_text,
        result=tool_result,
        success=tool_result_succeeded(tool_result),
        request_payload=request_payload,
    )


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
        db.flush()
        enqueue_message_delivery(db, msg)
        db.commit()

    # 广播事件
    await event_bus.emit("agent_message", {
        "run_id": run.id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "content": content[:2000],
        "message_type": message_type,
    })

    logger.info(f"Agent message: {from_agent} → {to_agent}: {content[:100]}")
    return f"Message sent to {to_agent}"


def _pop_messages_for_agent(db: Session, run_id: int, agent_name: str) -> List[Dict[str, Any]]:
    return pop_messages_for_agent(
        db,
        run_id=run_id,
        agent_name=agent_name,
        exclude_message_type="HUMAN_INSTRUCT",
    )


def _pop_instructions_for_agent(db: Session, run_id: int, agent_name: str) -> List[str]:
    return pop_instruction_texts_for_agent(db, run_id=run_id, agent_name=agent_name)


def _enqueue_message_delivery(db: Session, msg: PipelineMessage) -> None:
    enqueue_message_delivery(db, msg)


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


def _serialize_tool_call_preview(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": tool_call.get("id"),
        "function": tool_call.get("function"),
    }


# ==================== 引擎 ====================


def _format_stage_output_summary(
    stage_name: str,
    display_name: str,
    agent_name: str,
    raw_content: str,
    artifacts: list[str] | None = None,
    max_chars: int = 800,
) -> str:
    """Format a stage's LLM output into a compact structured summary for downstream agents.

    ADR-028 Phase 4: Cross-stage summary compression.
    """
    content = (raw_content or "").strip()
    if not content:
        return f"({display_name}: no output)"

    # Truncate content
    if len(content) > max_chars:
        content = content[:max_chars] + "..."

    parts = [f"Agent: {agent_name}", content]
    if artifacts:
        parts.append("Artifacts: " + ", ".join(artifacts))
    return "\n".join(parts)


def _build_completed_stage_summaries_fragment(
    db: Session,
    run: PipelineRun,
    current_stage_cfg: Any,
) -> Any:
    """Build a fragment with summaries of completed stages upstream of current stage.

    ADR-028 Phase 4: Cross-stage summary compression.
    """
    current_order = getattr(current_stage_cfg, "order", None) or getattr(current_stage_cfg, "stage_order", 0)
    completed_stages = (
        db.query(PipelineStage)
        .filter(
            PipelineStage.run_id == run.id,
            PipelineStage.status == "completed",
            PipelineStage.stage_order < current_order,
        )
        .order_by(PipelineStage.stage_order)
        .all()
    )
    if not completed_stages:
        return None
    return build_stage_summaries_fragment(completed_stages)


class PipelineEngine:
    """Pipeline 引擎 — 管理 pipeline 的完整生命周期，支持多项目并行"""

    def __init__(self, max_concurrent: int = 3):
        # pipeline_id → asyncio.Task，用于跟踪正在运行的 pipeline
        self._running_tasks: Dict[int, asyncio.Task] = {}
        # pipeline_id → "pause" / "rollback:{target}" 指令
        self._pending_commands: Dict[int, str] = {}
        # 并发控制
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent

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
        runner_policy = _pipeline_runner_policy(pipeline, template)

        # 创建 run
        run_number = len(pipeline.runs) + 1
        workspace_path = str(
            settings.WORKSPACES_DIR / f"project_{pipeline.project_id}" / f"run_{run_number}"
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
        for stage_policy in runner_policy.stages:
            stage = PipelineStage(
                run_id=run.id,
                stage_name=stage_policy.stage_name,
                display_name=stage_policy.display_name,
                stage_order=stage_policy.stage_order,
                agent_name=stage_policy.agent_name,
                status="pending",
                gate_type=stage_policy.approval.kind,
            )
            db.add(stage)

        # 更新 pipeline 状态
        pipeline.status = "running"
        pipeline.current_stage_index = 0
        pipeline.updated_at = datetime.now()

        db.commit()
        db.refresh(run)

        initial_agent_name = runner_policy.stages[0].agent_name if runner_policy.stages else None
        task_run = _ensure_pipeline_task_run(
            db,
            pipeline=pipeline,
            run=run,
            requirement=requirement,
            initial_agent_name=initial_agent_name,
        )
        if task_run is not None:
            _append_pipeline_task_event(
                db,
                run,
                "pipeline_run_started",
                summary="Pipeline run started.",
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_name": pipeline.pipeline_name,
                    "pipeline_run_id": run.id,
                    "run_number": run.run_number,
                    "status": run.status,
                    "project_id": pipeline.project_id,
                    "workspace_path": run.workspace_path,
                    "stage_names": [stage.display_name for stage in template.stages],
                    "stage_count": len(template.stages),
                    "runner_policy": runner_policy.to_payload(),
                },
                target_agent_name=initial_agent_name,
            )
            workflow_policy_result = _pipeline_workflow_policy_result(pipeline, template)
            if workflow_policy_result is not None:
                append_policy_decision_event_from_result_payload(
                    db,
                    task_run,
                    workflow_policy_result.to_payload(),
                    agent_name=initial_agent_name,
                )

        # 初始化 Git
        self._git_init(run)

        # 异步执行（仅在有事件循环时创建 task）
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._execute_pipeline(run.id))
            self._running_tasks[pipeline_id] = task
        except RuntimeError:
            # 无事件循环（如同步测试中），跳过异步执行
            pass

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
        run = self._get_active_run(db, pipeline_id)
        if run is not None:
            _append_pipeline_task_event(
                db,
                run,
                "pipeline_pause_requested",
                summary="Pipeline pause requested.",
                payload={"pipeline_id": pipeline_id, "status": pipeline.status},
            )
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
            task_run = _pipeline_task_run(db, run)
            checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
            continuation_state = describe_checkpoint_continuation_state(checkpoint_snapshot)
            _append_pipeline_task_event(
                db,
                run,
                "pipeline_resumed",
                summary="Pipeline resumed.",
                payload={
                    "pipeline_id": pipeline_id,
                    "pipeline_run_id": run.id,
                    "checkpoint_snapshot": checkpoint_snapshot,
                    "continuation_state": continuation_state,
                },
            )
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
        template = pipeline_config_manager.get(pipeline.pipeline_name)
        runner_policy = _pipeline_runner_policy(pipeline, template) if template else None
        stage_policy = find_stage_policy(runner_policy, stage.stage_name)
        _append_pipeline_task_event(
            db,
            run,
            "pipeline_gate_approved",
            summary=f"Pipeline gate approved for {stage.display_name}.",
            payload={
                "pipeline_id": pipeline_id,
                "pipeline_run_id": run.id,
                "stage_name": stage.stage_name,
                "display_name": stage.display_name,
                "stage_policy": stage_policy.to_payload() if stage_policy is not None else None,
            },
        )
        queue_item = _resolve_pipeline_gate_queue_item(
            db,
            run=run,
            stage=stage,
            status="approved",
            resolution_note=f"Approved pipeline gate {stage.display_name}.",
        )
        if queue_item is not None:
            _append_pipeline_task_event(
                db,
                run,
                "approval_queue_item_resolved",
                summary=f"Resolved approval item for {stage.display_name}.",
                payload=build_approval_queue_item_resolved_event_payload(
                    queue_item,
                    status=queue_item.status,
                ),
            )
        logger.info(f"Gate approved: pipeline={pipeline_id}, stage={stage.stage_name}")

        # 写入 gate 审批事件
        db.add(Event(
            run_id=run.id,
            event_type=EventType.GATE_APPROVED,
            agent_name=None,
            stage_name=stage.stage_name,
            summary=f"Gate approved: {stage.display_name} by BOSS",
            payload=json.dumps({"stage": stage.stage_name, "display_name": stage.display_name}, ensure_ascii=False),
        ))
        db.commit()

        await event_bus.emit("gate_approved", {
            "pipeline_id": pipeline.id,
            "run_id": run.id,
            "stage": stage.stage_name,
        })

        # Release 阶段审批通过 → 打 Git tag
        if template:
            stage_cfg = None
            for sc in template.stages:
                if sc.name == stage.stage_name:
                    stage_cfg = sc
                    break
            # 如果是最后一个阶段（release），打 tag
            if stage_cfg and stage.stage_order == len(template.stages) - 1:
                version = f"v1.{run.run_number}.0"
                self._git_tag(run, version, f"Pipeline completed - {stage.display_name}")

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
        runner_policy = _pipeline_runner_policy(pipeline, template) if template else None
        stage_policy = find_stage_policy(runner_policy, stage.stage_name)
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
        _append_pipeline_task_event(
            db,
            run,
            "pipeline_gate_rejected",
            summary=f"Pipeline gate rejected; rolling back to {target_name}.",
            payload={
                "pipeline_id": pipeline_id,
                "pipeline_run_id": run.id,
                "rejected_stage": stage.stage_name,
                "rollback_target": target_name,
                "stage_policy": stage_policy.to_payload() if stage_policy is not None else None,
            },
            target_agent_name=template.stages[target_index].agent if template and target_index < len(template.stages) else None,
        )
        queue_item = _resolve_pipeline_gate_queue_item(
            db,
            run=run,
            stage=stage,
            status="rejected",
            resolution_note=f"Rejected pipeline gate {stage.display_name}; rollback to {target_name}.",
        )
        if queue_item is not None:
            _append_pipeline_task_event(
                db,
                run,
                "approval_queue_item_resolved",
                summary=f"Resolved approval item for {stage.display_name}.",
                payload=build_approval_queue_item_resolved_event_payload(
                    queue_item,
                    status=queue_item.status,
                ),
            )

        # 恢复执行
        task = asyncio.create_task(self._execute_pipeline(run.id))
        self._running_tasks[pipeline_id] = task

        # 写入 gate 拒绝 + 打回事件
        db.add(Event(
            run_id=run.id,
            event_type=EventType.GATE_REJECTED,
            agent_name=None,
            stage_name=stage.stage_name,
            summary=f"Gate rejected: {stage.display_name} → rolling back to {target_name}",
            payload=json.dumps({
                "rejected_stage": stage.stage_name,
                "rollback_target": target_name,
            }, ensure_ascii=False),
        ))
        db.commit()

        await event_bus.emit("gate_rejected", {
            "pipeline_id": pipeline.id,
            "run_id": run.id,
            "from_stage": stage.stage_name,
            "to_stage": target_name,
        })

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
        db.flush()
        enqueue_message_delivery(db, msg)

        # 写入 BOSS 指令事件
        db.add(Event(
            run_id=run.id,
            event_type=EventType.BOSS_INSTRUCTION,
            agent_name=agent_name,
            stage_name=None,
            summary=f"BOSS → {agent_name}: {message[:100]}",
            payload=json.dumps({"to_agent": agent_name, "content": message[:5000]}, ensure_ascii=False),
        ))

        db.commit()
        _append_pipeline_task_event(
            db,
            run,
            "pipeline_boss_instruction",
            agent_name=agent_name,
            summary=f"BOSS instructed {agent_name}.",
            payload={
                "pipeline_id": pipeline_id,
                "pipeline_run_id": run.id,
                "to_agent": agent_name,
                "content_preview": message[:500],
            },
            target_agent_name=agent_name,
        )
        logger.info(f"BOSS instruction sent to {agent_name} in pipeline {pipeline_id}")

        await event_bus.emit("boss_instruction", {
            "pipeline_id": pipeline_id,
            "run_id": run.id,
            "agent": agent_name,
            "content_preview": message[:200],
        })

    # ---- 核心执行 ----

    async def _execute_pipeline(self, run_id: int):
        """执行整个 Pipeline（在异步任务中运行，受并发信号量控制）"""
        async with self._semaphore:
            await self._do_execute_pipeline(run_id)

    async def _do_execute_pipeline(self, run_id: int):
        """Pipeline 执行逻辑（内部方法）"""
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
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_paused",
                        summary="Pipeline paused.",
                        payload={"pipeline_id": pipeline.id, "pipeline_run_id": run.id},
                    )
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
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_run_failed",
                        summary=f"Pipeline failed at stage {stage_cfg.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "failed_stage": stage_cfg.name,
                            "failed_stage_display_name": stage_cfg.display_name,
                        },
                        run_summary=f"Pipeline failed at {stage_cfg.display_name}.",
                    )
                    complete_task_run(
                        db,
                        _pipeline_task_run(db, run),
                        status="failed",
                        summary=f"Pipeline failed at {stage_cfg.display_name}.",
                    )
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
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_waiting_for_approval",
                        summary=f"Pipeline waiting for approval at {stage.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "stage_name": stage.stage_name,
                            "display_name": stage.display_name,
                        },
                        run_summary=f"Waiting for approval at {stage.display_name}.",
                    )
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
            _append_pipeline_task_event(
                db,
                run,
                "pipeline_run_completed",
                summary="Pipeline run completed.",
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_run_id": run.id,
                    "run_number": run.run_number,
                },
                run_summary="Pipeline completed.",
            )
            complete_task_run(db, _pipeline_task_run(db, run), summary="Pipeline completed.")

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
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_run_failed",
                        summary="Pipeline execution crashed.",
                        payload={
                            "pipeline_id": pipeline.id if pipeline else None,
                            "pipeline_run_id": run.id,
                            "error": str(e)[:1000],
                        },
                        run_summary=str(e)[:280],
                    )
                    complete_task_run(
                        db,
                        _pipeline_task_run(db, run),
                        status="failed",
                        summary=str(e)[:280],
                    )
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
        runner_policy = _pipeline_runner_policy(pipeline, template)
        stage_policy = find_stage_policy(runner_policy, stage_cfg.name) or compile_pipeline_stage_policy(
            stage_cfg=stage_cfg,
            stage_order=stage.stage_order,
            stage_count=max(len(getattr(template, "stages", []) or []), stage.stage_order + 1),
            tool_policy_pack=_pipeline_stage_tool_policy_pack(stage_cfg.agent),
        )
        timeout_seconds = stage_policy.timeout_minutes * 60

        # 构建阶段上下文
        context = self._build_stage_context(db, run, stage_cfg, template)

        stage.status = "running"
        stage.started_at = datetime.now()
        stage.input_context = json.dumps(
            {
                "context_length": len(context),
                "stage_policy": stage_policy.to_payload(),
            },
            ensure_ascii=False,
        )
        db.commit()

        # 将 skill full 内容写入 .catown/skills/ 供 Agent 按需 read_file
        workspace = _get_workspace(run)
        self._write_skill_full_files(stage_cfg.agent, stage_cfg, workspace)
        linked_task_run = _pipeline_task_run(db, run)
        checkpoint_snapshot = build_task_run_checkpoint_snapshot(linked_task_run)
        checkpoint_continuation_state = describe_checkpoint_continuation_state(checkpoint_snapshot)

        # 写入阶段开始事件
        active_skills = list(stage_policy.active_skills)
        db.add(Event(
            run_id=run.id,
            event_type=EventType.STAGE_START,
            agent_name=stage_policy.agent_name,
            stage_name=stage_policy.stage_name,
            summary=f"Stage: {stage_policy.display_name} ({stage_policy.agent_name})",
            payload=json.dumps({
                "gate": stage_policy.approval.kind,
                "timeout_minutes": stage_policy.timeout_minutes,
                "active_skills": active_skills,
                "expected_artifacts": stage_policy.delivery.expected_artifacts,
                "stage_policy": stage_policy.to_payload(),
            }, ensure_ascii=False),
        ))
        db.commit()
        _append_pipeline_task_event(
            db,
            run,
            "pipeline_stage_started",
            agent_name=stage_policy.agent_name,
            summary=f"Pipeline stage started: {stage_policy.display_name}.",
            payload={
                "pipeline_id": pipeline.id,
                "pipeline_run_id": run.id,
                "stage_name": stage_policy.stage_name,
                "display_name": stage_policy.display_name,
                "gate": stage_policy.approval.kind,
                "timeout_minutes": stage_policy.timeout_minutes,
                "active_skills": active_skills,
                "expected_artifacts": stage_policy.delivery.expected_artifacts,
                "stage_policy": stage_policy.to_payload(),
                "checkpoint_snapshot": checkpoint_snapshot,
                "continuation_state": checkpoint_continuation_state,
            },
            target_agent_name=stage_policy.agent_name,
        )

        await event_bus.emit("stage_started", {
            "pipeline_id": pipeline.id,
            "run_id": run.id,
            "stage": stage_policy.stage_name,
            "display_name": stage_policy.display_name,
            "agent": stage_policy.agent_name,
            "gate": stage_policy.approval.kind,
            "active_skills": active_skills,
            "expected_artifacts": stage_policy.delivery.expected_artifacts,
            "stage_policy": stage_policy.to_payload(),
        })

        # 广播 skill 注入事件
        if active_skills:
            skills_config = self._load_skills_config()
            agent_data_skills = []
            try:
                config_file = settings.AGENT_CONFIG_FILE
                with open(config_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                agent_data_skills = data.get("agents", {}).get(stage_cfg.agent, {}).get("skills", [])
            except Exception:
                pass

            skill_details = []
            for skill_name in active_skills:
                skill = skills_config.get(skill_name, {})
                guide = skill.get("levels", {}).get("guide", "")
                hint = skill.get("levels", {}).get("hint", "")
                skill_details.append({
                    "name": skill_name,
                    "hint": hint,
                    "guide": guide[:500] if guide else None,
                    "guide_tokens": len(guide.split()) if guide else 0,
                })

            await event_bus.emit("skill_inject", {
                "pipeline_id": pipeline.id,
                "run_id": run.id,
                "agent": stage_policy.agent_name,
                "stage": stage_policy.stage_name,
                "skills": skill_details,
                "agent_all_skills": agent_data_skills,
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

                blocked_tool_state = getattr(stage, "_catown_blocked_tool", None)
                if isinstance(blocked_tool_state, dict) and blocked_tool_state:
                    queue_item = _find_latest_pending_pipeline_tool_queue_item(
                        db,
                        run=run,
                        stage=stage,
                    )
                    stage.status = "blocked"
                    stage.output_summary = summary[:2000] if summary else "(blocked tool approval pending)"
                    stage.completed_at = None
                    db.commit()
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_stage_blocked",
                        agent_name=stage_policy.agent_name,
                        summary=f"Pipeline stage blocked on tool approval: {blocked_tool_state.get('tool_name') or 'tool'}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "pipeline_stage_id": stage.id,
                            "stage_name": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "tool_name": blocked_tool_state.get("tool_name"),
                            "status": blocked_tool_state.get("status"),
                            "blocked_kind": blocked_tool_state.get("blocked_kind"),
                            "blocked_reason": blocked_tool_state.get("blocked_reason"),
                            "turn": blocked_tool_state.get("turn"),
                            "queue_item_id": getattr(queue_item, "id", None),
                            "stage_policy": stage_policy.to_payload(),
                        },
                        target_agent_name=stage_policy.agent_name,
                        run_summary=f"{stage_policy.display_name} blocked on {blocked_tool_state.get('tool_name') or 'tool'} approval.",
                    )
                    await event_bus.emit(
                        "stage_blocked",
                        {
                            "pipeline_id": pipeline.id,
                            "run_id": run.id,
                            "stage": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "tool_name": blocked_tool_state.get("tool_name"),
                            "blocked_kind": blocked_tool_state.get("blocked_kind"),
                            "queue_item_id": getattr(queue_item, "id", None),
                        },
                    )
                    return True

                # 记录产出物
                workspace = _get_workspace(run)
                artifacts = self._record_artifacts(db, stage, workspace, stage_policy.delivery.expected_artifacts)
                artifact_gate_results = _record_stage_artifact_policy_decisions(
                    db,
                    run=run,
                    stage=stage,
                    stage_policy=stage_policy,
                    runner_policy=runner_policy,
                    artifacts=artifacts,
                )
                blocked_artifact_gate = next(
                    (
                        gate_result
                        for gate_result in artifact_gate_results
                        if bool(gate_result.get("blocked"))
                    ),
                    None,
                )
                if blocked_artifact_gate is not None:
                    blocked_reason = str(blocked_artifact_gate.get("blocked_reason") or "").strip()
                    stage.status = "blocked"
                    stage.output_summary = blocked_reason or "Stage blocked by artifact policy."
                    stage.completed_at = None
                    db.commit()
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_stage_blocked",
                        agent_name=stage_policy.agent_name,
                        summary=f"Pipeline stage blocked by artifact policy: {stage_policy.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "pipeline_stage_id": stage.id,
                            "stage_name": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "blocked_kind": blocked_artifact_gate.get("blocked_kind"),
                            "blocked_reason": blocked_artifact_gate.get("blocked_reason"),
                            "policy_decision_summary": blocked_artifact_gate.get("policy_decision_summary"),
                            "stage_policy": stage_policy.to_payload(),
                        },
                        target_agent_name=stage_policy.agent_name,
                        run_summary=stage.output_summary[:280] if stage.output_summary else None,
                    )
                    await event_bus.emit(
                        "stage_blocked",
                        {
                            "pipeline_id": pipeline.id,
                            "run_id": run.id,
                            "stage": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "blocked_kind": blocked_artifact_gate.get("blocked_kind"),
                            "blocked_reason": blocked_artifact_gate.get("blocked_reason"),
                        },
                    )
                    return True

                stage.status = "completed"
                stage.output_summary = _format_stage_output_summary(
                    stage_name=stage_cfg.name,
                    display_name=stage_cfg.display_name,
                    agent_name=stage_cfg.agent,
                    raw_content=summary,
                    artifacts=stage_policy.delivery.expected_artifacts,
                )
                stage.completed_at = datetime.now()
                db.commit()

                # Git commit（如果有 git repo）
                self._git_commit(run, stage_cfg.name)

                # 写入阶段完成事件
                duration_min = (stage.completed_at - stage.started_at).total_seconds() / 60 if stage.started_at else 0
                db.add(Event(
                    run_id=run.id,
                    event_type=EventType.STAGE_END,
                    agent_name=stage_policy.agent_name,
                    stage_name=stage_policy.stage_name,
                    summary=f"Stage: {stage_policy.display_name} completed ({duration_min:.0f}min)",
                    payload=json.dumps({
                        "status": "completed",
                        "duration_min": round(duration_min, 1),
                        "output_summary": stage.output_summary[:500] if stage.output_summary else None,
                        "stage_policy": stage_policy.to_payload(),
                    }, ensure_ascii=False),
                ))
                db.commit()
                _append_pipeline_task_event(
                    db,
                    run,
                    "pipeline_stage_completed",
                    agent_name=stage_policy.agent_name,
                    summary=f"Pipeline stage completed: {stage_policy.display_name}.",
                    payload={
                        "pipeline_id": pipeline.id,
                        "pipeline_run_id": run.id,
                        "stage_name": stage_policy.stage_name,
                        "display_name": stage_policy.display_name,
                        "duration_min": round(duration_min, 1),
                        "output_summary": stage.output_summary[:500] if stage.output_summary else None,
                        "stage_policy": stage_policy.to_payload(),
                    },
                    target_agent_name=stage_policy.agent_name,
                    run_summary=stage.output_summary[:280] if stage.output_summary else None,
                )

                await event_bus.emit("stage_completed", {
                    "pipeline_id": pipeline.id,
                    "run_id": run.id,
                    "stage": stage_policy.stage_name,
                    "summary": stage.output_summary,
                    "stage_policy": stage_policy.to_payload(),
                })

                # 检查 gate
                if stage_policy.approval.required and permissions_auto_approve_all_enabled():
                    logger.info(
                        "Stage '%s' manual gate auto-approved by permissions.auto_approve_all",
                        stage_policy.stage_name,
                    )
                    db.add(Event(
                        run_id=run.id,
                        event_type=EventType.GATE_AUTO_APPROVED,
                        agent_name=stage_policy.agent_name,
                        stage_name=stage_policy.stage_name,
                        summary=f"Gate: {stage_policy.display_name} auto-approved by permission policy",
                        payload=json.dumps({
                            "gate_type": stage_policy.approval.kind,
                            "stage": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "stage_policy": stage_policy.to_payload(),
                            "auto_approve_all": True,
                        }, ensure_ascii=False),
                    ))
                    db.commit()
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_gate_auto_approved",
                        agent_name=stage_policy.agent_name,
                        summary=f"Auto-approved pipeline gate at {stage_policy.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "stage_name": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "gate_type": stage_policy.approval.kind,
                            "stage_policy": stage_policy.to_payload(),
                            "auto_approve_all": True,
                        },
                        target_agent_name=stage_policy.agent_name,
                        run_summary=f"Auto-approved gate at {stage_policy.display_name}.",
                    )
                    await event_bus.emit("gate_auto_approved", {
                        "pipeline_id": pipeline.id,
                        "run_id": run.id,
                        "stage": stage_policy.stage_name,
                        "display_name": stage_policy.display_name,
                        "stage_policy": stage_policy.to_payload(),
                    })
                elif stage_policy.approval.required:
                    stage.status = "blocked"
                    db.commit()
                    logger.info(f"Stage '{stage_policy.stage_name}' completed, blocked at manual gate")
                    queue_item = _queue_pipeline_gate_approval(
                        db,
                        pipeline=pipeline,
                        run=run,
                        stage=stage,
                        stage_policy=stage_policy,
                    )

                    # 写入 gate 阻塞事件
                    db.add(Event(
                        run_id=run.id,
                        event_type=EventType.GATE_BLOCKED,
                        agent_name=stage_policy.agent_name,
                        stage_name=stage_policy.stage_name,
                        summary=f"Gate: {stage_policy.display_name} — 等待人工审批",
                        payload=json.dumps({
                            "gate_type": stage_policy.approval.kind,
                            "stage": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "stage_policy": stage_policy.to_payload(),
                            "queue_item_id": getattr(queue_item, "id", None),
                        }, ensure_ascii=False),
                    ))
                    db.commit()
                    _append_pipeline_task_event(
                        db,
                        run,
                        "approval_queue_item_created",
                        agent_name=stage_policy.agent_name,
                        summary=f"Queued approval item for {stage_policy.display_name}.",
                        payload=build_approval_queue_item_created_event_payload(queue_item),
                    )
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_gate_blocked",
                        agent_name=stage_policy.agent_name,
                        summary=f"Pipeline gate blocked at {stage_policy.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "stage_name": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "gate_type": stage_policy.approval.kind,
                            "stage_policy": stage_policy.to_payload(),
                            "queue_item_id": getattr(queue_item, "id", None),
                        },
                        target_agent_name=stage_policy.agent_name,
                        run_summary=f"Waiting for approval at {stage_policy.display_name}.",
                    )

                    await event_bus.emit("gate_blocked", {
                        "pipeline_id": pipeline.id,
                        "run_id": run.id,
                        "stage": stage_policy.stage_name,
                        "display_name": stage_policy.display_name,
                        "stage_policy": stage_policy.to_payload(),
                    })

                return True

            except asyncio.TimeoutError:
                logger.warning(f"Stage '{stage_cfg.name}' timed out (attempt {attempt}/{max_retries})")
                stage.retry_count = attempt

                # 写入超时/重试事件
                if attempt < max_retries:
                    db.add(Event(
                        run_id=run.id,
                        event_type=EventType.STAGE_RETRY,
                        agent_name=stage_cfg.agent,
                        stage_name=stage_cfg.name,
                        summary=f"Stage: {stage_cfg.display_name} timeout, retry {attempt}/{max_retries}",
                        payload=json.dumps({"attempt": attempt, "reason": "timeout"}, ensure_ascii=False),
                    ))

                if attempt >= max_retries:
                    stage.status = "failed"
                    stage.error_message = f"Timeout after {max_retries} attempts"
                    stage.completed_at = datetime.now()
                    db.commit()

                    db.add(Event(
                        run_id=run.id,
                        event_type=EventType.TIMEOUT,
                        agent_name=stage_cfg.agent,
                        stage_name=stage_cfg.name,
                        summary=f"Stage: {stage_cfg.display_name} failed (timeout after {max_retries} retries)",
                        payload=json.dumps({"max_retries": max_retries, "reason": "timeout"}, ensure_ascii=False),
                    ))
                    db.commit()
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_stage_failed",
                        agent_name=stage_policy.agent_name,
                        summary=f"Pipeline stage timed out: {stage_policy.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "stage_name": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "failure_reason": "timeout",
                            "max_retries": max_retries,
                            "stage_policy": stage_policy.to_payload(),
                        },
                        target_agent_name=stage_policy.agent_name,
                        run_summary=f"{stage_policy.display_name} timed out after {max_retries} retries.",
                    )
                    return False
                db.commit()
                await asyncio.sleep(2 ** attempt)  # 指数退避

            except Exception as e:
                logger.error(f"Stage '{stage_cfg.name}' error (attempt {attempt}/{max_retries}): {e}")
                stage.retry_count = attempt

                # 写入错误/重试事件
                if attempt < max_retries:
                    db.add(Event(
                        run_id=run.id,
                        event_type=EventType.STAGE_RETRY,
                        agent_name=stage_cfg.agent,
                        stage_name=stage_cfg.name,
                        summary=f"Stage: {stage_cfg.display_name} error, retry {attempt}/{max_retries}",
                        payload=json.dumps({"attempt": attempt, "error": str(e)[:500]}, ensure_ascii=False),
                    ))

                if attempt >= max_retries:
                    stage.status = "failed"
                    stage.error_message = str(e)[:1000]
                    stage.completed_at = datetime.now()
                    db.commit()

                    db.add(Event(
                        run_id=run.id,
                        event_type=EventType.ERROR,
                        agent_name=stage_cfg.agent,
                        stage_name=stage_cfg.name,
                        summary=f"Stage: {stage_cfg.display_name} failed after {max_retries} retries",
                        payload=json.dumps({"error": str(e)[:1000], "max_retries": max_retries}, ensure_ascii=False),
                    ))
                    db.commit()
                    _append_pipeline_task_event(
                        db,
                        run,
                        "pipeline_stage_failed",
                        agent_name=stage_policy.agent_name,
                        summary=f"Pipeline stage failed: {stage_policy.display_name}.",
                        payload={
                            "pipeline_id": pipeline.id,
                            "pipeline_run_id": run.id,
                            "stage_name": stage_policy.stage_name,
                            "display_name": stage_policy.display_name,
                            "error": str(e)[:1000],
                            "max_retries": max_retries,
                            "stage_policy": stage_policy.to_payload(),
                        },
                        target_agent_name=stage_policy.agent_name,
                        run_summary=str(e)[:280],
                    )

                    # 检查打回逻辑（testing → development）
                    if stage_policy.rollback.enabled and stage_policy.rollback.target_stage:
                        logger.info(f"Rolling back to '{stage_policy.rollback.target_stage}'")
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

        # 前一阶段的产出摘要 (ADR-028 Phase 4: compact structured summaries)
        stages = (
            db.query(PipelineStage)
            .filter(PipelineStage.run_id == run.id, PipelineStage.status == "completed")
            .order_by(PipelineStage.stage_order)
            .all()
        )
        if stages:
            parts.append("## Previous Stage Summaries")
            for s in stages:
                if s.output_summary:
                    parts.append(f"### {s.display_name} ({s.agent_name})")
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

    def _assemble_stage_messages(
        self,
        *,
        run: PipelineRun,
        stage_cfg: StageConfig,
        context: str,
        llm_client: LLMClient,
        system_prompt: str,
        skills_config: Dict[str, Any],
        agent_skills: List[str],
        tool_names: List[str],
        turn_state: Optional[TurnContextState] = None,
        on_compaction: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        developer_fragments = [
            build_operating_developer_context(agent_name=stage_cfg.agent),
            build_stage_developer_context(
                stage_cfg=stage_cfg,
                active_skills=stage_cfg.active_skills,
                tools=tool_names,
                skills_config=skills_config,
                agent_skills=agent_skills,
            ),
        ]
        developer_fragments.extend(build_turn_state_developer_fragments(turn_state))

        user_fragments = build_runtime_user_fragments(runtime_context=context, run=run)

        # ADR-028 Phase 4: Inject summaries of completed upstream stages
        stage_summaries_fragment = _build_completed_stage_summaries_fragment(db, run, stage_cfg)
        if stage_summaries_fragment is not None:
            user_fragments.append(stage_summaries_fragment)

        user_fragments.extend(build_turn_state_user_fragments(turn_state))

        protocol_messages = turn_state.protocol_messages() if turn_state is not None else []
        selector = ContextSelector.for_context_window(
            context_window=self._get_agent_context_window(stage_cfg.agent, getattr(llm_client, "model", "")),
            base_system_prompt=system_prompt,
            current_input_messages=protocol_messages,
        )
        assembly = assemble_messages(
            base_system_prompt=system_prompt,
            developer_fragments=developer_fragments,
            user_fragments=user_fragments,
            current_input_messages=protocol_messages,
            selector=selector,
        )
        diagnostics = assembly.selector_diagnostics if isinstance(assembly.selector_diagnostics, dict) else {}
        if diagnostics.get("compacted") and on_compaction is not None:
            on_compaction(diagnostics)
        return assembly.to_messages()

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
        运行单个 Agent 的 LLM 对话循环（含审计写入）

        返回最终输出摘要
        """
        llm_client = get_llm_client_for_agent(stage_cfg.agent)
        tools = _build_tools_for_agent(stage_cfg.agent)

        # 加载 Agent 的 system_prompt（传入 stage_cfg 实现三级 Skill 注入）
        system_prompt = self._get_agent_system_prompt(stage_cfg.agent)
        agent_data = self._get_agent_config_data(stage_cfg.agent)
        agent_skills = agent_data.get("skills", []) if agent_data else []
        tool_names = AGENT_TOOLS.get(stage_cfg.agent, list(TOOL_REGISTRY.keys()))
        skills_config = self._load_skills_config()
        final_content = ""
        stage._catown_blocked_tool = None
        linked_task_run = _pipeline_task_run(db, run)
        checkpoint_snapshot = build_task_run_checkpoint_snapshot(linked_task_run)
        turn_state = build_turn_state_from_checkpoint_snapshot(checkpoint_snapshot)
        compaction_callback = _build_pipeline_context_compaction_callback(
            db,
            run,
            agent_name=stage_cfg.agent,
            extra_payload={
                "pipeline_id": pipeline.id,
                "pipeline_stage_id": stage.id,
                "stage_name": stage_cfg.name,
                "display_name": stage_cfg.display_name,
            },
        )
        record_agent_turn_started(
            db,
            linked_task_run,
            agent_name=stage_cfg.agent,
            summary=f"{stage_cfg.agent} started a pipeline stage turn.",
            payload=build_runtime_event_payload(
                pipeline_id=pipeline.id,
                pipeline_run_id=run.id,
                stage_name=stage_cfg.name,
                display_name=stage_cfg.display_name,
            ),
        )

        def _assemble_pipeline_stage_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
            return self._assemble_stage_messages(
                run=run,
                stage_cfg=stage_cfg,
                context=context,
                llm_client=llm_client,
                system_prompt=system_prompt,
                skills_config=skills_config,
                agent_skills=agent_skills,
                tool_names=tool_names,
                turn_state=current_turn_state,
                on_compaction=compaction_callback,
            )

        async def _before_pipeline_turn(turn_index: int, current_turn_state: TurnContextState) -> None:
            current_turn_state.add_boss_instructions(self._get_pending_instructions(db, run, stage_cfg.agent))
            current_turn_state.add_inter_agent_messages(_pop_messages_for_agent(db, run.id, stage_cfg.agent))

        def _before_pipeline_llm_call(frame, current_turn_state):
            # 审计：预先创建 LLM call 记录，异常时也能落错误信息。
            return {
                "record": LLMCall(
                    run_id=run.id,
                    stage_id=stage.id,
                    agent_name=stage_cfg.agent,
                    turn_index=frame.turn_index,
                    model=getattr(llm_client, "model", "unknown"),
                    system_prompt=system_prompt[:50000] if system_prompt else None,
                    messages=json.dumps(frame.messages[-10:], ensure_ascii=False)[:100000],
                ),
                "started_at": time.time(),
            }

        async def _on_pipeline_llm_error(frame, exc, current_turn_state):
            call_state = frame.state if isinstance(frame.state, dict) else {}
            llm_call_record = call_state.get("record")
            if llm_call_record is None:
                return
            llm_call_record.error = str(exc)[:5000]
            llm_call_record.duration_ms = int((time.time() - call_state.get("started_at", time.time())) * 1000)
            db.add(llm_call_record)
            db.commit()

        async def _on_pipeline_llm_response(frame, current_turn_state):
            call_state = frame.state if isinstance(frame.state, dict) else {}
            llm_call_record = call_state.get("record")
            if llm_call_record is None:
                return

            content = frame.content
            normalized_tool_calls = frame.normalized_tool_calls
            llm_call_record.response_content = content[:100000] if content else None
            llm_call_record.response_tool_calls = json.dumps(
                [_serialize_tool_call_preview(tc) for tc in normalized_tool_calls],
                ensure_ascii=False,
            ) if normalized_tool_calls else None
            llm_call_record.duration_ms = int((time.time() - call_state.get("started_at", time.time())) * 1000)
            if frame.usage:
                llm_call_record.token_input = frame.usage.get("prompt_tokens", 0)
                llm_call_record.token_output = frame.usage.get("completion_tokens", 0)
            db.add(llm_call_record)
            db.flush()
            db.add(Event(
                run_id=run.id,
                event_type=EventType.LLM_CALL,
                agent_name=stage_cfg.agent,
                stage_name=stage_cfg.name,
                summary=(
                    f"LLM #{frame.turn_index}: "
                    f"{llm_call_record.token_input}in/{llm_call_record.token_output}out, "
                    f"{llm_call_record.duration_ms}ms"
                ),
                payload=json.dumps({
                    "turn": frame.turn_index,
                    "model": llm_call_record.model,
                    "tokens_in": llm_call_record.token_input,
                    "tokens_out": llm_call_record.token_output,
                    "duration_ms": llm_call_record.duration_ms,
                    "content_preview": content[:200] if content else None,
                }, ensure_ascii=False),
            ))

            # 广播 LLM 调用事件（SSE 扩展）— 含完整上下文供卡片展示
            await event_bus.emit("llm_call", {
                "pipeline_id": pipeline.id,
                "run_id": run.id,
                "stage": stage_cfg.name,
                "agent": stage_cfg.agent,
                "turn": frame.turn_index,
                "model": llm_call_record.model,
                "tokens_in": llm_call_record.token_input,
                "tokens_out": llm_call_record.token_output,
                "duration_ms": llm_call_record.duration_ms,
                "system_prompt": system_prompt[:5000] if system_prompt else None,
                "response": content[:3000] if content else None,
                "tool_calls": [
                    {
                        "name": tc.get("function", {}).get("name"),
                        "args_preview": str(tc.get("function", {}).get("arguments", ""))[:200],
                    }
                    for tc in normalized_tool_calls
                ],
            })
            db.commit()

            if content:
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

        async def _execute_pipeline_tool(frame, tool_call):
            fn_name = tool_call["function"]["name"]
            tool_args_str = tool_call["function"].get("arguments", "{}")
            try:
                fn_args = json.loads(tool_args_str)
            except json.JSONDecodeError:
                fn_args = {}

            record_tool_call_started(
                db,
                linked_task_run,
                agent_name=stage_cfg.agent,
                turn=frame.turn_index + 1,
                tool_name=fn_name,
                arguments=tool_args_str,
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_run_id": run.id,
                    "pipeline_stage_id": stage.id,
                    "stage_name": stage_cfg.name,
                    "display_name": stage_cfg.display_name,
                },
            )
            tool_start = time.time()
            tool_result = await _execute_tool(stage_cfg.agent, run, fn_name, fn_args, db=db, stage_id=stage.id)
            tool_duration = int((time.time() - tool_start) * 1000)

            tool_result_text = str(
                tool_result.get("result")
                if isinstance(tool_result, dict) and tool_result.get("__catown_tool_result__") is True
                else (tool_result or "(no output)")
            )
            success = tool_result_succeeded(tool_result)
            result_len = len(tool_result_text)
            tool_result_record = build_tool_result_record(
                tool_call_id=tool_call.get("id", ""),
                tool_name=fn_name,
                arguments=tool_call["function"].get("arguments", "{}"),
                result=tool_result,
                success=success,
            )
            success = tool_result_record.success

            call_state = frame.state if isinstance(frame.state, dict) else {}
            llm_call_record = call_state.get("record")
            db.add(ToolCall(
                llm_call_id=llm_call_record.id if llm_call_record is not None else None,
                run_id=run.id,
                stage_id=stage.id,
                agent_name=stage_cfg.agent,
                tool_name=fn_name,
                arguments=json.dumps(fn_args, ensure_ascii=False)[:50000],
                result_summary=tool_result_record.result[:500],
                result_length=result_len,
                success=success,
                duration_ms=tool_duration,
            ))
            db.add(Event(
                run_id=run.id,
                event_type=EventType.TOOL_CALL,
                agent_name=stage_cfg.agent,
                stage_name=stage_cfg.name,
                summary=f"{fn_name}({tool_result_record.status}, {result_len} chars, {tool_duration}ms)",
                payload=json.dumps({
                    "tool": fn_name,
                    "args_keys": list(fn_args.keys()),
                    "success": success,
                    "status": tool_result_record.status,
                    "blocked": tool_result_record.blocked,
                    "blocked_kind": tool_result_record.blocked_kind,
                    "blocked_reason": tool_result_record.blocked_reason,
                    "result_length": result_len,
                    "result_preview": tool_result_record.result[:200],
                    "duration_ms": tool_duration,
                }, ensure_ascii=False),
            ))
            db.commit()

            await event_bus.emit("tool_call", {
                "pipeline_id": pipeline.id,
                "run_id": run.id,
                "stage": stage_cfg.name,
                "agent": stage_cfg.agent,
                "tool": fn_name,
                "arguments": json.dumps(fn_args, ensure_ascii=False)[:3000],
                "success": success,
                "status": tool_result_record.status,
                "blocked": tool_result_record.blocked,
                "blocked_kind": tool_result_record.blocked_kind,
                "blocked_reason": tool_result_record.blocked_reason,
                "result": tool_result_record.result[:3000],
                "duration_ms": tool_duration,
            })
            return tool_result_record

        async def _on_pipeline_tool_round(frame, tool_results, current_turn_state):
            blocked_result = next((result for result in tool_results if getattr(result, "blocked", False)), None)
            if blocked_result is None:
                blocked_result = getattr(frame, "blocked_tool_result", None)
            if blocked_result is not None:
                stage._catown_blocked_tool = {
                    "tool_name": blocked_result.tool_name,
                    "status": blocked_result.status,
                    "blocked_kind": blocked_result.blocked_kind,
                    "blocked_reason": blocked_result.blocked_reason,
                    "turn": frame.turn_index + 1,
                }
            record_runner_tool_round(
                db,
                linked_task_run,
                agent_name=stage_cfg.agent,
                turn=frame.turn_index + 1,
                tool_names=[
                    tool_call["function"]["name"]
                    for tool_call in (
                        (frame.executed_tool_calls or frame.normalized_tool_calls)
                        + ([{"function": {"name": blocked_result.tool_name}}] if blocked_result is not None else [])
                    )
                ],
                tool_results=tool_results,
                blocked_tool_results=[blocked_result] if blocked_result is not None else None,
                summary=f"{stage_cfg.agent} completed a pipeline tool round.",
                assistant_content=frame.content,
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_run_id": run.id,
                    "pipeline_stage_id": stage.id,
                    "stage_name": stage_cfg.name,
                    "display_name": stage_cfg.display_name,
                },
            )

        loop_result = await execute_non_stream_turn_loop(
            llm_client=llm_client,
            tools=tools,
            turn_state=turn_state,
            assemble_messages=_assemble_pipeline_stage_messages,
            execute_tool_call=_execute_pipeline_tool,
            max_turns=20,
            before_turn=_before_pipeline_turn,
            before_llm_call=_before_pipeline_llm_call,
            on_llm_response=_on_pipeline_llm_response,
            on_llm_error=_on_pipeline_llm_error,
            on_tool_round=_on_pipeline_tool_round,
        )
        if loop_result.awaiting_tool_approval:
            return ""
        final_content = loop_result.final_content

        record_agent_turn_completed(
            db,
            linked_task_run,
            agent_name=stage_cfg.agent,
            response_content=final_content,
            summary=f"{stage_cfg.agent} completed the pipeline stage turn.",
            payload=build_runtime_event_payload(
                pipeline_id=pipeline.id,
                pipeline_run_id=run.id,
                stage_name=stage_cfg.name,
                display_name=stage_cfg.display_name,
            ),
        )

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
        db.flush()
        enqueue_message_delivery(db, msg)
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

    def _load_skills_config(self) -> Dict:
        """加载 canonical SKILL.md package 格式的 skills。"""
        try:
            return load_skill_registry(settings.SKILLS_DIR)
        except Exception:
            return {}

    def _get_agent_config_data(self, agent_name: str) -> Dict[str, Any]:
        try:
            with open(settings.AGENT_CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data.get("agents", {}).get(agent_name, {})
        except Exception:
            return {}

    def _context_window_from_provider(self, provider_data: Any, model_id: str) -> Optional[int]:
        return context_window_from_provider(provider_data, model_id)

    def _get_agent_context_window(self, agent_name: str, model_id: str) -> Optional[int]:
        return resolve_llm_context_window(agent_name, model_id)

    def _get_agent_system_prompt(self, agent_name: str) -> str:
        """Build the stable system layer from structured agent configuration only."""
        prompt = build_base_system_prompt(
            self._get_agent_config_data(agent_name),
            fallback_name=agent_name,
        )
        return prompt or f"You are {agent_name}, a helpful AI assistant."

    def _write_skill_full_files(
        self, agent_name: str, stage_cfg: "StageConfig", workspace: Path
    ):
        """
        将 Agent 配置的所有 skill 的 full 内容写入 .catown/skills/ 目录。
        Agent 通过 read_file 按需读取。
        """
        config_file = settings.AGENT_CONFIG_FILE
        try:
            with open(config_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            agent_skills = data.get("agents", {}).get(agent_name, {}).get("skills", [])
        except Exception:
            return

        skills_config = self._load_skills_config()
        if not skills_config or not agent_skills:
            return

        write_workspace_skill_packages(skills_config, agent_skills, workspace)

    def _get_pending_instructions(
        self, db: Session, run: PipelineRun, agent_name: str
    ) -> List[str]:
        """获取并消费待处理的 BOSS 指令。"""
        instructions = _pop_instructions_for_agent(db, run.id, agent_name)
        if instructions:
            return instructions

        return consume_legacy_instruction_texts_for_agent(db, run_id=run.id, agent_name=agent_name)

    def _record_artifacts(
        self,
        db: Session,
        stage: PipelineStage,
        workspace: Path,
        expected: List[str],
    ) -> List[StageArtifact]:
        """记录阶段产出物"""
        artifacts: List[StageArtifact] = []
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
                artifacts.append(artifact)
        db.commit()
        return artifacts

    def _git_init(self, run: PipelineRun):
        """初始化 workspace 的 Git 仓库"""
        workspace = _get_workspace(run)
        git_dir = workspace / ".git"
        if git_dir.exists():
            return
        try:
            import subprocess
            # git init
            subprocess.run(
                ["git", "init"],
                cwd=str(workspace),
                capture_output=True,
                timeout=10,
            )
            # 初始 commit
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(workspace),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", "[pipeline] workspace initialized", "--allow-empty"],
                cwd=str(workspace),
                capture_output=True,
                timeout=10,
            )
            logger.debug(f"Git initialized in {workspace}")
        except Exception as e:
            logger.debug(f"Git init skipped: {e}")

    def _git_commit(self, run: PipelineRun, stage_name: str):
        """阶段完成自动 Git commit"""
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

    def _git_tag(self, run: PipelineRun, tag: str, message: str = ""):
        """Release 阶段自动打 Git tag"""
        workspace = _get_workspace(run)
        git_dir = workspace / ".git"
        if not git_dir.exists():
            return
        try:
            import subprocess
            cmd = ["git", "tag", "-a", tag, "-m", message or tag]
            subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                timeout=10,
            )
            logger.info(f"Git tag created: {tag} in {workspace}")
        except Exception as e:
            logger.debug(f"Git tag skipped: {e}")

    # ---- 并发管理 ----

    def running_count(self) -> int:
        """当前正在执行的 pipeline 数量"""
        # 清理已完成的 task
        done = [pid for pid, t in self._running_tasks.items() if t.done()]
        for pid in done:
            del self._running_tasks[pid]
        return len(self._running_tasks)

    def get_running_pipelines(self) -> List[int]:
        """获取正在运行的 pipeline ID 列表"""
        done = [pid for pid, t in self._running_tasks.items() if t.done()]
        for pid in done:
            del self._running_tasks[pid]
        return list(self._running_tasks.keys())

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
