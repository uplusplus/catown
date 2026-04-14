# -*- coding: utf-8 -*-
"""Bootstrap executor for the current scaffold-only project-first flow."""
import json
from dataclasses import dataclass
from datetime import datetime

from models.database import Decision, Project, StageRun


@dataclass
class StageExecutionResult:
    """Minimal execution contract for stage executors under the new kernel."""

    status: str
    summary: str
    emitted_asset_types: list[str]
    queued_stage_types: list[str]
    pending_decision_types: list[str]


class BootstrapStageExecutor:
    """Owns the scaffold generation path until a real runtime replaces it."""

    SUPPORTED_STAGE_TYPES = {
        "product_definition",
        "build_execution",
        "qa_validation",
        "release_preparation",
    }

    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.assets = service.asset_service

    def supports(self, stage_type: str) -> bool:
        return stage_type in self.SUPPORTED_STAGE_TYPES

    def execute(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        if stage_run.stage_type == "product_definition":
            return self._product_definition(project, stage_run, now)
        if stage_run.stage_type == "build_execution":
            return self._build_execution(project, stage_run, now)
        if stage_run.stage_type == "qa_validation":
            return self._qa_validation(project, stage_run, now)
        return self._release_preparation(project, stage_run, now)

    def _product_definition(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        source_refs = self.assets.build_source_refs(project.id, ["project_brief"])

        self.assets.replace_current_asset(
            project=project,
            asset_type="prd",
            title=f"{project.name} PRD v1",
            summary="Bootstrap PRD scaffold generated from the confirmed project brief",
            content_json={
                "project_name": project.name,
                "one_line_vision": project.one_line_vision,
                "problem_statement": project.description or project.one_line_vision,
                "target_users": json.loads(project.target_users_json or "[]"),
                "target_platforms": json.loads(project.target_platforms_json or "[]"),
                "scope_basis": "confirmed_project_brief",
                "status": "draft",
            },
            content_markdown=(
                f"# {project.name} PRD\n\n"
                f"## Vision\n{project.one_line_vision or 'TBD'}\n\n"
                f"## Scope Basis\nConfirmed project brief\n\n"
                f"## Next Step\nRefine requirements, flows, and UX blueprint.\n"
            ),
            owner_agent="product_manager",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        self.assets.replace_current_asset(
            project=project,
            asset_type="ux_blueprint",
            title=f"{project.name} UX Blueprint v1",
            summary="Bootstrap UX blueprint scaffold aligned with the initial PRD",
            content_json={
                "project_name": project.name,
                "primary_user_flow": ["Landing", "Core task", "Progress feedback"],
                "key_screens": ["home", "detail", "settings"],
                "design_goal": "Simple, low-friction MVP flow",
                "status": "draft",
            },
            content_markdown=(
                f"# {project.name} UX Blueprint\n\n"
                f"## Design Goal\nSimple, low-friction MVP flow.\n\n"
                f"## Key Screens\n- Home\n- Detail\n- Settings\n"
            ),
            owner_agent="ux_designer",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        self.assets.replace_current_asset(
            project=project,
            asset_type="tech_spec",
            title=f"{project.name} Tech Spec v1",
            summary="Bootstrap technical specification scaffold derived from the product definition stage",
            content_json={
                "project_name": project.name,
                "architecture_style": "modular monolith",
                "target_platforms": json.loads(project.target_platforms_json or "[]"),
                "core_modules": ["project", "asset", "decision", "stage_run"],
                "delivery_goal": "Support the first product-definition to release-ready pipeline",
                "status": "draft",
            },
            content_markdown=(
                f"# {project.name} Tech Spec\n\n"
                f"## Architecture Style\nModular monolith\n\n"
                f"## Core Modules\n- project\n- asset\n- decision\n- stage_run\n"
            ),
            owner_agent="architect",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        stage_run.status = "completed"
        stage_run.ended_at = now
        stage_run.summary = "Product definition bootstrap completed and PRD/UX/Tech scaffolds created"
        self.service._queue_stage_run(
            project=project,
            stage_type="build_execution",
            triggered_by="system",
            trigger_reason=f"stage_run:{stage_run.id}:definition_complete",
            summary="Build execution bootstrap queued after definition bundle review",
            now=now,
        )
        project.current_focus = "Review PRD, UX blueprint, and tech spec scaffolds"
        project.latest_summary = "Product definition scaffolds generated; build execution queued"
        return StageExecutionResult(
            status=stage_run.status,
            summary=stage_run.summary,
            emitted_asset_types=["prd", "ux_blueprint", "tech_spec"],
            queued_stage_types=["build_execution"],
            pending_decision_types=[],
        )

    def _build_execution(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        source_refs = self.assets.build_source_refs(project.id, ["tech_spec", "prd", "ux_blueprint"])

        self.assets.replace_current_asset(
            project=project,
            asset_type="task_plan",
            title=f"{project.name} Task Plan v1",
            summary="Bootstrap task plan scaffold derived from the tech spec",
            content_json={
                "project_name": project.name,
                "execution_tracks": ["backend", "frontend", "qa"],
                "milestones": ["foundation", "core_flow", "validation"],
                "source": "tech_spec",
                "status": "draft",
            },
            content_markdown=(
                f"# {project.name} Task Plan\n\n"
                f"## Execution Tracks\n- backend\n- frontend\n- qa\n\n"
                f"## Milestones\n- foundation\n- core_flow\n- validation\n"
            ),
            owner_agent="delivery_manager",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        self.assets.replace_current_asset(
            project=project,
            asset_type="build_artifact",
            title=f"{project.name} Build Artifact v1",
            summary="Bootstrap build artifact placeholder generated from the task plan",
            content_json={
                "project_name": project.name,
                "artifact_kind": "bootstrap_bundle",
                "includes": ["api-skeleton", "asset-flow", "overview-model"],
                "status": "draft",
            },
            content_markdown=(
                f"# {project.name} Build Artifact\n\n"
                f"## Includes\n- api-skeleton\n- asset-flow\n- overview-model\n"
            ),
            owner_agent="developer",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        stage_run.status = "completed"
        stage_run.ended_at = now
        stage_run.summary = "Build execution bootstrap completed and task plan/build artifact created"
        self.service._queue_stage_run(
            project=project,
            stage_type="qa_validation",
            triggered_by="system",
            trigger_reason=f"stage_run:{stage_run.id}:build_complete",
            summary="QA validation bootstrap queued after build execution",
            now=now,
        )
        project.status = "building"
        project.current_stage = "qa_validation"
        project.current_focus = "Review task plan and build artifact"
        project.latest_summary = "Build scaffolds generated; QA validation queued"
        return StageExecutionResult(
            status=stage_run.status,
            summary=stage_run.summary,
            emitted_asset_types=["task_plan", "build_artifact"],
            queued_stage_types=["qa_validation"],
            pending_decision_types=[],
        )

    def _qa_validation(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        source_refs = self.assets.build_source_refs(project.id, ["build_artifact"])

        self.assets.replace_current_asset(
            project=project,
            asset_type="test_report",
            title=f"{project.name} Test Report v1",
            summary="Bootstrap QA report generated from the current build artifact",
            content_json={
                "project_name": project.name,
                "test_suites": ["smoke", "api", "workflow"],
                "result": "pass_with_followups",
                "status": "draft",
            },
            content_markdown=(
                f"# {project.name} Test Report\n\n"
                f"## Test Suites\n- smoke\n- api\n- workflow\n\n"
                f"## Result\npass_with_followups\n"
            ),
            owner_agent="tester",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        stage_run.status = "completed"
        stage_run.ended_at = now
        stage_run.summary = "QA validation bootstrap completed and test report created"
        self.service._queue_stage_run(
            project=project,
            stage_type="release_preparation",
            triggered_by="system",
            trigger_reason=f"stage_run:{stage_run.id}:qa_complete",
            summary="Release preparation bootstrap queued after QA validation",
            now=now,
        )
        project.status = "testing"
        project.current_stage = "release_preparation"
        project.current_focus = "Review test report and prepare release pack"
        project.latest_summary = "QA report generated; release preparation queued"
        return StageExecutionResult(
            status=stage_run.status,
            summary=stage_run.summary,
            emitted_asset_types=["test_report"],
            queued_stage_types=["release_preparation"],
            pending_decision_types=[],
        )

    def _release_preparation(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        source_refs = self.assets.build_source_refs(project.id, ["test_report", "build_artifact"])

        self.assets.replace_current_asset(
            project=project,
            asset_type="release_pack",
            title=f"{project.name} Release Pack v1",
            summary="Bootstrap release pack assembled from the validated build and QA report",
            content_json={
                "project_name": project.name,
                "contents": ["build_artifact", "test_report", "release_notes"],
                "release_channel": "internal_review",
                "status": "in_review",
            },
            content_markdown=(
                f"# {project.name} Release Pack\n\n"
                f"## Contents\n- build_artifact\n- test_report\n- release_notes\n\n"
                f"## Release Channel\ninternal_review\n"
            ),
            owner_agent="release_manager",
            stage_run=stage_run,
            source_refs=source_refs,
            now=now,
        )
        release_pack = self.assets.get_current_asset(project.id, "release_pack")
        decision = Decision(
            project_id=project.id,
            stage_run_id=stage_run.id,
            decision_type="release_approval",
            title="Approve release pack",
            context_summary="Review the generated release pack and decide whether the project can be released.",
            recommended_option="approve_release_pack_v1",
            alternative_options_json=json.dumps(["approve_release_pack_v1", "revise_release_pack_v1"], ensure_ascii=False),
            impact_summary="Approval moves the project from release_ready to released; rejection keeps it in review.",
            requested_action="Approve the release pack or request another iteration.",
            status="pending",
            blocking_stage_run_id=stage_run.id,
            created_by_system_reason="release_pack_ready",
            created_at=now,
        )
        self.db.add(decision)
        self.db.flush()
        project.last_decision_id = decision.id
        if release_pack:
            release_pack.approval_decision_id = decision.id
            self.service._link_decision_asset(decision.id, release_pack.id, "approval_target", now)
        stage_run.status = "completed"
        stage_run.ended_at = now
        stage_run.summary = "Release preparation bootstrap completed and release pack created"
        project.status = "release_ready"
        project.current_stage = "release_preparation"
        project.current_focus = "Review release pack for approval"
        project.latest_summary = "Release pack generated and waiting for release approval"
        return StageExecutionResult(
            status=stage_run.status,
            summary=stage_run.summary,
            emitted_asset_types=["release_pack"],
            queued_stage_types=[],
            pending_decision_types=["release_approval"],
        )
