# -*- coding: utf-8 -*-
"""Lightweight message-driven scheduler helpers for multi-agent orchestration."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from agents.identity import agent_type_of


DEFAULT_SIDECAR_AGENT_TYPES = {"tester"}


@dataclass(frozen=True)
class ScheduledAgentTurn:
    """A single dispatch decision made by the orchestration scheduler."""

    step_id: str
    position: int
    requested_name: str
    agent_id: int | None
    agent_name: str
    agent_type: str
    dispatch_kind: str = "blocking"
    wait_for_step_id: str | None = None
    attached_to_step_id: str | None = None
    source: str = "user_mentions"

    def to_payload(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "requested_name": self.requested_name,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type,
            "dispatch_kind": self.dispatch_kind,
            "wait_for_step_id": self.wait_for_step_id,
            "attached_to_step_id": self.attached_to_step_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class OrchestrationSchedulePlan:
    """A persisted view of the scheduler's current dispatch plan."""

    mode: str
    steps: list[ScheduledAgentTurn]
    sidecar_agent_types: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "step_count": len(self.steps),
            "blocking_step_count": sum(1 for step in self.steps if step.dispatch_kind == "blocking"),
            "sidecar_step_count": sum(1 for step in self.steps if step.dispatch_kind == "sidecar"),
            "sidecar_agent_types": self.sidecar_agent_types,
            "steps": [step.to_payload() for step in self.steps],
        }


@dataclass(frozen=True)
class ScheduledStepRuntimeState:
    """A point-in-time runtime state for a scheduled step."""

    step_id: str
    position: int
    requested_name: str
    agent_id: int | None
    agent_name: str
    agent_type: str
    dispatch_kind: str
    wait_for_step_id: str | None
    attached_to_step_id: str | None
    source: str
    status: str
    released_by_step_id: str | None = None
    dispatch_count: int = 0
    completion_count: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "requested_name": self.requested_name,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type,
            "dispatch_kind": self.dispatch_kind,
            "wait_for_step_id": self.wait_for_step_id,
            "attached_to_step_id": self.attached_to_step_id,
            "source": self.source,
            "status": self.status,
            "released_by_step_id": self.released_by_step_id,
            "dispatch_count": self.dispatch_count,
            "completion_count": self.completion_count,
        }


@dataclass(frozen=True)
class OrchestrationRuntimeSnapshot:
    """A serializable snapshot of the scheduler's live runtime state."""

    mode: str
    step_count: int
    ready_step_count: int
    waiting_step_count: int
    running_step_count: int
    completed_step_count: int
    ready_step_ids: list[str]
    waiting_step_ids: list[str]
    running_step_ids: list[str]
    completed_step_ids: list[str]
    steps: list[ScheduledStepRuntimeState]

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "step_count": self.step_count,
            "ready_step_count": self.ready_step_count,
            "waiting_step_count": self.waiting_step_count,
            "running_step_count": self.running_step_count,
            "completed_step_count": self.completed_step_count,
            "ready_step_ids": self.ready_step_ids,
            "waiting_step_ids": self.waiting_step_ids,
            "running_step_ids": self.running_step_ids,
            "completed_step_ids": self.completed_step_ids,
            "steps": [step.to_payload() for step in self.steps],
        }


class OrchestrationRuntimeQueue:
    """Tracks which scheduled steps are ready after each completed turn."""

    def __init__(self, plan: OrchestrationSchedulePlan):
        self.plan = plan
        self._steps_by_id = {step.step_id: step for step in plan.steps}
        self._ready = deque(_sort_ready_steps([step for step in plan.steps if not step.wait_for_step_id]))
        self._waiting: dict[str, list[ScheduledAgentTurn]] = {}
        self._completed: set[str] = set()
        self._runtime_state: dict[str, dict[str, Any]] = {
            step.step_id: {
                "status": "ready" if not step.wait_for_step_id else "waiting",
                "released_by_step_id": None,
                "dispatch_count": 0,
                "completion_count": 0,
            }
            for step in plan.steps
        }

        for step in plan.steps:
            if step.wait_for_step_id:
                self._waiting.setdefault(step.wait_for_step_id, []).append(step)

    def pop_ready(self) -> ScheduledAgentTurn | None:
        if not self._ready:
            return None
        step = self._ready.popleft()
        step_state = self._runtime_state.get(step.step_id)
        if step_state is not None:
            step_state["status"] = "running"
            step_state["dispatch_count"] += 1
        return step

    def mark_completed(self, step_id: str) -> list[ScheduledAgentTurn]:
        if step_id in self._completed:
            return []

        self._completed.add(step_id)
        if self._ready:
            self._ready = deque(step for step in self._ready if step.step_id != step_id)
        step_state = self._runtime_state.get(step_id)
        if step_state is not None:
            step_state["status"] = "completed"
            step_state["completion_count"] += 1
        ready = _sort_ready_steps(list(self._waiting.pop(step_id, [])))
        for step in ready:
            next_state = self._runtime_state.get(step.step_id)
            if next_state is not None:
                next_state["status"] = "ready"
                next_state["released_by_step_id"] = step_id
            self._ready.append(step)
        return ready

    def runtime_snapshot(self) -> OrchestrationRuntimeSnapshot:
        steps = [self.runtime_state_for_step(step.step_id) for step in self.plan.steps]
        ready_step_ids = [step.step_id for step in steps if step.status == "ready"]
        waiting_step_ids = [step.step_id for step in steps if step.status == "waiting"]
        running_step_ids = [step.step_id for step in steps if step.status == "running"]
        completed_step_ids = [step.step_id for step in steps if step.status == "completed"]
        return OrchestrationRuntimeSnapshot(
            mode=self.plan.mode,
            step_count=len(steps),
            ready_step_count=len(ready_step_ids),
            waiting_step_count=len(waiting_step_ids),
            running_step_count=len(running_step_ids),
            completed_step_count=len(completed_step_ids),
            ready_step_ids=ready_step_ids,
            waiting_step_ids=waiting_step_ids,
            running_step_ids=running_step_ids,
            completed_step_ids=completed_step_ids,
            steps=steps,
        )

    def runtime_snapshot_payload(self) -> dict[str, Any]:
        return self.runtime_snapshot().to_payload()

    def runtime_state_for_step(self, step_id: str) -> ScheduledStepRuntimeState:
        step = self._steps_by_id[step_id]
        runtime_state = self._runtime_state.get(step_id, {})
        return ScheduledStepRuntimeState(
            step_id=step.step_id,
            position=step.position,
            requested_name=step.requested_name,
            agent_id=step.agent_id,
            agent_name=step.agent_name,
            agent_type=step.agent_type,
            dispatch_kind=step.dispatch_kind,
            wait_for_step_id=step.wait_for_step_id,
            attached_to_step_id=step.attached_to_step_id,
            source=step.source,
            status=str(runtime_state.get("status") or "waiting"),
            released_by_step_id=runtime_state.get("released_by_step_id"),
            dispatch_count=int(runtime_state.get("dispatch_count") or 0),
            completion_count=int(runtime_state.get("completion_count") or 0),
        )

    def runtime_state_payload_for_step(self, step_id: str) -> dict[str, Any]:
        return self.runtime_state_for_step(step_id).to_payload()


def build_orchestration_schedule(
    targets: list[tuple[str, Any]],
    *,
    sidecar_agent_types: set[str] | None = None,
) -> OrchestrationSchedulePlan:
    """Build the minimal blocking + sidecar plan for the current orchestration runtime."""

    configured_sidecars = sorted(
        {
            str(agent_type or "").strip().lower()
            for agent_type in (DEFAULT_SIDECAR_AGENT_TYPES if sidecar_agent_types is None else sidecar_agent_types)
            if str(agent_type or "").strip()
        }
    )
    configured_sidecar_set = set(configured_sidecars)
    steps: list[ScheduledAgentTurn] = []
    last_blocking_step_id: str | None = None
    has_sidecar = False

    for index, (requested_name, agent) in enumerate(targets, start=1):
        if agent is None:
            continue

        step_id = f"step-{index}"
        agent_name = str(getattr(agent, "name", None) or requested_name or "agent")
        agent_type = agent_type_of(agent)
        dispatch_kind = "blocking"
        wait_for_step_id = last_blocking_step_id
        attached_to_step_id = None

        if last_blocking_step_id and agent_type in configured_sidecar_set:
            dispatch_kind = "sidecar"
            attached_to_step_id = last_blocking_step_id
            has_sidecar = True
        else:
            last_blocking_step_id = step_id

        steps.append(
            ScheduledAgentTurn(
                step_id=step_id,
                position=index,
                requested_name=str(requested_name or agent_name),
                agent_id=getattr(agent, "id", None),
                agent_name=agent_name,
                agent_type=agent_type,
                dispatch_kind=dispatch_kind,
                wait_for_step_id=wait_for_step_id,
                attached_to_step_id=attached_to_step_id,
            )
        )

    mode = "blocking_chain_with_sidecars" if has_sidecar else "linear_blocking_chain"
    return OrchestrationSchedulePlan(mode=mode, steps=steps, sidecar_agent_types=configured_sidecars)


def build_linear_orchestration_schedule(targets: list[tuple[str, Any]]) -> OrchestrationSchedulePlan:
    """Backward-compatible alias for callers not yet renamed."""

    return build_orchestration_schedule(targets)


def _sort_ready_steps(steps: list[ScheduledAgentTurn]) -> list[ScheduledAgentTurn]:
    return sorted(
        steps,
        key=lambda step: (
            0 if step.dispatch_kind == "blocking" else 1,
            step.position,
        ),
    )
