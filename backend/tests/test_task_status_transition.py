# -*- coding: utf-8 -*-
"""Tests for TaskRun status transition validation."""

from __future__ import annotations

import pytest

from services.task_status_transition import (
    InvalidTaskRunStatusTransition,
    ALL_TASK_RUN_STATUSES,
    allowed_targets,
    is_terminal,
    is_valid_transition,
    validate_transition,
)


class TestIsValidTransition:
    """is_valid_transition() truth table."""

    def test_running_can_go_to_completed_failed_cancelled_paused(self):
        for target in ("completed", "failed", "cancelled", "paused"):
            assert is_valid_transition("running", target)

    def test_running_cannot_go_to_running(self):
        assert not is_valid_transition("running", "running")

    def test_paused_can_go_to_running_or_cancelled(self):
        assert is_valid_transition("paused", "running")
        assert is_valid_transition("paused", "cancelled")

    def test_paused_cannot_go_to_completed_or_failed(self):
        for target in ("completed", "failed"):
            assert not is_valid_transition("paused", target)

    def test_completed_can_reopen_to_running(self):
        assert is_valid_transition("completed", "running")

    def test_completed_cannot_go_to_failed_or_cancelled(self):
        for target in ("failed", "cancelled"):
            assert not is_valid_transition("completed", target)

    def test_failed_is_terminal(self):
        for target in ALL_TASK_RUN_STATUSES:
            assert not is_valid_transition("failed", target)

    def test_cancelled_is_terminal(self):
        for target in ALL_TASK_RUN_STATUSES:
            assert not is_valid_transition("cancelled", target)

    def test_unknown_status_is_never_valid(self):
        assert not is_valid_transition("unknown", "running")
        assert not is_valid_transition("", "running")

    def test_case_insensitive(self):
        assert is_valid_transition("Running", "Completed")
        assert is_valid_transition("PAUSED", "running")


class TestValidateTransition:
    """validate_transition() raises on illegal transitions."""

    def test_valid_does_not_raise(self):
        validate_transition("running", "completed")  # should not raise

    def test_invalid_raises(self):
        with pytest.raises(InvalidTaskRunStatusTransition) as exc_info:
            validate_transition("completed", "failed")
        assert "completed" in str(exc_info.value)
        assert "failed" in str(exc_info.value)

    def test_terminal_raises(self):
        with pytest.raises(InvalidTaskRunStatusTransition):
            validate_transition("cancelled", "running")


class TestIsTerminal:
    """is_terminal() predicate."""

    @pytest.mark.parametrize("status", ["failed", "cancelled"])
    def test_terminal_statuses(self, status: str):
        assert is_terminal(status)

    @pytest.mark.parametrize("status", ["running", "paused", "completed"])
    def test_non_terminal_statuses(self, status: str):
        assert not is_terminal(status)


class TestAllowedTargets:
    """allowed_targets() returns the correct reachable set."""

    def test_running_targets(self):
        assert allowed_targets("running") == {"completed", "failed", "cancelled", "paused"}

    def test_paused_targets(self):
        assert allowed_targets("paused") == {"running", "cancelled"}

    def test_completed_targets(self):
        assert allowed_targets("completed") == {"running"}

    def test_terminal_targets_empty(self):
        assert allowed_targets("failed") == set()
        assert allowed_targets("cancelled") == set()

    def test_unknown_returns_empty(self):
        assert allowed_targets("nonexistent") == set()
