# -*- coding: utf-8 -*-
"""TaskRun status transition validation.

Centralises the allowed state-machine transitions so that no caller can
illegally skip or reverse a status (e.g. ``completed → running``).
"""

from __future__ import annotations

from typing import Set

# ── Canonical transition table ────────────────────────────────────────────────
# Keys   = current status
# Values = set of statuses the current status may transition *to*.
# Terminal statuses map to an empty set (no outgoing edges).
TASK_RUN_VALID_TRANSITIONS: dict[str, Set[str]] = {
    "running":   {"completed", "failed", "cancelled", "paused"},
    "paused":    {"running", "cancelled"},
    "completed": {"running"},      # reopen for approval followup / resume
    "failed":    set(),
    "cancelled": set(),
}

ALL_TASK_RUN_STATUSES: Set[str] = set(TASK_RUN_VALID_TRANSITIONS) | {
    target for targets in TASK_RUN_VALID_TRANSITIONS.values() for target in targets
}


class InvalidTaskRunStatusTransition(Exception):
    """Raised when a TaskRun status transition is not allowed."""

    def __init__(self, current_status: str, target_status: str):
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Invalid TaskRun status transition: {current_status!r} → {target_status!r}. "
            f"Allowed targets from {current_status!r}: "
            f"{sorted(TASK_RUN_VALID_TRANSITIONS.get(current_status, set())) or '(none)'}"
        )


def is_valid_transition(current_status: str, target_status: str) -> bool:
    """Return *True* if the transition is allowed."""

    allowed = TASK_RUN_VALID_TRANSITIONS.get(current_status.strip().lower(), set())
    return target_status.strip().lower() in allowed


def validate_transition(current_status: str, target_status: str) -> None:
    """Raise :class:`InvalidTaskRunStatusTransition` when the transition is illegal."""

    if not is_valid_transition(current_status, target_status):
        raise InvalidTaskRunStatusTransition(
            current_status.strip().lower(),
            target_status.strip().lower(),
        )


def is_terminal(status: str) -> bool:
    """Return *True* if *status* is a terminal (no outgoing edges) state."""

    return TASK_RUN_VALID_TRANSITIONS.get(status.strip().lower()) == set()


def allowed_targets(current_status: str) -> Set[str]:
    """Return the set of statuses reachable from *current_status*."""

    return set(TASK_RUN_VALID_TRANSITIONS.get(current_status.strip().lower(), set()))
