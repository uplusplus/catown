# -*- coding: utf-8 -*-
"""Stage execution kernel for the project-first runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import HTTPException

from execution.bootstrap_stage_executor import BootstrapStageExecutor, StageExecutionResult
from models.database import Project, StageRun


class StageExecutor(Protocol):
    def supports(self, stage_type: str) -> bool:
        ...

    def execute(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        ...


@dataclass
class StageExecutionKernel:
    """Resolve a StageRun to the executor that owns its runtime behavior."""

    service: object

    def __post_init__(self) -> None:
        self.executors: list[StageExecutor] = [
            BootstrapStageExecutor(self.service),
        ]

    def execute(self, project: Project, stage_run: StageRun, now: datetime) -> StageExecutionResult:
        for executor in self.executors:
            if executor.supports(stage_run.stage_type):
                return executor.execute(project, stage_run, now)
        raise HTTPException(
            status_code=501,
            detail=f"No stage executor is registered for stage_type '{stage_run.stage_type}'",
        )
