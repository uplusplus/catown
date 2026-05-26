# -*- coding: utf-8 -*-
"""
Save Memory Tool — three-layer memory write.

Supports writing to:
1. Project memory (.catown/memory/) — file-based markdown
2. Long-term memory (memories table) — database

Short-term memory is written automatically by the engine, not by agents directly.

Design follows PRD §4.6 three-layer memory architecture.
"""
from .base import BaseTool
import asyncio
from typing import Optional


class SaveMemoryTool(BaseTool):
    """Tool for saving information to the agent's memory system."""

    name = "save_memory"
    description = (
        "Save important information to memory. Supports two targets: "
        "'project' (writes to .catown/memory/ files, category: decision/convention/issue/context) "
        "or 'long_term' (writes to database for cross-project retention). "
        "Default is 'project' when project_id is provided."
    )

    async def execute(
        self,
        content: str,
        agent_id: Optional[int] = None,
        project_id: Optional[int] = None,
        importance: Optional[int] = None,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
        metadata: Optional[dict] = None,
        **kwargs,
    ) -> str:
        return await asyncio.to_thread(
            self._execute_sync, content, agent_id, project_id, importance, memory_type, category, metadata,
        )

    def _execute_sync(
        self,
        content: str,
        agent_id: Optional[int] = None,
        project_id: Optional[int] = None,
        importance: Optional[int] = None,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Save a memory to the appropriate layer.

        Args:
            content: The information to remember
            agent_id: Agent ID (optional, for long-term memory)
            project_id: Project ID (optional, for project memory)
            importance: Importance score 1-10 (default: 5)
            memory_type: 'project' or 'long_term' (auto-detected if not specified)
            category: For project memory: 'decision', 'convention', 'issue', 'context'
            metadata: Optional dict of extra metadata (e.g. screenshot paths, audit info)
        """
        try:
            # Determine target layer
            target = self._resolve_target(memory_type, project_id)

            if target == "project":
                return self._save_to_project(content, project_id, category, importance, metadata)
            else:
                return self._save_to_long_term(content, agent_id, importance, metadata)

        except Exception as e:
            return f"Error saving memory: {str(e)}"

    def _resolve_target(self, memory_type: Optional[str], project_id: Optional[int]) -> str:
        """Determine whether to save to project or long-term memory."""
        if memory_type in ("project", "short_term"):
            return "project"
        if memory_type == "long_term":
            return "long_term"
        # Default: if project_id is provided, save to project memory
        if project_id:
            return "project"
        return "long_term"

    def _save_to_project(
        self, content: str, project_id: Optional[int], category: Optional[str], importance: Optional[int],
        metadata: Optional[dict] = None,
    ) -> str:
        """Save to project memory files."""
        from services.project_memory import (
            write_decision, write_convention, write_issue, write_context,
            MemoryEntry, append_entry, _resolve_workspace,
        )
        import json as _json

        workspace = self._get_workspace(project_id)
        if not workspace:
            return "Error: No workspace found for project. Cannot save to project memory."

        cat = category or "context"
        imp = importance or 5
        agent_name = self._get_agent_name()

        # If metadata contains screenshot info, append it to content
        if metadata:
            screenshot_info = metadata.get("screenshot") or metadata.get("screenshot_path")
            if screenshot_info:
                content = f"{content}\n\n> 截图: {screenshot_info}"
            diff_info = metadata.get("diff") or metadata.get("diff_path")
            if diff_info:
                content = f"{content}\n> 差异图: {diff_info}"

        # Map category to writer function
        writers = {
            "decision": write_decision,
            "convention": write_convention,
            "issue": write_issue,
            "context": write_context,
        }

        writer = writers.get(cat, write_context)
        path = writer(workspace, content, source_agent=agent_name, importance=imp)

        return f"Memory saved to project ({cat}): {path}"

    def _save_to_long_term(self, content: str, agent_id: Optional[int], importance: Optional[int],
                           metadata: Optional[dict] = None) -> str:
        """Save to long-term database memory and vector store."""
        import json as _json
        from models.database import get_db, Memory

        db = next(get_db())
        try:
            metadata_json = _json.dumps(metadata or {}, ensure_ascii=False)
            memory = Memory(
                agent_id=agent_id or 0,
                memory_type="long_term",
                content=content,
                importance=importance or 5,
                metadata_json=metadata_json,
            )
            db.add(memory)
            db.commit()
            db.refresh(memory)

            # Also save to vector store (ChromaDB) for semantic search
            vector_status = ""
            try:
                from services.vector_memory import add_memory, is_available
                if is_available():
                    added = add_memory(
                        agent_id=agent_id or 0,
                        content=content,
                        memory_type="long_term",
                        importance=importance or 5,
                        source="save_memory",
                    )
                    if added:
                        vector_status = " + vector store"
            except Exception:
                pass  # Vector store is optional, don't fail if unavailable

            return f"Memory saved to long-term store (id={memory.id}, importance={memory.importance}{vector_status})"
        finally:
            db.close()

    def _get_workspace(self, project_id: Optional[int]) -> Optional[str]:
        """Resolve workspace path from project_id."""
        if not project_id:
            return None
        try:
            from models.database import get_db, Project
            db = next(get_db())
            try:
                project = db.query(Project).filter(Project.id == project_id).first()
                if project and project.workspace_path:
                    return project.workspace_path
            finally:
                db.close()
        except Exception:
            pass
        return None

    def _get_agent_name(self) -> str:
        """Get the current agent name from context if available."""
        # This is set by the tool execution context
        return getattr(self, '_current_agent_name', '')

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to save to memory"
                },
                "agent_id": {
                    "type": "integer",
                    "description": "Agent ID for long-term memory"
                },
                "project_id": {
                    "type": "integer",
                    "description": "Project ID for project memory"
                },
                "importance": {
                    "type": "integer",
                    "description": "Importance score 1-10 (default: 5)"
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["project", "long_term"],
                    "description": "Target memory layer: 'project' (file-based) or 'long_term' (database). Auto-detected from project_id if omitted."
                },
                "category": {
                    "type": "string",
                    "enum": ["decision", "convention", "issue", "context"],
                    "description": "For project memory: the category of memory (default: context)"
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional metadata dict. Supports screenshot/diff paths for audit logging: {\"screenshot\": \"path\", \"diff\": \"path\"}"
                },
            },
            "required": ["content"]
        }
