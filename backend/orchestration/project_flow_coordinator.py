# -*- coding: utf-8 -*-
"""Coordinator helpers for the v2 project-first execution flow."""
from datetime import datetime

from fastapi import HTTPException

from execution.stage_execution_kernel import StageExecutionKernel
from models.database import Project, StageRun


class ProjectFlowCoordinator:
    """Own stage-run execution progression so service routes stay thin."""

    def __init__(self, service):
        self.service = service
        self.db = service.db

    def continue_project(self, project: Project) -> StageRun:
        pending = self.service.get_pending_decisions(project.id)
        if pending:
            raise HTTPException(status_code=409, detail="Project has pending decisions and cannot continue yet")

        stage_run = (
            self.db.query(StageRun)
            .filter(StageRun.project_id == project.id, StageRun.status == "queued")
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

        self.service._sync_stage_inputs_for_stage(stage_run, now)

        result = StageExecutionKernel(self.service).execute(project, stage_run, now)
        self.service.append_stage_run_event(
            stage_run=stage_run,
            event_type="stage_execution_completed",
            summary=result.summary,
            payload={
                "status": result.status,
                "emitted_asset_types": result.emitted_asset_types,
                "queued_stage_types": result.queued_stage_types,
                "pending_decision_types": result.pending_decision_types,
            },
        )

        return stage_run
