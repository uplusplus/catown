# -*- coding: utf-8 -*-
"""Decision-first v2 routes."""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import Decision, get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-decisions"])


class DecisionResolvePayload(BaseModel):
    resolution: str
    selected_option: Optional[str] = None
    note: Optional[str] = None


@router.get("/projects/{project_id}/decisions")
def list_project_decisions(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    service.get_project_or_404(project_id)
    decisions = db.query(Decision).filter(Decision.project_id == project_id).order_by(Decision.created_at.desc()).all()
    return [service.serialize_decision(decision) for decision in decisions]


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    decision = db.query(Decision).filter(Decision.id == decision_id).first()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    return service.serialize_decision(decision)


@router.post("/decisions/{decision_id}/resolve")
def resolve_decision(decision_id: int, payload: DecisionResolvePayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    project, decision = service.resolve_decision(
        decision_id=decision_id,
        resolution=payload.resolution,
        selected_option=payload.selected_option,
        note=payload.note,
    )
    return {
        "project": service.serialize_project(project),
        "decision": service.serialize_decision(decision),
    }
