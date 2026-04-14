# -*- coding: utf-8 -*-
"""Bootstrap executor for the current scaffold-only project-first flow."""
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from models.database import Decision, Project, StageRun


@dataclass
class StageExecutionResult:
    """Minimal execution contract for stage executors under the new kernel."""

    status: str
    summary: str
    emitted_asset_types: list[str]
    queued_stage_types: list[str]
    pending_decision_types: list[str]


@dataclass(frozen=True)
class AssetRecipe:
    """Describes one scaffold asset emitted by a bootstrap stage."""

    asset_type: str
    summary: str
    owner_agent: str
    title_suffix: str
    content_builder: Callable[[Project], dict[str, Any]]
    markdown_builder: Callable[[Project], str]


@dataclass(frozen=True)
class StagePlan:
    """Static bootstrap plan for a stage before special-case side effects run."""

    source_asset_types: list[str]
    asset_recipes: list[AssetRecipe]
    queue_next_stage_type: str | None
    queue_summary: str | None
    queue_trigger_suffix: str | None
    project_status: str | None
    project_current_stage: str
    project_current_focus: str
    project_latest_summary: str
    stage_summary: str


class BootstrapStageExecutor:
    """Owns the scaffold generation path until a real runtime replaces it."""

    SUPPORTED_STAGE_TYPES = {
        "product_definition",
        "build_execution",
        "qa_validation",
        "release_preparation",
    }

    STAGE_PLANS: dict[str, StagePlan] = {
        "product_definition": StagePlan(
            source_asset_types=["project_brief"],
            asset_recipes=[
                AssetRecipe(
                    asset_type="prd",
                    title_suffix="PRD",
                    summary="Bootstrap PRD scaffold generated from the confirmed project brief",
                    owner_agent="product_manager",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "one_line_vision": project.one_line_vision,
                        "problem_statement": project.description or project.one_line_vision,
                        "target_users": json.loads(project.target_users_json or "[]"),
                        "target_platforms": json.loads(project.target_platforms_json or "[]"),
                        "scope_basis": "confirmed_project_brief",
                        "status": "draft",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} PRD\n\n"
                        f"## Vision\n{project.one_line_vision or 'TBD'}\n\n"
                        f"## Scope Basis\nConfirmed project brief\n\n"
                        f"## Next Step\nRefine requirements, flows, and UX blueprint.\n"
                    ),
                ),
                AssetRecipe(
                    asset_type="ux_blueprint",
                    title_suffix="UX Blueprint",
                    summary="Bootstrap UX blueprint scaffold aligned with the initial PRD",
                    owner_agent="ux_designer",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "primary_user_flow": ["Landing", "Core task", "Progress feedback"],
                        "key_screens": ["home", "detail", "settings"],
                        "design_goal": "Simple, low-friction MVP flow",
                        "status": "draft",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} UX Blueprint\n\n"
                        f"## Design Goal\nSimple, low-friction MVP flow.\n\n"
                        f"## Key Screens\n- Home\n- Detail\n- Settings\n"
                    ),
                ),
                AssetRecipe(
                    asset_type="tech_spec",
                    title_suffix="Tech Spec",
                    summary="Bootstrap technical specification scaffold derived from the product definition stage",
                    owner_agent="architect",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "architecture_style": "modular monolith",
                        "target_platforms": json.loads(project.target_platforms_json or "[]"),
                        "core_modules": ["project", "asset", "decision", "stage_run"],
                        "delivery_goal": "Support the first product-definition to release-ready pipeline",
                        "status": "draft",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} Tech Spec\n\n"
                        f"## Architecture Style\nModular monolith\n\n"
                        f"## Core Modules\n- project\n- asset\n- decision\n- stage_run\n"
                    ),
                ),
            ],
            queue_next_stage_type="build_execution",
            queue_summary="Build execution bootstrap queued after definition bundle review",
            queue_trigger_suffix="definition_complete",
            project_status=None,
            project_current_stage="product_definition",
            project_current_focus="Review PRD, UX blueprint, and tech spec scaffolds",
            project_latest_summary="Product definition scaffolds generated; build execution queued",
            stage_summary="Product definition bootstrap completed and PRD/UX/Tech scaffolds created",
        ),
        "build_execution": StagePlan(
            source_asset_types=["tech_spec", "prd", "ux_blueprint"],
            asset_recipes=[
                AssetRecipe(
                    asset_type="task_plan",
                    title_suffix="Task Plan",
                    summary="Bootstrap task plan scaffold derived from the tech spec",
                    owner_agent="delivery_manager",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "execution_tracks": ["backend", "frontend", "qa"],
                        "milestones": ["foundation", "core_flow", "validation"],
                        "source": "tech_spec",
                        "status": "draft",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} Task Plan\n\n"
                        f"## Execution Tracks\n- backend\n- frontend\n- qa\n\n"
                        f"## Milestones\n- foundation\n- core_flow\n- validation\n"
                    ),
                ),
                AssetRecipe(
                    asset_type="build_artifact",
                    title_suffix="Build Artifact",
                    summary="Bootstrap build artifact placeholder generated from the task plan",
                    owner_agent="developer",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "artifact_kind": "bootstrap_bundle",
                        "includes": ["api-skeleton", "asset-flow", "overview-model"],
                        "status": "draft",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} Build Artifact\n\n"
                        f"## Includes\n- api-skeleton\n- asset-flow\n- overview-model\n"
                    ),
                ),
            ],
            queue_next_stage_type="qa_validation",
            queue_summary="QA validation bootstrap queued after build execution",
            queue_trigger_suffix="build_complete",
            project_status="building",
            project_current_stage="qa_validation",
            project_current_focus="Review task plan and build artifact",
            project_latest_summary="Build scaffolds generated; QA validation queued",
            stage_summary="Build execution bootstrap completed and task plan/build artifact created",
        ),
        "qa_validation": StagePlan(
            source_asset_types=["build_artifact"],
            asset_recipes=[
                AssetRecipe(
                    asset_type="test_report",
                    title_suffix="Test Report",
                    summary="Bootstrap QA report generated from the current build artifact",
                    owner_agent="tester",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "test_suites": ["smoke", "api", "workflow"],
                        "result": "pass_with_followups",
                        "status": "draft",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} Test Report\n\n"
                        f"## Test Suites\n- smoke\n- api\n- workflow\n\n"
                        f"## Result\npass_with_followups\n"
                    ),
                ),
            ],
            queue_next_stage_type="release_preparation",
            queue_summary="Release preparation bootstrap queued after QA validation",
            queue_trigger_suffix="qa_complete",
            project_status="testing",
            project_current_stage="release_preparation",
            project_current_focus="Review test report and prepare release pack",
            project_latest_summary="QA report generated; release preparation queued",
            stage_summary="QA validation bootstrap completed and test report created",
        ),
        "release_preparation": StagePlan(
            source_asset_types=["test_report", "build_artifact"],
            asset_recipes=[
                AssetRecipe(
                    asset_type="release_pack",
                    title_suffix="Release Pack",
                    summary="Bootstrap release pack assembled from the validated build and QA report",
                    owner_agent="release_manager",
                    content_builder=lambda project: {
                        "project_name": project.name,
                        "contents": ["build_artifact", "test_report", "release_notes"],
                        "release_channel": "internal_review",
                        "status": "in_review",
                    },
                    markdown_builder=lambda project: (
                        f"# {project.name} Release Pack\n\n"
                        f"## Contents\n- build_artifact\n- test_report\n- release_notes\n\n"
                        f"## Release Channel\ninternal_review\n"
                    ),
                ),
            ],
            queue_next_stage_type=None,
            queue_summary=None,
            queue_trigger_suffix=None,
            project_status="release_ready",
            project_current_stage="release_preparation",
            project_current_focus="Review release pack for approval",
            project_latest_summary="Release pack generated and waiting for release approval",
            stage_summary="Release preparation bootstrap completed and release pack created",
        ),
    }

    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.assets = service.asset_service

    def supports(self, stage_type: str) -> bool:
        return stage_type in self.SUPPORTED_STAGE_TYPES

    def execute(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        plan = self.STAGE_PLANS.get(stage_run.stage_type)
        if not plan:
            raise ValueError(f"Unsupported bootstrap stage type: {stage_run.stage_type}")

        source_refs = self.assets.build_source_refs(project.id, plan.source_asset_types)
        emitted_asset_types = self._emit_assets(project, stage_run, source_refs, now, plan.asset_recipes)

        self._finalize_stage(project, stage_run, now, plan)

        if stage_run.stage_type == "release_preparation":
            return self._attach_release_approval(project, stage_run, now, emitted_asset_types)

        queued_stage_types = self._queue_next_stage(project, stage_run, now, plan)
        return StageExecutionResult(
            status=stage_run.status,
            summary=stage_run.summary,
            emitted_asset_types=emitted_asset_types,
            queued_stage_types=queued_stage_types,
            pending_decision_types=[],
        )

    def _emit_assets(
        self,
        project: Project,
        stage_run: StageRun,
        source_refs: list[dict[str, Any]],
        now: datetime,
        recipes: list[AssetRecipe],
    ) -> list[str]:
        emitted_asset_types = []
        for recipe in recipes:
            self.assets.replace_current_asset(
                project=project,
                asset_type=recipe.asset_type,
                title=f"{project.name} {recipe.title_suffix} v1",
                summary=recipe.summary,
                content_json=recipe.content_builder(project),
                content_markdown=recipe.markdown_builder(project),
                owner_agent=recipe.owner_agent,
                stage_run=stage_run,
                source_refs=source_refs,
                now=now,
            )
            emitted_asset_types.append(recipe.asset_type)
        return emitted_asset_types

    def _finalize_stage(self, project: Project, stage_run: StageRun, now: datetime, plan: StagePlan) -> None:
        stage_run.status = "completed"
        stage_run.ended_at = now
        stage_run.summary = plan.stage_summary
        if plan.project_status:
            project.status = plan.project_status
        project.current_stage = plan.project_current_stage
        project.current_focus = plan.project_current_focus
        project.latest_summary = plan.project_latest_summary

    def _queue_next_stage(self, project: Project, stage_run: StageRun, now: datetime, plan: StagePlan) -> list[str]:
        if not plan.queue_next_stage_type:
            return []

        self.service._queue_stage_run(
            project=project,
            stage_type=plan.queue_next_stage_type,
            triggered_by="system",
            trigger_reason=f"stage_run:{stage_run.id}:{plan.queue_trigger_suffix}",
            summary=plan.queue_summary,
            now=now,
        )
        return [plan.queue_next_stage_type]

    def _attach_release_approval(
        self,
        project: Project,
        stage_run: StageRun,
        now: datetime,
        emitted_asset_types: list[str],
    ) -> StageExecutionResult:
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

        return StageExecutionResult(
            status=stage_run.status,
            summary=stage_run.summary,
            emitted_asset_types=emitted_asset_types,
            queued_stage_types=[],
            pending_decision_types=["release_approval"],
        )
