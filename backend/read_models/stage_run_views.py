"""Read-model builder for v2 stage-run detail responses."""

from fastapi import HTTPException

from models.database import Asset, Decision, StageRun, StageRunAsset


class StageRunViewBuilder:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    def build_stage_run_detail(self, stage_run_id: int):
        stage_run = self.db.query(StageRun).filter(StageRun.id == stage_run_id).first()
        if not stage_run:
            raise HTTPException(status_code=404, detail="Stage run not found")

        project = self.service.get_project_or_404(stage_run.project_id)
        link_rows = (
            self.db.query(StageRunAsset, Asset)
            .join(Asset, StageRunAsset.asset_id == Asset.id)
            .filter(StageRunAsset.stage_run_id == stage_run_id)
            .all()
        )
        decision_rows = (
            self.db.query(Decision)
            .filter(Decision.stage_run_id == stage_run_id)
            .order_by(Decision.created_at.desc())
            .all()
        )
        event_rows = self.service.list_stage_run_events(stage_run_id)

        input_assets = []
        output_assets = []
        for link, asset in link_rows:
            payload = self.service.serialize_asset(asset)
            payload["direction"] = link.direction
            if link.direction == "input":
                input_assets.append(payload)
            else:
                output_assets.append(payload)

        return {
            "stage_run": self.service.serialize_stage_run(stage_run),
            "project": self.service.serialize_project(project),
            "input_assets": input_assets,
            "output_assets": output_assets,
            "decisions": [self.service.serialize_decision(decision) for decision in decision_rows],
            "events": [self.service.serialize_event(event) for event in event_rows],
            "summary": {
                "input_count": len(input_assets),
                "output_count": len(output_assets),
                "decision_count": len(decision_rows),
                "event_count": len(event_rows),
            },
        }
