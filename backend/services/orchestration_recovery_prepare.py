# -*- coding: utf-8 -*-
"""Shared preparation helpers for interrupted orchestration recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from services.orchestration_guards import RecoveryFailureOutcome, fail_recovery_guard


@dataclass(frozen=True)
class PreparedOrchestrationRecoveryContext:
    chatroom: Any
    project: Any
    agents: list[Any]
    agent_names: list[str]
    prepared_runtime: Any
    resolved_agents: list[Any]
    plan: Any
    orchestration_policy: Any
    lease_expires_at: datetime | None


def prepare_orchestration_recovery_context(
    *,
    db: Session,
    task_run: Any,
    task_run_id: int,
    recovery_owner: str,
    lease_expires_at: datetime | None,
    resolve_chatroom: Callable[[Session, int], Any],
    resolve_chatroom_project: Callable[[Session, Any], Any],
    serialize_project_agents: Callable[[Session, int], list[Any]],
    list_global_agents: Callable[[Session], list[Any]],
    recover_agent_names: Callable[[Any], list[str]],
    prepare_orchestration_runtime: Callable[..., Any],
) -> PreparedOrchestrationRecoveryContext | RecoveryFailureOutcome:
    """Resolve the prepared recovery runtime or return a normalized failure outcome."""

    chatroom = resolve_chatroom(db, getattr(task_run, "chatroom_id", None))
    if chatroom is None:
        return fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="chatroom_missing",
            owner=recovery_owner,
            lease_expires_at=lease_expires_at,
            payload={"task_run_id": getattr(task_run, "id", task_run_id)},
        )

    expected_chatroom_public_id = str(getattr(task_run, "chatroom_public_id", "") or "").strip()
    actual_chatroom_public_id = str(getattr(chatroom, "public_id", "") or "").strip()
    if not expected_chatroom_public_id or not actual_chatroom_public_id:
        return fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="chatroom_identity_mismatch",
            owner=recovery_owner,
            lease_expires_at=lease_expires_at,
            payload={
                "task_run_id": getattr(task_run, "id", task_run_id),
                "expected_chatroom_public_id": expected_chatroom_public_id or None,
                "actual_chatroom_public_id": actual_chatroom_public_id or None,
                "chatroom_id": getattr(chatroom, "id", None),
            },
        )
    if expected_chatroom_public_id != actual_chatroom_public_id:
        return fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="chatroom_identity_mismatch",
            owner=recovery_owner,
            lease_expires_at=lease_expires_at,
            payload={
                "task_run_id": getattr(task_run, "id", task_run_id),
                "expected_chatroom_public_id": expected_chatroom_public_id,
                "actual_chatroom_public_id": actual_chatroom_public_id,
                "chatroom_id": getattr(chatroom, "id", None),
            },
        )

    project = resolve_chatroom_project(db, chatroom)
    agents = serialize_project_agents(db, project.id) if project else list_global_agents(db)
    agent_names = recover_agent_names(task_run)
    prepared_runtime = prepare_orchestration_runtime(
        db=db,
        project=project,
        agents=agents,
        agent_names=agent_names,
        streaming=(getattr(task_run, "run_kind", None) == "multi_agent_orchestration_stream"),
    )
    resolved_agents = list(getattr(prepared_runtime, "resolved_agents", []) or [])
    if not resolved_agents:
        return fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="no_valid_agents",
            owner=recovery_owner,
            lease_expires_at=lease_expires_at,
            requested_agents=agent_names,
        )

    plan = getattr(prepared_runtime, "plan", None)
    orchestration_policy = getattr(prepared_runtime, "runner_policy", None)
    if plan is None or orchestration_policy is None:
        return fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="no_runnable_plan",
            owner=recovery_owner,
            lease_expires_at=lease_expires_at,
            requested_agents=agent_names,
        )

    return PreparedOrchestrationRecoveryContext(
        chatroom=chatroom,
        project=project,
        agents=agents,
        agent_names=agent_names,
        prepared_runtime=prepared_runtime,
        resolved_agents=resolved_agents,
        plan=plan,
        orchestration_policy=orchestration_policy,
        lease_expires_at=lease_expires_at,
    )
