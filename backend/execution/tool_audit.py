# -*- coding: utf-8 -*-
"""Shared tool-call audit helpers for legacy and project-first runtimes."""
import json
from datetime import datetime

from models.audit import ToolCall


def append_tool_call(
    db,
    *,
    agent_name: str,
    tool_name: str,
    arguments: dict,
    result_summary: str,
    result_length: int,
    success: bool,
    duration_ms: int,
    llm_call_id: int | None = None,
    run_id: int | None = None,
    stage_id: int | None = None,
) -> ToolCall:
    """Append a normalized tool-call audit row."""
    record = ToolCall(
        llm_call_id=llm_call_id,
        run_id=run_id,
        stage_id=stage_id,
        agent_name=agent_name,
        tool_name=tool_name,
        arguments=json.dumps(arguments, ensure_ascii=False)[:50000],
        result_summary=result_summary[:500],
        result_length=result_length,
        success=success,
        duration_ms=duration_ms,
        created_at=datetime.now(),
    )
    db.add(record)
    db.flush()
    return record
