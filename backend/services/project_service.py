# -*- coding: utf-8 -*-
"""Minimal project-first business service for the v2 bootstrap flow."""
import json
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.database import Asset, Decision, DecisionAsset, Project, StageRun, StageRunAsset


class ProjectService:
    def __init__(self, db: Session):
        self.db = db

    def _slugify(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
        slug = base
        index = 2
        while self.db.query(Project).filter(Project.slug == slug).first():
            slug = f"{base}-{index}"
            index += 1
        return slug

    def create_project(
        self,
        name: str,
        one_line_vision: str,
        description: str,
        target_platforms: list[str],
        target_users: list[str],
        references: list[str],
        execution_mode: str,
    ) -> Project:
        now = datetime.now()
        project = Project(
            name=name,
            description=description,
            slug=self._slugify(name),
            one_line_vision=one_line_vision,
            target_platforms_json=json.dumps(target_platforms, ensure_ascii=False),
            target_users_json=json.dumps(target_users, ensure_ascii=False),
            references_json=json.dumps(references, ensure_ascii=False),
            status="draft",
            current_stage="briefing",
            execution_mode=execution_mode,
            health_status="healthy",
            autopilot_enabled=(execution_mode == "autopilot"),
            current_focus="Review the generated project brief",
            latest_summary="Project created and waiting for scope confirmation",
            last_activity_at=now,
            legacy_mode=False,
        )
        self.db.add(project)
        self.db.flush()

        stage_run = StageRun(
            project_id=project.id,
            stage_type="briefing",
            run_index=1,
            status="waiting_for_decision",
            triggered_by="system",
            trigger_reason="project_created",
            execution_mode_snapshot=execution_mode,
            summary="Initial briefing stage created for project bootstrap",
            started_at=now,
            created_at=now,
        )
        self.db.add(stage_run)
        self.db.flush()

        brief = Asset(
            project_id=project.id,
            asset_type="project_brief",
            title=f"{name} Project Brief v1",
            summary="Initial machine-generated project brief draft",
            content_json=json.dumps(
                {
                    "name": name,
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
                f"# {name}\n\n"
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
        self._link_stage_run_asset(stage_run.id, brief.id, "output", now)
        self._link_decision_asset(decision.id, brief.id, "approval_target", now)
        self.db.commit()
        self.db.refresh(project)
        return project

    def list_projects_v2(self) -> list[Project]:
        return self.db.query(Project).filter(Project.legacy_mode == False).order_by(Project.last_activity_at.desc()).all()

    def get_project_or_404(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    def update_project(self, project_id: int, patch: dict[str, Any]) -> Project:
        project = self.get_project_or_404(project_id)
        if "target_platforms" in patch:
            patch["target_platforms_json"] = json.dumps(patch.pop("target_platforms"), ensure_ascii=False)
        if "target_users" in patch:
            patch["target_users_json"] = json.dumps(patch.pop("target_users"), ensure_ascii=False)
        if "references" in patch:
            patch["references_json"] = json.dumps(patch.pop("references"), ensure_ascii=False)
        for field in [
            "name", "description", "one_line_vision", "execution_mode",
            "target_platforms_json", "target_users_json", "references_json",
        ]:
            if field in patch:
                setattr(project, field, patch[field])
        project.last_activity_at = datetime.now()
        self.db.commit()
        self.db.refresh(project)
        return project

    def get_pending_decisions(self, project_id: int) -> list[Decision]:
        return self.db.query(Decision).filter(Decision.project_id == project_id, Decision.status == "pending").all()

    def resolve_decision(self, decision_id: int, resolution: str, selected_option: str | None, note: str | None):
        decision = self.db.query(Decision).filter(Decision.id == decision_id).first()
        if not decision:
            raise HTTPException(status_code=404, detail="Decision not found")
        if decision.status != "pending":
            raise HTTPException(status_code=409, detail="Decision has already been resolved")
        if resolution not in {"approved", "rejected"}:
            raise HTTPException(status_code=400, detail="resolution must be approved or rejected")

        project = self.get_project_or_404(decision.project_id)
        now = datetime.now()
        decision.status = resolution
        decision.resolved_option = selected_option or decision.recommended_option
        decision.resolution_note = note
        decision.resolved_at = now
        project.last_activity_at = now

        if decision.decision_type == "scope_confirmation":
            brief = self.db.query(Asset).filter(Asset.approval_decision_id == decision.id).first()
            if resolution == "approved":
                if brief:
                    brief.status = "approved"
                    brief.approved_at = now
                    self._link_decision_asset(decision.id, brief.id, "approval_target", now)
                project.status = "brief_confirmed"
                project.current_stage = "product_definition"
                project.current_focus = "Generate PRD and UX blueprint"
                project.latest_summary = "Scope confirmed; product definition stage is queued"

                next_run = self._queue_stage_run(
                    project=project,
                    stage_type="product_definition",
                    triggered_by="decision",
                    trigger_reason=f"decision:{decision.id}:approved",
                    summary="Product definition stage queued after scope confirmation",
                    now=now,
                )
            else:
                if brief:
                    brief.status = "draft"
                project.status = "draft"
                project.current_stage = "briefing"
                project.current_focus = "Revise the project brief"
                project.latest_summary = "Scope rejected; brief needs revision"
        elif decision.decision_type == "release_approval":
            release_pack = self.db.query(Asset).filter(Asset.approval_decision_id == decision.id).first()
            if release_pack:
                self._link_decision_asset(decision.id, release_pack.id, "approval_target", now)
            if resolution == "approved":
                if release_pack:
                    release_pack.status = "approved"
                    release_pack.approved_at = now
                project.status = "released"
                project.current_focus = "Release approved"
                project.latest_summary = "Release approved and project marked as released"
                project.released_at = now
            else:
                if release_pack:
                    release_pack.status = "in_review"
                project.status = "release_ready"
                project.current_focus = "Revise release pack and rerun release preparation"
                project.latest_summary = "Release approval rejected; release pack needs revision"

        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(decision)
        return project, decision

    def _next_stage_run_index(self, project_id: int, stage_type: str) -> int:
        existing = (
            self.db.query(StageRun)
            .filter(StageRun.project_id == project_id, StageRun.stage_type == stage_type)
            .order_by(StageRun.run_index.desc())
            .first()
        )
        return (existing.run_index + 1) if existing else 1

    def continue_project(self, project_id: int) -> tuple[Project, StageRun]:
        project = self.get_project_or_404(project_id)
        pending = self.get_pending_decisions(project_id)
        if pending:
            raise HTTPException(status_code=409, detail="Project has pending decisions and cannot continue yet")

        stage_run = (
            self.db.query(StageRun)
            .filter(StageRun.project_id == project_id, StageRun.status == "queued")
            .order_by(StageRun.created_at.asc())
            .first()
        )
        if not stage_run:
            raise HTTPException(status_code=409, detail="Project has no queued stage run to continue")

        now = datetime.now()
        stage_run.status = "running"
        if not stage_run.started_at:
            stage_run.started_at = now

        project.current_stage = stage_run.stage_type
        project.current_focus = f"Running {stage_run.stage_type}"
        project.latest_summary = f"{stage_run.stage_type} stage is now running"
        project.last_activity_at = now
        if project.status == "brief_confirmed":
            project.status = "defining"

        self._sync_stage_inputs_for_stage(stage_run, now)

        if stage_run.stage_type == "product_definition":
            self._bootstrap_product_definition_output(project, stage_run, now)
        elif stage_run.stage_type == "build_execution":
            self._bootstrap_build_execution_output(project, stage_run, now)
        elif stage_run.stage_type == "qa_validation":
            self._bootstrap_qa_validation_output(project, stage_run, now)
        elif stage_run.stage_type == "release_preparation":
            self._bootstrap_release_preparation_output(project, stage_run, now)

        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(stage_run)
        return project, stage_run

    def _bootstrap_product_definition_output(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        brief = (
            self.db.query(Asset)
            .filter(Asset.project_id == project.id, Asset.asset_type == "project_brief", Asset.is_current == True)
            .first()
        )
        source_refs = []
        if brief:
            source_refs.append({"asset_id": brief.id, "asset_type": brief.asset_type})

        self._replace_current_asset(
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

        self._replace_current_asset(
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

        self._replace_current_asset(
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

        self._queue_stage_run(
            project=project,
            stage_type="build_execution",
            triggered_by="system",
            trigger_reason=f"stage_run:{stage_run.id}:definition_complete",
            summary="Build execution bootstrap queued after definition bundle review",
            now=now,
        )

        project.current_focus = "Review PRD, UX blueprint, and tech spec scaffolds"
        project.latest_summary = "Product definition scaffolds generated; build execution queued"

    def _bootstrap_build_execution_output(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        tech_spec = (
            self.db.query(Asset)
            .filter(Asset.project_id == project.id, Asset.asset_type == "tech_spec", Asset.is_current == True)
            .first()
        )
        source_refs = []
        if tech_spec:
            source_refs.append({"asset_id": tech_spec.id, "asset_type": tech_spec.asset_type})

        self._replace_current_asset(
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

        self._replace_current_asset(
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

        self._queue_stage_run(
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

    def _bootstrap_qa_validation_output(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        build_artifact = (
            self.db.query(Asset)
            .filter(Asset.project_id == project.id, Asset.asset_type == "build_artifact", Asset.is_current == True)
            .first()
        )
        source_refs = []
        if build_artifact:
            source_refs.append({"asset_id": build_artifact.id, "asset_type": build_artifact.asset_type})

        self._replace_current_asset(
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

        self._queue_stage_run(
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

    def _bootstrap_release_preparation_output(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        test_report = (
            self.db.query(Asset)
            .filter(Asset.project_id == project.id, Asset.asset_type == "test_report", Asset.is_current == True)
            .first()
        )
        source_refs = []
        if test_report:
            source_refs.append({"asset_id": test_report.id, "asset_type": test_report.asset_type})

        self._replace_current_asset(
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

        release_pack = self.db.query(Asset).filter(
            Asset.project_id == project.id,
            Asset.asset_type == "release_pack",
            Asset.is_current == True,
        ).first()

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
            self._link_decision_asset(decision.id, release_pack.id, "approval_target", now)

        stage_run.status = "completed"
        stage_run.ended_at = now
        stage_run.summary = "Release preparation bootstrap completed and release pack created"

        project.status = "release_ready"
        project.current_stage = "release_preparation"
        project.current_focus = "Review release pack for approval"
        project.latest_summary = "Release pack generated and waiting for release approval"

    def _replace_current_asset(
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
        existing = (
            self.db.query(Asset)
            .filter(Asset.project_id == project.id, Asset.asset_type == asset_type, Asset.is_current == True)
            .first()
        )
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
        self._link_stage_run_asset(stage_run.id, asset.id, "output", now)
        for ref in source_refs:
            if ref.get("asset_id"):
                self._link_asset_dependency(project.id, ref["asset_id"], asset.id, now)
        return asset

    def _queue_stage_run(
        self,
        project: Project,
        stage_type: str,
        triggered_by: str,
        trigger_reason: str,
        summary: str,
        now: datetime,
    ) -> StageRun:
        queued = (
            self.db.query(StageRun)
            .filter(StageRun.project_id == project.id, StageRun.stage_type == stage_type, StageRun.status == "queued")
            .first()
        )
        if queued:
            return queued

        stage_run = StageRun(
            project_id=project.id,
            stage_type=stage_type,
            run_index=self._next_stage_run_index(project.id, stage_type),
            status="queued",
            triggered_by=triggered_by,
            trigger_reason=trigger_reason,
            execution_mode_snapshot=project.execution_mode,
            summary=summary,
            created_at=now,
        )
        self.db.add(stage_run)
        self.db.flush()
        return stage_run

    def _link_stage_run_asset(self, stage_run_id: int, asset_id: int, direction: str, now: datetime) -> None:
        existing = self.db.query(StageRunAsset).filter(
            StageRunAsset.stage_run_id == stage_run_id,
            StageRunAsset.asset_id == asset_id,
            StageRunAsset.direction == direction,
        ).first()
        if not existing:
            self.db.add(StageRunAsset(stage_run_id=stage_run_id, asset_id=asset_id, direction=direction, created_at=now))

    def _link_decision_asset(self, decision_id: int, asset_id: int, relation_role: str, now: datetime) -> None:
        existing = self.db.query(DecisionAsset).filter(
            DecisionAsset.decision_id == decision_id,
            DecisionAsset.asset_id == asset_id,
            DecisionAsset.relation_role == relation_role,
        ).first()
        if not existing:
            self.db.add(DecisionAsset(decision_id=decision_id, asset_id=asset_id, relation_role=relation_role, created_at=now))

    def _link_asset_dependency(self, project_id: int, from_asset_id: int, to_asset_id: int, now: datetime) -> None:
        from models.database import AssetLink
        existing = self.db.query(AssetLink).filter(
            AssetLink.project_id == project_id,
            AssetLink.from_asset_id == from_asset_id,
            AssetLink.to_asset_id == to_asset_id,
            AssetLink.relation_type == "derived_from",
        ).first()
        if not existing:
            self.db.add(AssetLink(project_id=project_id, from_asset_id=from_asset_id, to_asset_id=to_asset_id, relation_type="derived_from", created_at=now))

    def _sync_stage_inputs_for_stage(self, stage_run: StageRun, now: datetime) -> None:
        input_map = {
            "product_definition": ["project_brief"],
            "build_execution": ["tech_spec", "prd", "ux_blueprint"],
            "qa_validation": ["build_artifact"],
            "release_preparation": ["test_report", "build_artifact"],
        }
        for asset_type in input_map.get(stage_run.stage_type, []):
            asset = self.db.query(Asset).filter(
                Asset.project_id == stage_run.project_id,
                Asset.asset_type == asset_type,
                Asset.is_current == True,
            ).first()
            if asset:
                self._link_stage_run_asset(stage_run.id, asset.id, "input", now)

    def build_dashboard(self) -> dict[str, Any]:
        projects = [self.serialize_project(project) for project in self.list_projects_v2()]
        decisions = self.db.query(Decision).filter(Decision.status == "pending").order_by(Decision.created_at.desc()).all()
        assets = self.db.query(Asset).filter(Asset.is_current == True).order_by(Asset.updated_at.desc()).limit(10).all()
        stage_runs = self.db.query(StageRun).filter(StageRun.status.in_(["queued", "running", "waiting_for_decision"])).all()
        return {
            "projects": projects,
            "pending_decisions": [self.serialize_decision(decision) for decision in decisions],
            "recent_assets": [self.serialize_asset(asset) for asset in assets],
            "active_stage_runs": [self.serialize_stage_run(stage_run) for stage_run in stage_runs],
            "alerts": [],
        }

    def build_project_overview(self, project_id: int) -> dict[str, Any]:
        project = self.get_project_or_404(project_id)
        assets = (
            self.db.query(Asset)
            .filter(Asset.project_id == project_id, Asset.is_current == True)
            .order_by(Asset.updated_at.desc())
            .all()
        )
        decisions = (
            self.db.query(Decision)
            .filter(Decision.project_id == project_id)
            .order_by(Decision.created_at.desc())
            .all()
        )
        stage_runs = (
            self.db.query(StageRun)
            .filter(StageRun.project_id == project_id)
            .order_by(StageRun.created_at.desc())
            .all()
        )

        current_stage_run = stage_runs[0] if stage_runs else None
        pending_decisions = [decision for decision in decisions if decision.status == "pending"]
        assets_by_type = {asset.asset_type: self.serialize_asset(asset) for asset in assets}
        stage_summary = {
            "total": len(stage_runs),
            "completed": len([run for run in stage_runs if run.status == "completed"]),
            "active": len([run for run in stage_runs if run.status in {"queued", "running", "waiting_for_decision"}]),
            "latest_completed_stage": next(
                (run.stage_type for run in stage_runs if run.status == "completed"),
                None,
            ),
        }
        release_readiness = {
            "has_prd": "prd" in assets_by_type,
            "has_release_pack": "release_pack" in assets_by_type,
            "pending_release_decision": any(
                decision.decision_type == "release_approval" and decision.status == "pending"
                for decision in decisions
            ),
            "status": "not_ready",
        }
        if release_readiness["has_release_pack"] and not release_readiness["pending_release_decision"]:
            release_readiness["status"] = "ready_for_review"
        elif "test_report" in assets_by_type:
            release_readiness["status"] = "qa_complete"
        elif "task_plan" in assets_by_type or "build_artifact" in assets_by_type:
            release_readiness["status"] = "in_build"
        elif release_readiness["has_prd"]:
            release_readiness["status"] = "in_definition"

        recommended_next_action = "continue_project"
        if pending_decisions:
            recommended_next_action = "resolve_scope_confirmation"
        elif current_stage_run and current_stage_run.status == "queued":
            recommended_next_action = "continue_project"
        elif current_stage_run and current_stage_run.status in {"running", "waiting_for_decision"}:
            recommended_next_action = "review_current_stage"
        elif current_stage_run and current_stage_run.status == "completed" and "release_pack" in assets_by_type:
            recommended_next_action = "review_release_pack"
        elif current_stage_run and current_stage_run.status == "completed" and "test_report" in assets_by_type:
            recommended_next_action = "review_test_report"
        elif current_stage_run and current_stage_run.status == "completed" and "task_plan" in assets_by_type:
            recommended_next_action = "review_task_plan"
        elif current_stage_run and current_stage_run.status == "completed" and {"prd", "ux_blueprint", "tech_spec"}.issubset(set(assets_by_type.keys())):
            recommended_next_action = "review_definition_bundle"
        elif current_stage_run and current_stage_run.status == "completed" and "prd" in assets_by_type:
            recommended_next_action = "review_prd"

        return {
            "project": self.serialize_project(project),
            "current_stage_run": self.serialize_stage_run(current_stage_run) if current_stage_run else None,
            "key_assets": [self.serialize_asset(asset) for asset in assets],
            "assets_by_type": assets_by_type,
            "pending_decisions": [self.serialize_decision(decision) for decision in pending_decisions],
            "decision_history": [self.serialize_decision(decision) for decision in decisions[:10]],
            "stage_summary": stage_summary,
            "open_tasks_summary": {"count": 0},
            "recent_activity": [self.serialize_stage_run(stage_run) for stage_run in stage_runs[:5]],
            "release_readiness": release_readiness,
            "recommended_next_action": recommended_next_action,
        }

    def serialize_project(self, project: Project) -> dict[str, Any]:
        return {
            "id": project.id,
            "slug": project.slug,
            "name": project.name,
            "description": project.description,
            "one_line_vision": project.one_line_vision,
            "status": project.status,
            "current_stage": project.current_stage,
            "execution_mode": project.execution_mode,
            "health_status": project.health_status,
            "current_focus": project.current_focus,
            "blocking_reason": project.blocking_reason,
            "latest_summary": project.latest_summary,
            "target_platforms": json.loads(project.target_platforms_json or "[]"),
            "target_users": json.loads(project.target_users_json or "[]"),
            "references": json.loads(project.references_json or "[]"),
            "last_decision_id": project.last_decision_id,
            "legacy_mode": project.legacy_mode,
            "last_activity_at": project.last_activity_at.isoformat() if project.last_activity_at else None,
            "created_at": project.created_at.isoformat() if project.created_at else None,
        }

    def serialize_asset(self, asset: Asset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "project_id": asset.project_id,
            "asset_type": asset.asset_type,
            "title": asset.title,
            "summary": asset.summary,
            "content_json": json.loads(asset.content_json or "{}"),
            "content_markdown": asset.content_markdown,
            "version": asset.version,
            "status": asset.status,
            "is_current": asset.is_current,
            "approval_decision_id": asset.approval_decision_id,
            "produced_by_stage_run_id": asset.produced_by_stage_run_id,
            "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
        }

    def serialize_decision(self, decision: Decision) -> dict[str, Any]:
        return {
            "id": decision.id,
            "project_id": decision.project_id,
            "stage_run_id": decision.stage_run_id,
            "decision_type": decision.decision_type,
            "title": decision.title,
            "context_summary": decision.context_summary,
            "recommended_option": decision.recommended_option,
            "alternative_options": json.loads(decision.alternative_options_json or "[]"),
            "impact_summary": decision.impact_summary,
            "requested_action": decision.requested_action,
            "status": decision.status,
            "resolved_option": decision.resolved_option,
            "resolution_note": decision.resolution_note,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
            "resolved_at": decision.resolved_at.isoformat() if decision.resolved_at else None,
        }

    def serialize_stage_run(self, stage_run: StageRun) -> dict[str, Any]:
        return {
            "id": stage_run.id,
            "project_id": stage_run.project_id,
            "stage_type": stage_run.stage_type,
            "run_index": stage_run.run_index,
            "status": stage_run.status,
            "triggered_by": stage_run.triggered_by,
            "trigger_reason": stage_run.trigger_reason,
            "summary": stage_run.summary,
            "started_at": stage_run.started_at.isoformat() if stage_run.started_at else None,
            "ended_at": stage_run.ended_at.isoformat() if stage_run.ended_at else None,
            "created_at": stage_run.created_at.isoformat() if stage_run.created_at else None,
        }
