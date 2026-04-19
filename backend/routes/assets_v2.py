# -*- coding: utf-8 -*-
"""Asset-first v2 routes."""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from models.database import Asset, get_db
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
    return service.build_asset_detail(asset_id)
