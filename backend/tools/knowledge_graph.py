# -*- coding: utf-8 -*-
"""
Knowledge Graph Tool — build and query project code knowledge graphs via graphify.

Integrates with Choice Box for BOSS approval before building/updating graphs.
Ref: ADR-004 (知识图谱集成方案)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("catown.knowledge_graph")

from services.choice_box import (
    ChoiceBox,
    ChoiceBoxType,
    ChoiceOption,
    get_choice_box_store,
)
from services.tool_governance import build_structured_tool_result
from tools.base import BaseTool
from tools.file_operations import get_active_workspace

# Directory where graphify outputs its artifacts
GRAPHIFY_OUT_DIR = "graphify-out"
GRAPH_JSON = "graph.json"
GRAPH_REPORT = "GRAPH_REPORT.md"


def _workspace_root() -> str:
    """Return the active workspace root directory."""
    workspace = get_active_workspace()
    if not workspace:
        raise RuntimeError("No active workspace configured.")
    return os.path.realpath(workspace)


def _graphify_out_path() -> str:
    return os.path.join(_workspace_root(), GRAPHIFY_OUT_DIR)


def _graph_json_path() -> str:
    return os.path.join(_graphify_out_path(), GRAPH_JSON)


def _graph_report_path() -> str:
    return os.path.join(_graphify_out_path(), GRAPH_REPORT)


def _graph_exists() -> bool:
    return os.path.isfile(_graph_json_path())


def _graph_metadata() -> Dict[str, Any]:
    """Return metadata about the existing graph, if any."""
    graph_path = _graph_json_path()
    if not os.path.isfile(graph_path):
        return {"exists": False}
    try:
        stat = os.stat(graph_path)
        return {
            "exists": True,
            "path": graph_path,
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
        }
    except OSError:
        return {"exists": False}


class KnowledgeGraphTool(BaseTool):
    """Tool for building and querying project code knowledge graphs."""

    name = "knowledge_graph"
    description = (
        "Build or query a project code knowledge graph using graphify. "
        "Building a graph requires BOSS approval (LLM API cost). "
        "Querying an existing graph is a local operation (no approval needed)."
    )

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["check", "request_build", "query", "report"],
                    "description": (
                        "Action to perform: "
                        "'check' = check if graph exists and return metadata; "
                        "'request_build' = present a Choice Box to BOSS for build approval; "
                        "'query' = run a graphify query against the existing graph; "
                        "'report' = read the GRAPH_REPORT.md summary."
                    ),
                },
                "query_text": {
                    "type": "string",
                    "description": "Query string for action='query', e.g. '模块 A 依赖哪些外部库'.",
                },
                "chatroom_id": {
                    "type": "integer",
                    "description": "Chatroom ID for the Choice Box (required for action='request_build').",
                },
                "project_id": {
                    "type": "integer",
                    "description": "Project ID for context.",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent requesting the action.",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        query_text: str = "",
        chatroom_id: int | None = None,
        project_id: int | None = None,
        agent_name: str | None = None,
        **kwargs,
    ) -> Any:
        action = str(action or "").strip().lower()

        if action == "check":
            return self._action_check()
        elif action == "request_build":
            return self._action_request_build(
                chatroom_id=chatroom_id,
                project_id=project_id,
                agent_name=agent_name or "agent",
            )
        elif action == "query":
            return self._action_query(query_text)
        elif action == "report":
            return self._action_report()
        else:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Knowledge Graph] Error: Unknown action '{action}'. Valid actions: check, request_build, query, report.",
                success=False,
                status="invalid_action",
            )

    def _action_check(self) -> Dict[str, Any]:
        """Check if the knowledge graph exists and return metadata."""
        meta = _graph_metadata()
        if meta["exists"]:
            report_exists = os.path.isfile(_graph_report_path())
            result_text = (
                f"✅ 知识图谱已存在\n"
                f"  - graph.json: {meta['size_bytes']} bytes\n"
                f"  - GRAPH_REPORT.md: {'存在' if report_exists else '不存在'}\n"
                f"  - 可直接使用 query 或 report 操作查询"
            )
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=result_text,
                success=True,
                status="graph_exists",
                metadata=meta,
            )
        else:
            result_text = (
                "❌ 知识图谱不存在\n"
                "  - 需要先构建图谱（需 BOSS 审批，因为会调用 LLM API）\n"
                "  - 使用 action='request_build' 请求构建许可"
            )
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=result_text,
                success=True,
                status="graph_not_found",
                metadata=meta,
            )

    def _action_request_build(
        self,
        chatroom_id: int | None,
        project_id: int | None,
        agent_name: str,
    ) -> Dict[str, Any]:
        """Create a Choice Box asking BOSS to approve knowledge graph building."""
        if _graph_exists():
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="✅ 知识图谱已存在，无需重新构建。使用 action='query' 查询。",
                success=True,
                status="already_exists",
            )

        if chatroom_id is None:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Knowledge Graph] Error: chatroom_id is required for request_build action.",
                success=False,
                status="missing_chatroom",
            )

        workspace = _workspace_root()

        # Create a Choice Box for BOSS approval
        box = ChoiceBox(
            id="",
            type=ChoiceBoxType.SINGLE,
            source_agent=agent_name,
            question="请求构建项目代码知识图谱（graphify）",
            context="🧠 知识图谱构建",
            options=[
                ChoiceOption(
                    "approve",
                    "✅ 批准构建",
                    "调用 graphify 建图，会产生 LLM API 费用",
                ),
                ChoiceOption(
                    "reject",
                    "❌ 拒绝",
                    "跳过建图，Agent 将逐文件理解代码",
                ),
            ],
            multi=False,
        )

        # Store metadata for post-approval execution
        box_metadata = box.to_metadata()
        box_metadata["knowledge_graph"] = {
            "action": "build",
            "workspace": workspace,
            "project_id": project_id,
            "chatroom_id": chatroom_id,
            "agent_name": agent_name,
        }
        box.metadata = box_metadata.get("knowledge_graph", {})

        store = get_choice_box_store()
        store.create(box)

        # Register an action handler for post-approval graphify execution
        async def _on_graph_approved():
            """Execute graphify build after BOSS approval."""
            import subprocess
            logger.info("[KnowledgeGraph] BOSS approved graph build, running graphify...")
            try:
                proc = subprocess.run(
                    ["graphify", ".", "--no-viz"],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                success = proc.returncode == 0
                output = proc.stdout if success else f"{proc.stdout}\n{proc.stderr}"
                # Publish result as a chat message
                try:
                    from services.chat_publish import publish_saved_chat_message
                    from models.database import get_db as _get_db, Message as _Message
                    db = next(_get_db())
                    try:
                        if success:
                            result_content = (
                                f"✅ 知识图谱构建完成\n\n"
                                f"```\n{output[-2000:] if len(output) > 2000 else output}\n```\n\n"
                                f"产出物位于 `{GRAPHIFY_OUT_DIR}/` 目录。\n"
                                f"可使用 `knowledge_graph(action='report')` 查看结构概览。"
                            )
                        else:
                            result_content = f"❌ 知识图谱构建失败\n\n```\n{output[-2000:]}\n```"
                        msg = _Message(
                            chatroom_id=chatroom_id or 0,
                            content=result_content,
                            message_type="system",
                            metadata_json=json.dumps({
                                "source": "knowledge_graph",
                                "action": "build_result",
                                "success": success,
                            }),
                        )
                        db.add(msg)
                        db.commit()
                    finally:
                        db.close()
                except Exception as pub_exc:
                    logger.warning("[KnowledgeGraph] Failed to publish build result: %s", pub_exc)
            except subprocess.TimeoutExpired:
                logger.error("[KnowledgeGraph] graphify build timed out (300s)")
            except FileNotFoundError:
                logger.error("[KnowledgeGraph] graphify not found. Install with: pip install graphify")
            except Exception as exc:
                logger.error("[KnowledgeGraph] graphify build failed: %s", exc)

        store.register_action_handler(box.id, _on_graph_approved)

        result_text = (
            f"📋 已创建知识图谱构建审批请求\n"
            f"  - Choice Box ID: {box.id}\n"
            f"  - 等待 BOSS 审批...\n"
            f"  - BOSS 批准后将自动执行 graphify . --no-viz"
        )
        return build_structured_tool_result(
            tool_name=self.name,
            result_text=result_text,
            success=True,
            status="pending_approval",
            metadata={
                "choice_box_id": box.id,
                "choice_box": box.to_dict(),
                "workspace": workspace,
            },
        )

    def _action_query(self, query_text: str) -> Dict[str, Any]:
        """Run a graphify query against the existing graph."""
        if not _graph_exists():
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    "❌ 知识图谱不存在，无法查询。\n"
                    "使用 action='request_build' 先构建图谱。"
                ),
                success=False,
                status="graph_not_found",
            )

        query_text = str(query_text or "").strip()
        if not query_text:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Knowledge Graph] Error: query_text is required for query action.",
                success=False,
                status="missing_query",
            )

        # The actual graphify query is executed via execute_code or run_shell by the agent.
        # This action validates preconditions and provides guidance.
        graph_path = _graph_json_path()
        result_text = (
            f"📊 知识图谱查询就绪\n"
            f"  - 图谱路径: {graph_path}\n"
            f"  - 查询: {query_text}\n"
            f"  - 请使用 execute_code 或 run_shell 执行:\n"
            f"    graphify query \"{query_text}\" --graph {GRAPHIFY_OUT_DIR}/{GRAPH_JSON}"
        )
        return build_structured_tool_result(
            tool_name=self.name,
            result_text=result_text,
            success=True,
            status="query_ready",
            metadata={
                "graph_path": graph_path,
                "query": query_text,
                "command": f'graphify query "{query_text}" --graph {GRAPHIFY_OUT_DIR}/{GRAPH_JSON}',
            },
        )

    def _action_report(self) -> Dict[str, Any]:
        """Read the GRAPH_REPORT.md summary."""
        report_path = _graph_report_path()
        if not os.path.isfile(report_path):
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    "❌ GRAPH_REPORT.md 不存在。\n"
                    "知识图谱可能尚未构建。使用 action='check' 确认状态。"
                ),
                success=False,
                status="report_not_found",
            )

        try:
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            if len(content) > 20000:
                content = content[:20000] + "\n\n... (truncated)"
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=content,
                success=True,
                status="report_loaded",
                metadata={"path": report_path, "size": len(content)},
            )
        except Exception as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Knowledge Graph] Error reading report: {exc}",
                success=False,
                status="report_error",
            )
