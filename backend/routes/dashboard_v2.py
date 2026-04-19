# -*- coding: utf-8 -*-
"""Dashboard-oriented v2 aggregate routes."""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from models.database import get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-dashboard"])


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    return service.build_dashboard()


@router.get("/projects/{project_id}/overview")
def get_project_overview(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    return service.build_project_overview(project_id)
