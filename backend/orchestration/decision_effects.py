"""Decision side-effect handlers for the v2 project-first flow."""
from datetime import datetime

from models.database import Asset, Decision, Project


class DecisionEffectsCoordinator:
    """Applies project/asset side effects after a decision is resolved."""

    def __init__(self, service):
        self.service = service
        self.db = service.db

    def apply(self, project: Project, decision: Decision, resolution: str, now: datetime) -> None:
        if decision.decision_type == "scope_confirmation":
            self._apply_scope_confirmation(project, decision, resolution, now)
        elif decision.decision_type == "release_approval":
            self._apply_release_approval(project, decision, resolution, now)

    def _apply_scope_confirmation(self, project: Project, decision: Decision, resolution: str, now: datetime) -> None:
        brief = self.db.query(Asset).filter(Asset.approval_decision_id == decision.id).first()
        if resolution == "approved":
            if brief:
                brief.status = "approved"
                brief.approved_at = now
                self.service._link_decision_asset(decision.id, brief.id, "approval_target", now)
            project.status = "brief_confirmed"
            project.current_stage = "product_definition"
            project.current_focus = "Generate PRD and UX blueprint"
            project.latest_summary = "Scope confirmed; product definition stage is queued"
            self.service._queue_stage_run(
                project=project,
                stage_type="product_definition",
                triggered_by="decision",
                trigger_reason=f"decision:{decision.id}:approved",
                summary="Product definition stage queued after scope confirmation",
                now=now,
            )
            return

        if brief:
            brief.status = "draft"
        project.status = "draft"
        project.current_stage = "briefing"
        project.current_focus = "Revise the project brief"
        project.latest_summary = "Scope rejected; brief needs revision"

    def _apply_release_approval(self, project: Project, decision: Decision, resolution: str, now: datetime) -> None:
        release_pack = self.db.query(Asset).filter(Asset.approval_decision_id == decision.id).first()
        if release_pack:
            self.service._link_decision_asset(decision.id, release_pack.id, "approval_target", now)

        if resolution == "approved":
            if release_pack:
                release_pack.status = "approved"
                release_pack.approved_at = now
            project.status = "released"
            project.current_stage = "released"
            project.current_focus = "Release approved"
            project.latest_summary = "Release approved and project marked as released"
            project.released_at = now
            return

        if release_pack:
            release_pack.status = "in_review"
        self.service._queue_stage_run(
            project=project,
            stage_type="release_preparation",
            triggered_by="decision",
            trigger_reason=f"decision:{decision.id}:rejected",
            summary="Release preparation queued after release approval rejection",
            now=now,
        )
        project.status = "release_ready"
        project.current_stage = "release_preparation"
        project.current_focus = "Revise release pack and rerun release preparation"
        project.latest_summary = "Release approval rejected; release pack needs revision"
