# -*- coding: utf-8 -*-
"""Minimal project-first business service for the v2 bootstrap flow."""
import json
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from execution.event_log import append_event
from models.audit import Event
from models.database import Asset, Decision, DecisionAsset, Project, StageRun, StageRunAsset
from orchestration.decision_effects import DecisionEffectsCoordinator
from orchestration.project_bootstrap_coordinator import ProjectBootstrapCoordinator
from orchestration.project_flow_coordinator import ProjectFlowCoordinator
from read_models.asset_views import AssetViewBuilder
from read_models.project_views import ProjectViewBuilder
from read_models.serializers import ProjectSerializer
from read_models.stage_run_views import StageRunViewBuilder
from services.asset_service import AssetService


class ProjectService:
    Asset = Asset
    Decision = Decision
    DecisionAsset = DecisionAsset
    StageRun = StageRun
    StageRunAsset = StageRunAsset

    def __init__(self, db: Session):
        self.db = db
        self.asset_service = AssetService(db, self)

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

        ProjectBootstrapCoordinator(self).bootstrap(
            project=project,
            one_line_vision=one_line_vision,
            description=description,
            target_platforms=target_platforms,
            target_users=target_users,
            references=references,
            now=now,
        )
        self.db.commit()
        self.db.refresh(project)
        return project

    def list_projects_v2(self) -> list[Project]:
        return self.db.query(Project).filter(Project.legacy_mode == False).order_by(Project.last_activity_at.desc()).all()

    def list_project_payloads(self) -> list[dict[str, Any]]:
        return [self.serialize_project(project) for project in self.list_projects_v2()]

    def get_project_or_404(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    def build_create_project_response(
        self,
        name: str,
        one_line_vision: str,
        description: str,
        target_platforms: list[str],
        target_users: list[str],
        references: list[str],
        execution_mode: str,
    ) -> dict[str, Any]:
        project = self.create_project(
            name=name,
            one_line_vision=one_line_vision,
            description=description,
            target_platforms=target_platforms,
            target_users=target_users,
            references=references,
            execution_mode=execution_mode,
        )
        return {"project": self.serialize_project(project), "next_action": "review_scope_confirmation"}

    def build_project_payload(self, project_id: int) -> dict[str, Any]:
        return self.serialize_project(self.get_project_or_404(project_id))

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

    def list_project_decisions(self, project_id: int) -> list[Decision]:
        self.get_project_or_404(project_id)
        return self.db.query(Decision).filter(Decision.project_id == project_id).order_by(Decision.created_at.desc()).all()

    def list_project_decision_payloads(self, project_id: int) -> list[dict[str, Any]]:
        return [self.serialize_decision(decision) for decision in self.list_project_decisions(project_id)]

    def get_pending_decisions(self, project_id: int) -> list[Decision]:
        return self.db.query(Decision).filter(Decision.project_id == project_id, Decision.status == "pending").all()

    def get_decision_or_404(self, decision_id: int) -> Decision:
        decision = self.db.query(Decision).filter(Decision.id == decision_id).first()
        if not decision:
            raise HTTPException(status_code=404, detail="Decision not found")
        return decision

    def build_decision_payload(self, decision_id: int) -> dict[str, Any]:
        return self.serialize_decision(self.get_decision_or_404(decision_id))

    def resolve_decision(self, decision_id: int, resolution: str, selected_option: str | None, note: str | None):
        decision = self.get_decision_or_404(decision_id)
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

        coordinator = DecisionEffectsCoordinator(self)
        coordinator.apply(project, decision, resolution, now)

        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(decision)
        return project, decision

    def build_resolve_decision_response(
        self,
        decision_id: int,
        resolution: str,
        selected_option: str | None,
        note: str | None,
    ) -> dict[str, Any]:
        project, decision = self.resolve_decision(
            decision_id=decision_id,
            resolution=resolution,
            selected_option=selected_option,
            note=note,
        )
        return {
            "project": self.serialize_project(project),
            "decision": self.serialize_decision(decision),
        }

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
        coordinator = ProjectFlowCoordinator(self)
        stage_run = coordinator.continue_project(project)
        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(stage_run)
        return project, stage_run

    def build_continue_project_response(self, project_id: int) -> dict[str, Any]:
        project, stage_run = self.continue_project(project_id)
        return {
            "project": self.serialize_project(project),
            "stage_run": self.serialize_stage_run(stage_run),
            "message": f"{stage_run.stage_type} is now running",
        }

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

    def _stage_phase(self, status: str) -> str:
        phase_map = {
            "queued": "queued",
            "running": "in_progress",
            "waiting_for_decision": "blocked",
            "blocked": "blocked",
            "completed": "done",
            "cancelled": "done",
            "failed": "done",
        }
        return phase_map.get(status, "unknown")

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
        return ProjectViewBuilder(self).build_dashboard()

    def build_project_overview(self, project_id: int) -> dict[str, Any]:
        return ProjectViewBuilder(self).build_project_overview(project_id)

    def build_asset_detail(self, asset_id: int) -> dict[str, Any]:
        return AssetViewBuilder(self).build_asset_detail(asset_id)

    def list_project_stage_runs(self, project_id: int) -> list[dict[str, Any]]:
        return StageRunViewBuilder(self).list_stage_runs(project_id)

    def build_updated_project_payload(self, project_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        project = self.update_project(project_id, patch)
        return self.serialize_project(project)

    def serialize_project(self, project: Project) -> dict[str, Any]:
        return ProjectSerializer(self).serialize_project(project)

    def serialize_asset_dependencies(self, asset_id: int) -> dict[str, list[dict[str, Any]]]:
        return AssetViewBuilder(self).serialize_asset_dependencies(asset_id)

    def serialize_asset(self, asset: Asset) -> dict[str, Any]:
        return ProjectSerializer(self).serialize_asset(asset)

    def serialize_decision(self, decision: Decision) -> dict[str, Any]:
        return ProjectSerializer(self).serialize_decision(decision)

    def build_stage_run_detail(self, stage_run_id: int) -> dict[str, Any]:
        return StageRunViewBuilder(self).build_stage_run_detail(stage_run_id)

    def build_stage_run_events(self, stage_run_id: int) -> list[dict[str, Any]]:
        return StageRunViewBuilder(self).list_stage_run_events(stage_run_id)

    def list_stage_run_events(self, stage_run_id: int) -> list[Event]:
        return (
            self.db.query(Event)
            .filter(Event.stage_run_id == stage_run_id)
            .order_by(Event.created_at.asc(), Event.id.asc())
            .all()
        )

    def append_stage_run_event(
        self,
        stage_run: StageRun,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> Event:
        return append_event(
            self.db,
            project_id=stage_run.project_id,
            stage_run_id=stage_run.id,
            event_type=event_type,
            agent_name=agent_name,
            stage_name=stage_run.stage_type,
            summary=summary,
            payload=payload,
        )

    def add_stage_run_instruction(self, stage_run_id: int, content: str, author: str = "boss") -> Event:
        stage_run = self.db.query(StageRun).filter(StageRun.id == stage_run_id).first()
        if not stage_run:
            raise HTTPException(status_code=404, detail="Stage run not found")
        return self.append_stage_run_event(
            stage_run=stage_run,
            event_type="stage_instruction",
            summary=f"Instruction added for {stage_run.stage_type}",
            payload={"content": content, "author": author},
            agent_name=author,
        )

    def serialize_event(self, event: Event) -> dict[str, Any]:
        return ProjectSerializer(self).serialize_event(event)

    def serialize_stage_run(self, stage_run: StageRun) -> dict[str, Any]:
        return ProjectSerializer(self).serialize_stage_run(stage_run)
