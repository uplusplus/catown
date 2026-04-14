# -*- coding: utf-8 -*-
"""Stage execution kernel for the project-first runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import HTTPException

from models.database import Project, StageRun
from execution.bootstrap_stage_executor import BootstrapStageExecutor


class StageExecutor(Protocol):
    """Executor contract for a StageRun implementation."""

    def supports(self, stage_type: str) -> bool:
        ...

    def execute(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        ...


@dataclass
class StageExecutionKernel:
    """Resolves a StageRun to the executor that owns its runtime behavior."""

    service: object

    def __post_init__(self) -> None:
        self.executors: list[StageExecutor] = [
            BootstrapStageExecutor(self.service),
        ]

    def execute(self, project: Project, stage_run: StageRun, now: datetime) -> None:
        for executor in self.executors:
            if executor.supports(stage_run.stage_type):
                executor.execute(project, stage_run, now)
                return
        raise HTTPException(
            status_code=501,
            detail=f"No stage executor is registered for stage_type '{stage_run.stage_type}'",
        )
