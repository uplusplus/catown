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
GRAPHIFY_INSTALL_HINT = "pip install graphifyy"
GRAPHIFY_IGNORED_DIRS = {
    ".git",
    ".catown",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "graphify-out",
    "htmlcov",
    "node_modules",
    "reports",
    "uploads",
}
GRAPHIFY_SOURCE_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".rs",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


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


def _iter_graph_source_files(workspace: str):
    """Yield source-ish files used for local graph staleness checks."""
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [
            dirname
            for dirname in dirs
            if dirname not in GRAPHIFY_IGNORED_DIRS and not dirname.startswith(".")
        ]
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext.lower() not in GRAPHIFY_SOURCE_EXTENSIONS:
                continue
            yield os.path.join(root, filename)


def _latest_source_mtime(workspace: str) -> tuple[float, str | None]:
    latest_mtime = 0.0
    latest_path: str | None = None
    for path in _iter_graph_source_files(workspace):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = path
    return latest_mtime, latest_path


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
                    "enum": [
                        "check", "request_build", "query", "report",
                        "update", "explain", "path", "check_update",
                    ],
                    "description": (
                        "Action to perform: "
                        "'check' = check if graph exists and return metadata; "
                        "'request_build' = present a Choice Box to BOSS for build approval; "
                        "'query' = run a graphify query against the existing graph; "
                        "'report' = read the GRAPH_REPORT.md summary; "
                        "'update' = incremental re-extraction (no LLM cost, no approval needed); "
                        "'explain' = plain-language explanation of a node and its neighbors; "
                        "'path' = shortest path between two nodes; "
                        "'check_update' = check if semantic re-extraction is pending."
                    ),
                },
                "query_text": {
                    "type": "string",
                    "description": (
                        "Query string for action='query' (e.g. '模块 A 依赖哪些外部库'), "
                        "or node name for action='explain', "
                        "or ignored for other actions."
                    ),
                },
                "target_node": {
                    "type": "string",
                    "description": "Target node for action='path' (the endpoint node name).",
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
                "force": {
                    "type": "boolean",
                    "description": "For action='update': force rebuild even if fewer nodes (default false).",
                    "default": False,
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        query_text: str = "",
        target_node: str = "",
        chatroom_id: int | None = None,
        project_id: int | None = None,
        agent_name: str | None = None,
        force: bool = False,
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
            return await self._action_query_async(query_text)
        elif action == "report":
            return self._action_report()
        elif action == "update":
            return await self._action_update_async(force=force)
        elif action == "explain":
            return await self._action_explain_async(query_text)
        elif action == "path":
            return await self._action_path_async(query_text, target_node)
        elif action == "check_update":
            return await self._action_check_update_local_async()
        else:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    f"[Knowledge Graph] Error: Unknown action '{action}'. "
                    f"Valid actions: check, request_build, query, report, "
                    f"update, explain, path, check_update."
                ),
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
                    ["graphify", "update", workspace],
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
                logger.error("[KnowledgeGraph] graphify not found. Install with: %s", GRAPHIFY_INSTALL_HINT)
            except Exception as exc:
                logger.error("[KnowledgeGraph] graphify build failed: %s", exc)

        store.register_action_handler(box.id, _on_graph_approved)

        result_text = (
            f"📋 已创建知识图谱构建审批请求\n"
            f"  - Choice Box ID: {box.id}\n"
            f"  - 等待 BOSS 审批...\n"
            f"  - BOSS 批准后将自动执行 graphify update <workspace>"
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

    async def _action_query_async(self, query_text: str) -> Dict[str, Any]:
        """Execute graphify query directly and return results."""
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

        import subprocess
        workspace = _workspace_root()
        graph_path = _graph_json_path()

        try:
            proc = subprocess.run(
                ["graphify", "query", query_text, "--graph", graph_path],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if proc.returncode != 0:
                return build_structured_tool_result(
                    tool_name=self.name,
                    result_text=f"❌ 查询失败:\n{stderr or output}",
                    success=False,
                    status="query_error",
                )

            if not output:
                output = "(查询完成，无结果返回)"

            # Truncate very long output
            if len(output) > 15000:
                output = output[:15000] + "\n\n... (truncated)"

            return build_structured_tool_result(
                tool_name=self.name,
                result_text=output,
                success=True,
                status="query_result",
                metadata={
                    "query": query_text,
                    "graph_path": graph_path,
                    "output_size": len(output),
                },
            )
        except subprocess.TimeoutExpired:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="❌ 查询超时（60s）。查询可能过于复杂，请缩小范围。",
                success=False,
                status="query_timeout",
            )
        except FileNotFoundError:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ graphify 未安装。请运行: {GRAPHIFY_INSTALL_HINT}",
                success=False,
                status="graphify_not_found",
            )
        except Exception as exc:
            logger.error("[KnowledgeGraph] query failed: %s", exc)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ 查询异常: {exc}",
                success=False,
                status="query_exception",
            )

    async def _action_update_async(self, force: bool = False) -> Dict[str, Any]:
        """Incremental graph update — re-extract code files without LLM calls."""
        if not _graph_exists():
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    "❌ 知识图谱不存在，无法增量更新。\n"
                    "使用 action='request_build' 先构建图谱。"
                ),
                success=False,
                status="graph_not_found",
            )

        import subprocess
        workspace = _workspace_root()

        cmd = ["graphify", "update", workspace]
        if force:
            cmd.append("--force")

        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if proc.returncode != 0:
                return build_structured_tool_result(
                    tool_name=self.name,
                    result_text=f"❌ 增量更新失败:\n{stderr or output}",
                    success=False,
                    status="update_error",
                )

            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"✅ 知识图谱增量更新完成\n\n{output}",
                success=True,
                status="updated",
                metadata={"force": force},
            )
        except subprocess.TimeoutExpired:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="❌ 增量更新超时（120s）。项目可能过大。",
                success=False,
                status="update_timeout",
            )
        except FileNotFoundError:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ graphify 未安装。请运行: {GRAPHIFY_INSTALL_HINT}",
                success=False,
                status="graphify_not_found",
            )
        except Exception as exc:
            logger.error("[KnowledgeGraph] update failed: %s", exc)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ 增量更新异常: {exc}",
                success=False,
                status="update_exception",
            )

    async def _action_explain_async(self, node_name: str) -> Dict[str, Any]:
        """Get plain-language explanation of a node and its neighbors."""
        if not _graph_exists():
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="❌ 知识图谱不存在。使用 action='request_build' 先构建图谱。",
                success=False,
                status="graph_not_found",
            )

        node_name = str(node_name or "").strip()
        if not node_name:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Knowledge Graph] Error: query_text (node name) is required for explain action.",
                success=False,
                status="missing_node",
            )

        import subprocess
        workspace = _workspace_root()
        graph_path = _graph_json_path()

        try:
            proc = subprocess.run(
                ["graphify", "explain", node_name, "--graph", graph_path],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = proc.stdout.strip()

            if proc.returncode != 0:
                stderr = proc.stderr.strip()
                return build_structured_tool_result(
                    tool_name=self.name,
                    result_text=f"❌ 解释失败:\n{stderr or output}",
                    success=False,
                    status="explain_error",
                )

            if not output:
                output = f"(未找到节点 '{node_name}' 的信息)"

            return build_structured_tool_result(
                tool_name=self.name,
                result_text=output,
                success=True,
                status="explain_result",
                metadata={"node": node_name},
            )
        except subprocess.TimeoutExpired:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="❌ 解释超时。",
                success=False,
                status="explain_timeout",
            )
        except FileNotFoundError:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ graphify 未安装。请运行: {GRAPHIFY_INSTALL_HINT}",
                success=False,
                status="graphify_not_found",
            )
        except Exception as exc:
            logger.error("[KnowledgeGraph] explain failed: %s", exc)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ 解释异常: {exc}",
                success=False,
                status="explain_exception",
            )

    async def _action_path_async(self, source: str, target: str) -> Dict[str, Any]:
        """Find shortest path between two nodes in the graph."""
        if not _graph_exists():
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="❌ 知识图谱不存在。使用 action='request_build' 先构建图谱。",
                success=False,
                status="graph_not_found",
            )

        source = str(source or "").strip()
        target = str(target or "").strip()
        if not source or not target:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Knowledge Graph] Error: query_text (source) and target_node (target) are both required for path action.",
                success=False,
                status="missing_nodes",
            )

        import subprocess
        workspace = _workspace_root()
        graph_path = _graph_json_path()

        try:
            proc = subprocess.run(
                ["graphify", "path", source, target, "--graph", graph_path],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = proc.stdout.strip()

            if proc.returncode != 0:
                stderr = proc.stderr.strip()
                return build_structured_tool_result(
                    tool_name=self.name,
                    result_text=f"❌ 路径查询失败:\n{stderr or output}",
                    success=False,
                    status="path_error",
                )

            if not output:
                output = f"(未找到从 '{source}' 到 '{target}' 的路径)"

            return build_structured_tool_result(
                tool_name=self.name,
                result_text=output,
                success=True,
                status="path_result",
                metadata={"source": source, "target": target},
            )
        except subprocess.TimeoutExpired:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="❌ 路径查询超时。",
                success=False,
                status="path_timeout",
            )
        except FileNotFoundError:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ graphify 未安装。请运行: {GRAPHIFY_INSTALL_HINT}",
                success=False,
                status="graphify_not_found",
            )
        except Exception as exc:
            logger.error("[KnowledgeGraph] path failed: %s", exc)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"❌ 路径查询异常: {exc}",
                success=False,
                status="path_exception",
            )

    async def _action_check_update_local_async(self) -> Dict[str, Any]:
        """Check graph freshness with local source-file mtimes."""
        if not _graph_exists():
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="Knowledge graph does not exist. Use action='request_build' first.",
                success=False,
                status="graph_not_found",
            )

        try:
            workspace = _workspace_root()
            graph_mtime = os.path.getmtime(_graph_json_path())
            latest_mtime, latest_path = _latest_source_mtime(workspace)
            needs_update = latest_mtime > graph_mtime
            latest_rel_path = (
                os.path.relpath(latest_path, workspace).replace("\\", "/")
                if latest_path
                else None
            )
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    f"Knowledge graph may need update; latest source file is {latest_rel_path}."
                    if needs_update
                    else "Knowledge graph appears current by local source-file mtimes."
                ),
                success=True,
                status="needs_update" if needs_update else "up_to_date",
                metadata={
                    "needs_update": needs_update,
                    "graph_modified_at": graph_mtime,
                    "latest_source_modified_at": latest_mtime,
                    "latest_source_path": latest_rel_path,
                },
            )
        except Exception as exc:
            logger.error("[KnowledgeGraph] local check_update failed: %s", exc)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"Knowledge graph update check failed: {exc}",
                success=False,
                status="check_exception",
            )

    async def _action_check_update_async(self) -> Dict[str, Any]:
        """Backward-compatible alias for local graph freshness checks."""
        return await self._action_check_update_local_async()

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
