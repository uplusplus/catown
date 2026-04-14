# -*- coding: utf-8 -*-
"""Asset-first v2 routes."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models.database import Asset, DecisionAsset, StageRunAsset, get_db
from services.project_service import ProjectService

router = APIRouter(prefix="/api/v2", tags=["v2-assets"])


@router.get("/projects/{project_id}/assets")
def list_project_assets(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = ProjectService(db)
    service.get_project_or_404(project_id)
    assets = db.query(Asset).filter(Asset.project_id == project_id).order_by(Asset.asset_type, Asset.version.desc()).all()
    return [service.serialize_asset(asset) for asset in assets]


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ProjectService(db)
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    stage_links = db.query(StageRunAsset).filter(StageRunAsset.asset_id == asset_id).all()
    decision_links = db.query(DecisionAsset).filter(DecisionAsset.asset_id == asset_id).all()
    payload = service.serialize_asset(asset)
    payload["stage_links"] = [
        {"stage_run_id": link.stage_run_id, "direction": link.direction}
        for link in stage_links
    ]
    payload["decision_links"] = [
        {"decision_id": link.decision_id, "relation_role": link.relation_role}
        for link in decision_links
    ]
    return payload
