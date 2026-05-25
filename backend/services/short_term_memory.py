# -*- coding: utf-8 -*-
"""
Short-Term Memory Service — session/stage-level memory.

Lives in-memory during a chat turn or pipeline stage, with optional
JSON persistence to ``{workspace}/.catown/stages/{stage_id}/history.json``.

At stage end, the orchestrator calls ``archive_to_project_memory()`` to
extract key information and persist it into the project memory layer.

Design follows PRD §4.6.1.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catown.short_term_memory")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TurnRecord:
    """One conversation turn (user message + agent response + tool calls)."""
    turn_index: int
    user_message: str
    agent_response: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: int = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TurnRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StageMemory:
    """Accumulated memory for one pipeline stage or chat session."""
    stage_id: str
    agent_name: str
    turns: List[TurnRecord] = field(default_factory=list)
    key_decisions: List[str] = field(default_factory=list)
    issues_encountered: List[str] = field(default_factory=list)
    artifacts_produced: List[str] = field(default_factory=list)
    started_at: str = ""

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now().isoformat()

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def total_tool_calls(self) -> int:
        return sum(len(t.tool_calls) for t in self.turns)

    def add_turn(self, turn: TurnRecord):
        self.turns.append(turn)

    def add_decision(self, decision: str):
        if decision and decision not in self.key_decisions:
            self.key_decisions.append(decision)

    def add_issue(self, issue: str):
        if issue and issue not in self.issues_encountered:
            self.issues_encountered.append(issue)

    def add_artifact(self, artifact: str):
        if artifact and artifact not in self.artifacts_produced:
            self.artifacts_produced.append(artifact)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "agent_name": self.agent_name,
            "turns": [t.to_dict() for t in self.turns],
            "key_decisions": self.key_decisions,
            "issues_encountered": self.issues_encountered,
            "artifacts_produced": self.artifacts_produced,
            "started_at": self.started_at,
            "summary": {
                "turn_count": self.turn_count,
                "total_tool_calls": self.total_tool_calls,
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StageMemory":
        turns = [TurnRecord.from_dict(t) for t in d.get("turns", [])]
        obj = cls(
            stage_id=d.get("stage_id", ""),
            agent_name=d.get("agent_name", ""),
            turns=turns,
            key_decisions=d.get("key_decisions", []),
            issues_encountered=d.get("issues_encountered", []),
            artifacts_produced=d.get("artifacts_produced", []),
            started_at=d.get("started_at", ""),
        )
        return obj


# ---------------------------------------------------------------------------
# In-memory store (per process)
# ---------------------------------------------------------------------------

class ShortTermMemoryStore:
    """Manages short-term memory instances for active stages/sessions.

    Thread-safe for single-process FastAPI (the current deployment model).
    """

    def __init__(self):
        self._active: Dict[str, StageMemory] = {}

    def get_or_create(self, stage_id: str, agent_name: str = "") -> StageMemory:
        """Get existing stage memory or create a new one."""
        if stage_id not in self._active:
            self._active[stage_id] = StageMemory(
                stage_id=stage_id, agent_name=agent_name,
            )
        return self._active[stage_id]

    def get(self, stage_id: str) -> Optional[StageMemory]:
        return self._active.get(stage_id)

    def remove(self, stage_id: str) -> Optional[StageMemory]:
        return self._active.pop(stage_id, None)

    @property
    def active_stages(self) -> List[str]:
        return list(self._active.keys())

    def record_turn(
        self,
        stage_id: str,
        agent_name: str,
        user_message: str,
        agent_response: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        duration_ms: int = 0,
    ) -> TurnRecord:
        """Record a conversation turn into the stage's short-term memory."""
        mem = self.get_or_create(stage_id, agent_name)
        turn = TurnRecord(
            turn_index=len(mem.turns),
            user_message=user_message[:2000],  # truncate for memory
            agent_response=agent_response[:3000],
            tool_calls=tool_calls or [],
            duration_ms=duration_ms,
        )
        mem.add_turn(turn)
        return turn


# Global singleton
_store = ShortTermMemoryStore()


def get_short_term_store() -> ShortTermMemoryStore:
    return _store


# ---------------------------------------------------------------------------
# Persistence (JSON to .catown/stages/)
# ---------------------------------------------------------------------------

def _stage_history_path(workspace: str, stage_id: str) -> Path:
    return Path(workspace) / ".catown" / "stages" / stage_id / "history.json"


def persist_stage_memory(workspace: str, mem: StageMemory) -> Optional[str]:
    """Persist a stage's short-term memory to disk.

    Returns the file path, or None if workspace is not set.
    """
    if not workspace:
        return None
    fpath = _stage_history_path(workspace, mem.stage_id)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(mem.to_dict(), f, ensure_ascii=False, indent=2)
    logger.info(
        "[ShortTerm] Persisted stage %s: %d turns, %d tool calls → %s",
        mem.stage_id, mem.turn_count, mem.total_tool_calls, fpath,
    )
    return str(fpath)


def load_stage_memory(workspace: str, stage_id: str) -> Optional[StageMemory]:
    """Load a persisted stage memory from disk."""
    fpath = _stage_history_path(workspace, stage_id)
    if not fpath.exists():
        return None
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return StageMemory.from_dict(data)


# ---------------------------------------------------------------------------
# Archive: stage → project memory
# ---------------------------------------------------------------------------

def archive_to_project_memory(
    workspace: str,
    mem: StageMemory,
    project_memory_module: Any = None,
) -> Dict[str, str]:
    """Archive short-term memory to project memory at stage end.

    This is the bridge between session-level and project-level memory.
    Extracts key information and writes to .catown/memory/ files.

    Returns paths of written project memory files.
    """
    if project_memory_module is None:
        from services import project_memory as pm
        project_memory_module = pm

    # Persist the full history first
    persist_stage_memory(workspace, mem)

    # Build a summary for project memory
    summary_parts = []

    if mem.key_decisions:
        summary_parts.append("关键决策:\n" + "\n".join(f"- {d}" for d in mem.key_decisions))

    if mem.issues_encountered:
        summary_parts.append("遇到的问题:\n" + "\n".join(f"- {i}" for i in mem.issues_encountered))

    if mem.artifacts_produced:
        summary_parts.append("产出物:\n" + "\n".join(f"- {a}" for a in mem.artifacts_produced))

    # Add turn summary
    summary_parts.append(
        f"执行摘要: {mem.turn_count} 轮对话, {mem.total_tool_calls} 次工具调用"
    )

    summary_text = "\n\n".join(summary_parts)
    if not summary_text.strip():
        return {}

    return project_memory_module.summarize_stage_to_project_memory(
        workspace=workspace,
        stage_name=mem.stage_id,
        agent_name=mem.agent_name,
        summary_text=summary_text,
        artifacts=mem.artifacts_produced,
        decisions=mem.key_decisions,
    )


# ---------------------------------------------------------------------------
# Context injection helpers
# ---------------------------------------------------------------------------

def format_recent_turns_for_context(mem: StageMemory, max_turns: int = 3) -> str:
    """Format the most recent turns for injection into LLM context."""
    if not mem.turns:
        return ""

    recent = mem.turns[-max_turns:]
    parts = []
    for turn in recent:
        part = f"[Turn {turn.turn_index}] User: {turn.user_message[:300]}"
        if turn.tool_calls:
            tools = ", ".join(tc.get("name", "?") for tc in turn.tool_calls)
            part += f"\n  Tools used: {tools}"
        part += f"\n  Agent: {turn.agent_response[:500]}"
        parts.append(part)

    return "\n\n".join(parts)


def get_stage_context_summary(mem: StageMemory) -> str:
    """Get a compact summary of the stage so far, for context injection."""
    parts = [f"Stage: {mem.stage_id} ({mem.agent_name})"]
    parts.append(f"Turns: {mem.turn_count}, Tool calls: {mem.total_tool_calls}")

    if mem.key_decisions:
        parts.append("Decisions: " + "; ".join(mem.key_decisions[:3]))
    if mem.issues_encountered:
        parts.append("Issues: " + "; ".join(mem.issues_encountered[:3]))
    if mem.artifacts_produced:
        parts.append("Artifacts: " + ", ".join(mem.artifacts_produced[:5]))

    return " | ".join(parts)
