# -*- coding: utf-8 -*-
"""Asset-focused service helpers for the project-first v2 flow."""
import json
from datetime import datetime
from typing import Any

from models.database import Asset, AssetLink, Project, StageRun


class AssetService:
    def __init__(self, db, project_service):
        self.db = db
        self.project_service = project_service

    def get_current_asset(self, project_id: int, asset_type: str) -> Asset | None:
        return (
            self.db.query(Asset)
            .filter(Asset.project_id == project_id, Asset.asset_type == asset_type, Asset.is_current == True)
            .first()
        )

    def build_source_refs(self, project_id: int, asset_types: list[str]) -> list[dict[str, Any]]:
        source_refs = []
        for asset_type in asset_types:
            asset = self.get_current_asset(project_id, asset_type)
            if asset:
                source_refs.append({"asset_id": asset.id, "asset_type": asset.asset_type})
        return source_refs

    def replace_current_asset(
        self,
        project: Project,
        asset_type: str,
        title: str,
        summary: str,
        content_json: dict[str, Any],
        content_markdown: str,
        owner_agent: str,
        stage_run: StageRun,
        source_refs: list[dict[str, Any]],
        now: datetime,
    ) -> Asset:
        existing = self.get_current_asset(project.id, asset_type)
        version = 1
        supersedes_asset_id = None
        if existing:
            existing.is_current = False
            existing.status = "superseded"
            version = existing.version + 1
            supersedes_asset_id = existing.id

        asset = Asset(
            project_id=project.id,
            asset_type=asset_type,
            title=title,
            summary=summary,
            content_json=json.dumps(content_json, ensure_ascii=False),
            content_markdown=content_markdown,
            version=version,
            status="draft",
            is_current=True,
            owner_agent=owner_agent,
            produced_by_stage_run_id=stage_run.id,
            supersedes_asset_id=supersedes_asset_id,
            source_input_refs_json=json.dumps(source_refs, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        self.db.add(asset)
        self.db.flush()
        self.project_service._link_stage_run_asset(stage_run.id, asset.id, "output", now)
        for ref in source_refs:
            if ref.get("asset_id"):
                self.link_dependency(project.id, ref["asset_id"], asset.id, now)
        return asset

    def link_dependency(self, project_id: int, from_asset_id: int, to_asset_id: int, now: datetime) -> None:
        existing = self.db.query(AssetLink).filter(
            AssetLink.project_id == project_id,
            AssetLink.from_asset_id == from_asset_id,
            AssetLink.to_asset_id == to_asset_id,
            AssetLink.relation_type == "derived_from",
        ).first()
        if not existing:
            self.db.add(
                AssetLink(
                    project_id=project_id,
                    from_asset_id=from_asset_id,
                    to_asset_id=to_asset_id,
                    relation_type="derived_from",
                    created_at=now,
                )
            )

    def set_asset_status(self, asset: Asset | None, status: str, now: datetime, approved: bool = False) -> None:
        if not asset:
            return
        asset.status = status
        asset.updated_at = now
        if approved:
            asset.approved_at = now
