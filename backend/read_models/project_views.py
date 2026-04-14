"""Read-model builders for project-first v2 dashboard and overview responses."""


class ProjectViewBuilder:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    def build_dashboard(self):
        project_rows = self.service.list_projects_v2()
        decisions = self.db.query(self.service.Decision).filter(self.service.Decision.status == "pending").order_by(self.service.Decision.created_at.desc()).all()
        assets = self.db.query(self.service.Asset).filter(self.service.Asset.is_current == True).order_by(self.service.Asset.updated_at.desc()).limit(10).all()
        stage_runs = self.db.query(self.service.StageRun).filter(self.service.StageRun.status.in_(["queued", "running", "waiting_for_decision"])).all()

        project_cards = [self._build_project_card(project) for project in project_rows]
        alerts = [
            {
                "kind": "pending_decision",
                "project_id": decision.project_id,
                "decision_id": decision.id,
                "decision_type": decision.decision_type,
                "title": decision.title,
            }
            for decision in decisions[:10]
        ]
        return {
            "projects": [self.service.serialize_project(project) for project in project_rows],
            "project_cards": project_cards,
            "pending_decisions": [self.service.serialize_decision(decision) for decision in decisions],
            "recent_assets": [self.service.serialize_asset(asset) for asset in assets],
            "active_stage_runs": [self.service.serialize_stage_run(stage_run) for stage_run in stage_runs],
            "summary": {
                "project_count": len(project_rows),
                "pending_decision_count": len(decisions),
                "active_stage_run_count": len(stage_runs),
            },
            "alerts": alerts,
        }

    def build_project_overview(self, project_id: int):
        project = self.service.get_project_or_404(project_id)
        assets = (
            self.db.query(self.service.Asset)
            .filter(self.service.Asset.project_id == project_id, self.service.Asset.is_current == True)
            .order_by(self.service.Asset.updated_at.desc())
            .all()
        )
        decisions = (
            self.db.query(self.service.Decision)
            .filter(self.service.Decision.project_id == project_id)
            .order_by(self.service.Decision.created_at.desc())
            .all()
        )
        stage_runs = (
            self.db.query(self.service.StageRun)
            .filter(self.service.StageRun.project_id == project_id)
            .order_by(self.service.StageRun.created_at.desc())
            .all()
        )

        current_stage_run = stage_runs[0] if stage_runs else None
        pending_decisions = [decision for decision in decisions if decision.status == "pending"]
        assets_by_type = {asset.asset_type: self.service.serialize_asset(asset) for asset in assets}
        return {
            "project": self.service.serialize_project(project),
            "current_stage_run": self.service.serialize_stage_run(current_stage_run) if current_stage_run else None,
            "key_assets": [self.service.serialize_asset(asset) for asset in assets],
            "assets_by_type": assets_by_type,
            "pending_decisions": [self.service.serialize_decision(decision) for decision in pending_decisions],
            "decision_history": [self.service.serialize_decision(decision) for decision in decisions[:10]],
            "stage_summary": self._build_stage_summary(stage_runs),
            "stage_asset_links": self.service._serialize_stage_asset_links(project_id),
            "decision_asset_links": self.service._serialize_decision_asset_links(project_id),
            "open_tasks_summary": {"count": 0},
            "recent_activity": [self.service.serialize_stage_run(stage_run) for stage_run in stage_runs[:5]],
            "release_readiness": self.service._build_release_readiness(assets_by_type, decisions),
            "recommended_next_action": self._recommended_next_action(current_stage_run, pending_decisions, assets_by_type),
        }

    def _build_project_card(self, project):
        project_decisions = self.db.query(self.service.Decision).filter(
            self.service.Decision.project_id == project.id,
            self.service.Decision.status == "pending",
        ).all()
        current_assets = self.db.query(self.service.Asset).filter(
            self.service.Asset.project_id == project.id,
            self.service.Asset.is_current == True,
        ).all()
        current_stage_run = self.db.query(self.service.StageRun).filter(
            self.service.StageRun.project_id == project.id
        ).order_by(self.service.StageRun.created_at.desc()).first()
        asset_types = sorted(asset.asset_type for asset in current_assets)
        release_readiness = self.service._build_release_readiness(
            {asset.asset_type: self.service.serialize_asset(asset) for asset in current_assets},
            project_decisions,
        )
        return {
            "project": self.service.serialize_project(project),
            "current_stage_run": self.service.serialize_stage_run(current_stage_run) if current_stage_run else None,
            "pending_decision_count": len(project_decisions),
            "asset_types": asset_types,
            "release_readiness": release_readiness,
            "recommended_next_action": (
                self.service._recommended_decision_action(project_decisions[0].decision_type)
                if project_decisions else ("continue_project" if current_stage_run and current_stage_run.status == "queued" else "review_project")
            ),
        }

    def _build_stage_summary(self, stage_runs):
        return {
            "total": len(stage_runs),
            "completed": len([run for run in stage_runs if run.status == "completed"]),
            "active": len([run for run in stage_runs if run.status in {"queued", "running", "waiting_for_decision"}]),
            "latest_completed_stage": next((run.stage_type for run in stage_runs if run.status == "completed"), None),
        }

    def _recommended_next_action(self, current_stage_run, pending_decisions, assets_by_type):
        if pending_decisions:
            return self.service._recommended_decision_action(pending_decisions[0].decision_type)
        if current_stage_run and current_stage_run.status == "queued":
            return "continue_project"
        if current_stage_run and current_stage_run.status in {"running", "waiting_for_decision"}:
            return "review_current_stage"
        if current_stage_run and current_stage_run.status == "completed" and "release_pack" in assets_by_type:
            return "review_release_pack"
        if current_stage_run and current_stage_run.status == "completed" and "test_report" in assets_by_type:
            return "review_test_report"
        if current_stage_run and current_stage_run.status == "completed" and "task_plan" in assets_by_type:
            return "review_task_plan"
        if current_stage_run and current_stage_run.status == "completed" and {"prd", "ux_blueprint", "tech_spec"}.issubset(set(assets_by_type.keys())):
            return "review_definition_bundle"
        if current_stage_run and current_stage_run.status == "completed" and "prd" in assets_by_type:
            return "review_prd"
        return "continue_project"
