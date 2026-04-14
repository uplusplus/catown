# -*- coding: utf-8 -*-
"""Stage-run v2 routes."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models.database import StageRun, get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-stage-runs"])


@router.get("/projects/{project_id}/stage-runs")
def list_stage_runs(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    service.get_project_or_404(project_id)
    stage_runs = db.query(StageRun).filter(StageRun.project_id == project_id).order_by(StageRun.created_at.desc()).all()
    return [service.serialize_stage_run(stage_run) for stage_run in stage_runs]


@router.get("/stage-runs/{stage_run_id}")
def get_stage_run(stage_run_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    return service.build_stage_run_detail(stage_run_id)
