# -*- coding: utf-8 -*-
"""Shared event logging helpers for legacy and project-first runtimes."""
import json
from datetime import datetime
from typing import Any

from models.audit import Event


def append_event(
    db,
    *,
    event_type: str,
    summary: str,
    run_id: int | None = None,
    project_id: int | None = None,
    stage_run_id: int | None = None,
    asset_id: int | None = None,
    agent_name: str | None = None,
    stage_name: str | None = None,
    payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> Event:
    """Append a normalized audit event across old and new execution paths."""
    event = Event(
        run_id=run_id,
        project_id=project_id,
        stage_run_id=stage_run_id,
        asset_id=asset_id,
        event_type=event_type,
        agent_name=agent_name,
        stage_name=stage_name,
        summary=summary,
        payload=json.dumps(payload or {}, ensure_ascii=False),
        created_at=created_at or datetime.now(),
    )
    db.add(event)
    db.flush()
    return event
