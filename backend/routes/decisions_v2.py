# -*- coding: utf-8 -*-
"""Decision-first v2 routes."""
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-decisions"])


class DecisionResolvePayload(BaseModel):
    resolution: str
    selected_option: Optional[str] = None
    note: Optional[str] = None


@router.get("/projects/{project_id}/decisions")
def list_project_decisions(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    return service.list_project_decision_payloads(project_id)


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    return service.build_decision_payload(decision_id)


@router.post("/decisions/{decision_id}/resolve")
def resolve_decision(decision_id: int, payload: DecisionResolvePayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    return service.build_resolve_decision_response(
        decision_id=decision_id,
        resolution=payload.resolution,
        selected_option=payload.selected_option,
        note=payload.note,
    )
