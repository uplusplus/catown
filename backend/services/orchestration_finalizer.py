# -*- coding: utf-8 -*-
"""Shared finalization helpers for orchestration task runs."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.orchestration_handoffs import compact_runtime_text
from services.run_ledger import append_task_event, complete_task_run


def summarize_orchestration_result(
    *,
    last_blocking_result: str = "",
    completed_turns: List[Dict[str, str]] | None = None,
    results: List[Dict[str, str]] | None = None,
    fallback: str,
    limit: int = 280,
) -> str:
    """Choose the final human-readable summary for an orchestration run."""

    completed_turns = completed_turns or []
    results = results or []
    summary = (last_blocking_result or "").strip()
    if not summary and results:
        summary = str(results[-1].get("content") or "").strip()
    if not summary and completed_turns:
        summary = str(completed_turns[-1].get("content") or "").strip()
    return compact_runtime_text(summary or fallback, limit=limit)


def finalize_orchestration_task_run(
    db: Session,
    task_run: TaskRun | None,
    *,
    last_blocking_result: str = "",
    completed_turns: List[Dict[str, str]] | None = None,
    results: List[Dict[str, str]] | None = None,
    fallback: str,
    status: str = "completed",
) -> TaskRun | None:
    """Complete an orchestration task run using the shared summary selection rule."""

    summary = summarize_orchestration_result(
        last_blocking_result=last_blocking_result,
        completed_turns=completed_turns,
        results=results,
        fallback=fallback,
    )
    return complete_task_run(db, task_run, status=status, summary=summary)


def fail_orchestration_task_run(
    db: Session,
    task_run: TaskRun | None,
    *,
    summary: str,
    event_type: str = "task_run_failed",
    agent_name: str | None = None,
    event_summary: str | None = None,
    payload: Any = None,
    limit: int = 280,
) -> TaskRun | None:
    """Record a terminal orchestration failure event and close the task run."""

    compact_summary = compact_runtime_text(summary or "Orchestration failed.", limit=limit)
    append_task_event(
        db,
        task_run,
        event_type,
        agent_name=agent_name,
        summary=event_summary or compact_summary,
        payload=payload,
    )
    return complete_task_run(db, task_run, status="failed", summary=compact_summary)
