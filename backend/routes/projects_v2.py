# -*- coding: utf-8 -*-
"""Project-first v2 routes."""
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-projects"])


class ProjectCreateV2(BaseModel):
    name: str
    one_line_vision: str
    description: Optional[str] = ""
    target_platforms: list[str] = Field(default_factory=list)
    target_users: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    execution_mode: str = "autopilot"


class ProjectPatchV2(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    one_line_vision: Optional[str] = None
    execution_mode: Optional[str] = None
    target_platforms: Optional[list[str]] = None
    target_users: Optional[list[str]] = None
    references: Optional[list[str]] = None


@router.post("/projects")
def create_project(payload: ProjectCreateV2, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    project = service.create_project(
        name=payload.name,
        one_line_vision=payload.one_line_vision,
        description=payload.description or "",
        target_platforms=payload.target_platforms,
        target_users=payload.target_users,
        references=payload.references,
        execution_mode=payload.execution_mode,
    )
    return {"project": service.serialize_project(project), "next_action": "review_scope_confirmation"}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    return [service.serialize_project(project) for project in service.list_projects_v2()]


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    project = service.get_project_or_404(project_id)
    return service.serialize_project(project)


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, payload: ProjectPatchV2, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    project = service.update_project(project_id, payload.model_dump(exclude_unset=True))
    return service.serialize_project(project)


@router.post("/projects/{project_id}/continue")
def continue_project(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    project = service.get_project_or_404(project_id)
    pending = service.get_pending_decisions(project_id)
    if pending:
        raise HTTPException(status_code=409, detail="Project has pending decisions and cannot continue yet")
    return {"project": service.serialize_project(project), "message": "No automatic continuation implemented yet for the v2 bootstrap path"}
