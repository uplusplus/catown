# -*- coding: utf-8 -*-
"""Stage-run v2 routes."""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-stage-runs"])


class StageRunInstructionPayload(BaseModel):
    content: str
    author: str = "boss"


@router.get("/projects/{project_id}/stage-runs")
def list_stage_runs(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    return service.list_project_stage_runs(project_id)


@router.get("/stage-runs/{stage_run_id}")
def get_stage_run(stage_run_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    return service.build_stage_run_detail(stage_run_id)


@router.get("/stage-runs/{stage_run_id}/events")
def list_stage_run_events(stage_run_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    return service.build_stage_run_events(stage_run_id)


@router.post("/stage-runs/{stage_run_id}/instructions")
def add_stage_run_instruction(
    stage_run_id: int,
    payload: StageRunInstructionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service = ProjectService(db)
    event = service.add_stage_run_instruction(stage_run_id, payload.content, payload.author)
    db.commit()
    return {"event": service.serialize_event(event)}
