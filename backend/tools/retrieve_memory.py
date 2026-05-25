# -*- coding: utf-8 -*-
"""
Retrieve Memory Tool — three-layer memory retrieval.

1. Short-term memory (current stage/session) — in-memory
2. Project memory (.catown/memory/) — file-based markdown
3. Long-term memory (memories table) — database

Design follows PRD §4.6 three-layer memory architecture.
"""
from .base import BaseTool
from typing import Optional


class RetrieveMemoryTool(BaseTool):
    """Tool for retrieving agent memories across all three memory layers."""

    name = "retrieve_memory"
    description = (
        "Retrieve relevant memories from the agent's three-layer memory system. "
        "Searches: (1) current session context, (2) project memory files, "
        "(3) long-term database memories. Use this to recall decisions, "
        "conventions, issues, and learned patterns."
    )

    async def execute(
        self,
        query: str,
        agent_id: Optional[int] = None,
        project_id: Optional[int] = None,
        scope: Optional[str] = None,
        **kwargs,
    ) -> str:
        return self._execute_sync(query, agent_id, project_id, scope)

    def _execute_sync(
        self,
        query: str,
        agent_id: Optional[int] = None,
        project_id: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> str:
        """Retrieve memories from all three layers.

        Args:
            query: Query string to search memories
            agent_id: Agent ID for long-term memory filter
            project_id: Project ID to locate project memory
            scope: Optional scope hint: "short_term", "project", "long_term", or None (all)
        """
        try:
            results = []
            scopes_to_search = self._resolve_scopes(scope)

            # --- Layer 1: Short-term memory (current stage) ---
            if "short_term" in scopes_to_search:
                st_results = self._search_short_term(query, project_id)
                if st_results:
                    results.append(st_results)

            # --- Layer 2: Project memory (.catown/memory/) ---
            if "project" in scopes_to_search:
                pm_results = self._search_project_memory(query, project_id)
                if pm_results:
                    results.append(pm_results)

            # --- Layer 3: Long-term memory (database) ---
            if "long_term" in scopes_to_search:
                lt_results = self._search_long_term(query, agent_id)
                if lt_results:
                    results.append(lt_results)

            if results:
                return "\n\n".join(results)
            return f"[Memory] No relevant memories found for '{query}'."

        except Exception as e:
            return f"[Memory] Error retrieving memories: {str(e)}"

    def _resolve_scopes(self, scope: Optional[str]) -> list:
        """Determine which memory layers to search."""
        all_scopes = ["short_term", "project", "long_term"]
        if scope is None:
            return all_scopes
        scope_map = {
            "short_term": ["short_term"],
            "project": ["project"],
            "long_term": ["long_term"],
            "session": ["short_term"],
            "stage": ["short_term"],
        }
        return scope_map.get(scope, all_scopes)

    def _search_short_term(self, query: str, project_id: Optional[int]) -> str:
        """Search short-term memory (current active stages)."""
        try:
            from services.short_term_memory import get_short_term_store, format_recent_turns_for_context

            store = get_short_term_store()
            if not store.active_stages:
                return ""

            query_lower = query.lower()
            matching = []
            for stage_id in store.active_stages:
                mem = store.get(stage_id)
                if not mem:
                    continue
                # Search turns
                for turn in mem.turns:
                    if (query_lower in turn.user_message.lower() or
                            query_lower in turn.agent_response.lower()):
                        matching.append(f"[Stage: {stage_id}] Turn {turn.turn_index}: "
                                        f"{turn.agent_response[:200]}")
                # Search decisions/issues
                for dec in mem.key_decisions:
                    if query_lower in dec.lower():
                        matching.append(f"[Stage: {stage_id}] Decision: {dec}")
                for issue in mem.issues_encountered:
                    if query_lower in issue.lower():
                        matching.append(f"[Stage: {stage_id}] Issue: {issue}")

            if matching:
                return "**Current session memories:**\n" + "\n".join(f"- {m}" for m in matching[:5])
            return ""
        except Exception:
            return ""

    def _search_project_memory(self, query: str, project_id: Optional[int]) -> str:
        """Search project memory files (.catown/memory/)."""
        try:
            from services.project_memory import search_memory, _resolve_workspace

            workspace = self._get_workspace(project_id)
            if not workspace:
                return ""

            results = search_memory(workspace, query, max_results=5)
            if results:
                lines = ["**Project memory:**"]
                for r in results:
                    lines.append(
                        f"- [{r['category_label']}] {r['content'][:200]}"
                    )
                return "\n".join(lines)
            return ""
        except Exception:
            return ""

    def _search_long_term(self, query: str, agent_id: Optional[int]) -> str:
        """Search long-term database memories."""
        try:
            from models.database import get_db, Message, Agent, Memory
            from sqlalchemy import or_

            db = next(get_db())
            try:
                results = []

                # Search Memory table
                mem_query = db.query(Memory).filter(Memory.content.contains(query))
                if agent_id is not None:
                    mem_query = mem_query.filter(or_(Memory.agent_id == agent_id))

                memories = mem_query.order_by(
                    Memory.importance.desc(), Memory.created_at.desc()
                ).limit(5).all()

                # Build agent name map
                agent_map: dict = {}
                if memories:
                    agent_ids = {m.agent_id for m in memories if m.agent_id}
                    agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
                    agent_map = {a.id: getattr(a, 'name', None) for a in agents}

                if memories:
                    results.append(f"**Long-term memories ({len(memories)}):**")
                    for m in memories:
                        who = agent_map.get(m.agent_id, "system")
                        ts = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "?"
                        results.append(f"- [{ts}] [{who}] [imp={m.importance}] {m.content[:200]}")

                # Also search recent messages
                msg_query = db.query(Message).filter(Message.content.contains(query))
                if agent_id is not None:
                    msg_query = msg_query.filter(or_(Message.agent_id == agent_id))

                messages = msg_query.order_by(Message.created_at.desc()).limit(3).all()
                if messages:
                    msg_agent_ids = {m.agent_id for m in messages if m.agent_id}
                    missing = msg_agent_ids - set(agent_map.keys())
                    if missing:
                        more = db.query(Agent).filter(Agent.id.in_(missing)).all()
                        agent_map.update({a.id: getattr(a, 'name', None) for a in more})

                    results.append(f"\n**Past messages ({len(messages)}):**")
                    for msg in messages:
                        who = agent_map.get(msg.agent_id, "user")
                        ts = msg.created_at.strftime("%Y-%m-%d %H:%M") if msg.created_at else "?"
                        preview = msg.content[:150] + "..." if len(msg.content) > 150 else msg.content
                        results.append(f"- [{ts}] {who}: {preview}")

                return "\n".join(results) if results else ""
            finally:
                db.close()
        except Exception:
            return ""

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

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query string to search memories"
                },
                "agent_id": {
                    "type": "integer",
                    "description": "Optional agent ID to search specific agent's long-term memories"
                },
                "project_id": {
                    "type": "integer",
                    "description": "Project ID to search project-specific memory"
                },
                "scope": {
                    "type": "string",
                    "enum": ["short_term", "project", "long_term", "session", "stage"],
                    "description": "Optional scope: 'short_term' (current session), 'project' (project files), 'long_term' (database), or omit for all"
                },
            },
            "required": ["query"]
        }
