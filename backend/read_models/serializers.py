"""Serializer helpers for project-first v2 responses."""
import json
from typing import Any

from models.audit import Event
from models.database import Asset, Decision, Project, StageRun


class ProjectSerializer:
    def __init__(self, service):
        self.service = service

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
            "relationships": self.service._serialize_asset_dependencies(asset.id),
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

    def serialize_event(self, event: Event) -> dict[str, Any]:
        return {
            "id": event.id,
            "project_id": event.project_id,
            "stage_run_id": event.stage_run_id,
            "asset_id": event.asset_id,
            "event_type": event.event_type,
            "agent_name": event.agent_name,
            "stage_name": event.stage_name,
            "summary": event.summary,
            "payload": json.loads(event.payload or "{}"),
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

    def serialize_stage_run(self, stage_run: StageRun) -> dict[str, Any]:
        lifecycle = {
            "phase": self.service._stage_phase(stage_run.status),
            "is_active": stage_run.status == "running",
            "is_terminal": stage_run.status in {"completed", "cancelled", "failed"},
            "requires_attention": stage_run.status in {"waiting_for_decision", "blocked"},
        }
        return {
            "id": stage_run.id,
            "project_id": stage_run.project_id,
            "stage_type": stage_run.stage_type,
            "run_index": stage_run.run_index,
            "status": stage_run.status,
            "lifecycle": lifecycle,
            "triggered_by": stage_run.triggered_by,
            "trigger_reason": stage_run.trigger_reason,
            "summary": stage_run.summary,
            "started_at": stage_run.started_at.isoformat() if stage_run.started_at else None,
            "ended_at": stage_run.ended_at.isoformat() if stage_run.ended_at else None,
            "created_at": stage_run.created_at.isoformat() if stage_run.created_at else None,
        }
