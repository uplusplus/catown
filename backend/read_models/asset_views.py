"""Read-model builders for asset detail and dependency views."""

from fastapi import HTTPException

from models.database import Asset, AssetLink, DecisionAsset, StageRunAsset


class AssetViewBuilder:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    def build_asset_detail(self, asset_id: int):
        asset = self.db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        payload = self.service.serialize_asset(asset)
        stage_links = self.db.query(StageRunAsset).filter(StageRunAsset.asset_id == asset_id).all()
        decision_links = self.db.query(DecisionAsset).filter(DecisionAsset.asset_id == asset_id).all()
        payload["stage_links"] = [
            {"stage_run_id": link.stage_run_id, "direction": link.direction}
            for link in stage_links
        ]
        payload["decision_links"] = [
            {"decision_id": link.decision_id, "relation_role": link.relation_role}
            for link in decision_links
        ]
        return payload

    def serialize_asset_dependencies(self, asset_id: int):
        upstream_rows = (
            self.db.query(AssetLink, Asset)
            .join(Asset, AssetLink.from_asset_id == Asset.id)
            .filter(AssetLink.to_asset_id == asset_id)
            .all()
        )
        downstream_rows = (
            self.db.query(AssetLink, Asset)
            .join(Asset, AssetLink.to_asset_id == Asset.id)
            .filter(AssetLink.from_asset_id == asset_id)
            .all()
        )
        return {
            "upstream": [
                {"asset_id": asset.id, "asset_type": asset.asset_type, "relation_type": link.relation_type}
                for link, asset in upstream_rows
            ],
            "downstream": [
                {"asset_id": asset.id, "asset_type": asset.asset_type, "relation_type": link.relation_type}
                for link, asset in downstream_rows
            ],
        }
