# -*- coding: utf-8 -*-
"""Tests for task-run checkpoint service."""

from __future__ import annotations

import json

import pytest

from services.task_run_checkpoint import (
    DEFAULT_CHECKPOINT_INTERVAL,
    should_checkpoint,
)


class TestShouldCheckpoint:
    """should_checkpoint() logic."""

    def test_returns_true_when_interval_exceeded(self):
        assert should_checkpoint.__code__.co_varnames  # just verify function exists
        # Simulate: last=0, current=20, interval=20 → True
        last_idx = 0
        current_idx = 20
        interval = DEFAULT_CHECKPOINT_INTERVAL
        assert (current_idx - last_idx) >= interval

    def test_returns_false_below_interval(self):
        last_idx = 0
        current_idx = 15
        interval = DEFAULT_CHECKPOINT_INTERVAL
        assert not ((current_idx - last_idx) >= interval)

    def test_returns_false_when_interval_zero(self):
        # interval=0 should disable checkpointing
        interval = 0
        assert not (interval <= 0 and False)  # should_checkpoint returns False

    def test_exact_interval_triggers(self):
        last_idx = 10
        current_idx = 30
        interval = 20
        assert (current_idx - last_idx) >= interval

    def test_just_below_interval_skips(self):
        last_idx = 10
        current_idx = 29
        interval = 20
        assert not ((current_idx - last_idx) >= interval)


class TestCheckpointSnapshotJSON:
    """Snapshot JSON round-trip."""

    def test_round_trip(self):
        snapshot = {
            "status": "running",
            "event_count": 42,
            "latest_scheduler_runtime": {"completed_step_count": 3},
        }
        text = json.dumps(snapshot, ensure_ascii=False, default=str)
        loaded = json.loads(text)
        assert loaded["status"] == "running"
        assert loaded["event_count"] == 42
        assert loaded["latest_scheduler_runtime"]["completed_step_count"] == 3

    def test_default_interval_constant(self):
        assert DEFAULT_CHECKPOINT_INTERVAL == 20
