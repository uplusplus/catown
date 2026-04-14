"""Initial project bootstrap coordinator for the v2 project-first flow."""
import json
from datetime import datetime

from models.database import Asset, Decision, Project, StageRun


class ProjectBootstrapCoordinator:
    """Creates the initial briefing stage, brief asset, and scope decision."""

    def __init__(self, service):
        self.service = service
        self.db = service.db

    def bootstrap(
        self,
        project: Project,
        one_line_vision: str,
        description: str,
        target_platforms: list[str],
        target_users: list[str],
        references: list[str],
        now: datetime,
    ) -> None:
        stage_run = StageRun(
            project_id=project.id,
            stage_type="briefing",
            run_index=1,
            status="waiting_for_decision",
            triggered_by="system",
            trigger_reason="project_created",
            execution_mode_snapshot=project.execution_mode,
            summary="Initial briefing stage created for project bootstrap",
            started_at=now,
            created_at=now,
        )
        self.db.add(stage_run)
        self.db.flush()

        brief = Asset(
            project_id=project.id,
            asset_type="project_brief",
            title=f"{project.name} Project Brief v1",
            summary="Initial machine-generated project brief draft",
            content_json=json.dumps(
                {
                    "name": project.name,
                    "one_line_vision": one_line_vision,
                    "description": description,
                    "target_platforms": target_platforms,
                    "target_users": target_users,
                    "references": references,
                    "status": "draft",
                },
                ensure_ascii=False,
            ),
            content_markdown=(
                f"# {project.name}\n\n"
                f"## One-line vision\n{one_line_vision}\n\n"
                f"## Description\n{description or 'TBD'}\n"
            ),
            version=1,
            status="in_review",
            is_current=True,
            owner_agent="founder",
            produced_by_stage_run_id=stage_run.id,
            created_at=now,
            updated_at=now,
        )
        self.db.add(brief)
        self.db.flush()

        decision = Decision(
            project_id=project.id,
            stage_run_id=stage_run.id,
            decision_type="scope_confirmation",
            title="Confirm project scope",
            context_summary="Review the initial project brief and confirm the MVP scope.",
            recommended_option="approve_brief_v1",
            alternative_options_json=json.dumps(["approve_brief_v1", "revise_brief_v1"], ensure_ascii=False),
            impact_summary="Approval moves the project into brief_confirmed and unlocks the next planning stage.",
            requested_action="Approve the brief or request a revision.",
            status="pending",
            blocking_stage_run_id=stage_run.id,
            created_by_system_reason="project_bootstrap",
            created_at=now,
        )
        self.db.add(decision)
        self.db.flush()

        brief.approval_decision_id = decision.id
        project.last_decision_id = decision.id
        self.service._link_stage_run_asset(stage_run.id, brief.id, "output", now)
        self.service._link_decision_asset(decision.id, brief.id, "approval_target", now)
