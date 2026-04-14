"""Compatibility wrapper for legacy imports of stage execution orchestration."""
from datetime import datetime

from execution.bootstrap_stage_executor import BootstrapStageExecutor
from models.database import Project, StageRun


class StageExecutionCoordinator:
    """Backward-compatible facade over the bootstrap stage executor."""

    def __init__(self, service):
        self.executor = BootstrapStageExecutor(service)

    def run(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        self.executor.execute(project, stage_run, now)
