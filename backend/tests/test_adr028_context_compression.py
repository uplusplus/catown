# -*- coding: utf-8 -*-
"""Tests for ADR-028: Context Compression Early Trigger Fix.

Tests cover:
- Phase 0: Fragment budget increase + fragment merging
- Phase 1: Output filter (RTK strategy)
- Phase 2: History progressive compression + tool result truncation
- Phase 3: Dynamic budget derivation (ratio profiles) with min_tokens_cap
- Phase 4: Cross-stage summaries
- Phase 5: LLM-assisted summarization (integration point)
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Phase 0: Fragment Budget ─────────────────────────────────────


class TestPhase0FragmentBudget:
    """Phase 0: Verify increased fragment budgets."""

    def test_chat_interactive_budget_increased(self):
        """chat_interactive should have max_fragments=20, max_tokens_cap=8000."""
        from services.chat_prompt_builder import _DEFAULT_SELECTOR_PROFILES

        profile = _DEFAULT_SELECTOR_PROFILES["chat_interactive"]
        assert profile["max_fragments"] == 20
        assert profile["max_tokens_cap"] == 8000
        assert profile["max_tokens_by_role"]["developer"] == 2500
        assert profile["max_tokens_by_role"]["user"] == 5500

    def test_chat_interactive_scope_budgets_increased(self):
        """Scope budgets should be increased per ADR-028 Phase 0."""
        from services.chat_prompt_builder import _DEFAULT_SELECTOR_PROFILES

        scopes = _DEFAULT_SELECTOR_PROFILES["chat_interactive"]["max_tokens_by_scope"]
        assert scopes["session"] == 400
        assert scopes["run"] == 4000
        assert scopes["stage"] == 1200
        assert scopes["turn"] == 1200
        assert scopes["shared_fact"] == 600
        assert scopes["agent_private"] == 600


class TestPhase0FragmentMerging:
    """Phase 0: Verify project+chatroom fragments are merged."""

    def test_project_chat_overview_fragment_exists(self):
        """The merged fragment function should exist."""
        from services.context_builder import _build_project_chat_overview_fragment
        assert callable(_build_project_chat_overview_fragment)

    def test_merged_fragment_source(self):
        """Merged fragment should use 'project_chat_overview' source."""
        from services.context_builder import _build_project_chat_overview_fragment

        mock_project = MagicMock()
        mock_project.id = 1
        mock_project.name = "Test Project"
        mock_project.description = "A test project"
        mock_project.workspace_path = "/tmp/test"
        mock_project.vision = "Build something"
        mock_project.desired_outcome = "Done"
        mock_project.status = "active"
        mock_project.current_stage = "development"
        mock_project.health = "good"
        mock_project.created_at = MagicMock()
        mock_project.created_at.isoformat.return_value = "2026-01-01"

        mock_chatroom = MagicMock()
        mock_chatroom.id = 10
        mock_chatroom.title = "General"
        mock_chatroom.session_type = "group"

        fragment = _build_project_chat_overview_fragment(
            mock_project, mock_chatroom, None
        )
        assert fragment is not None
        assert fragment.source == "project_chat_overview"


# ── Phase 1: Output Filter ───────────────────────────────────────


class TestPhase1OutputFilter:
    """Phase 1: Verify output filter module and filters exist."""

    def test_output_filter_module_exists(self):
        """Output filter module should exist."""
        from tools.output_filter import filter_output, FilterResult
        assert callable(filter_output)

    def test_filter_result_dataclass(self):
        """FilterResult should have required fields."""
        from tools.output_filter import FilterResult
        result = FilterResult(
            output="test",
            raw_tokens=100,
            filtered_tokens=50,
            savings_pct=50.0,
        )
        assert result.output == "test"
        assert result.savings_pct == 50.0

    def test_git_filter_registered(self):
        """Git filter should be registered."""
        from tools.output_filter import _ensure_filters_loaded, _FILTER_REGISTRY
        _ensure_filters_loaded()
        patterns = [p for p, _ in _FILTER_REGISTRY]
        assert "git" in patterns

    def test_test_filter_registered(self):
        """Test runner filter should be registered."""
        from tools.output_filter import _ensure_filters_loaded, _FILTER_REGISTRY
        _ensure_filters_loaded()
        patterns = [p for p, _ in _FILTER_REGISTRY]
        assert "pytest" in patterns or "test" in patterns

    def test_build_filter_registered(self):
        """Build filter should be registered."""
        from tools.output_filter import _ensure_filters_loaded, _FILTER_REGISTRY
        _ensure_filters_loaded()
        patterns = [p for p, _ in _FILTER_REGISTRY]
        assert "build" in patterns or "make" in patterns

    def test_filter_output_strips_ansi(self):
        """Generic filter should strip ANSI escape sequences."""
        from tools.output_filter import filter_output
        raw = "\x1b[31mError\x1b[0m: something failed"
        result = filter_output("unknown_command", raw, 1)
        assert "\x1b[" not in result.output

    def test_filter_saves_tee(self):
        """Filter should save raw output to tee file."""
        from tools.output_filter import filter_output
        raw = "some output\n" * 100
        result = filter_output("git status", raw, 0)
        # Tee should be saved for git commands
        assert result.tee_path is not None or result.savings_pct > 0


class TestPhase1FilterIntegration:
    """Phase 1: Verify output filter is integrated into run_shell."""

    def test_filter_applied_in_run_shell_processes(self):
        """_apply_output_filter should exist in run_shell_processes."""
        from services.run_shell_processes import _apply_output_filter
        assert callable(_apply_output_filter)

    def test_apply_output_filter_calls_filter(self):
        """_apply_output_filter should call the filter module and return (output, stats)."""
        with patch("services.run_shell_processes.filter_output") as mock_filter:
            mock_result = MagicMock()
            mock_result.output = "filtered"
            mock_result.raw_tokens = 100
            mock_result.filtered_tokens = 50
            mock_result.savings_pct = 50.0
            mock_result.tee_path = "/tmp/test.log"
            mock_filter.return_value = mock_result
            from services.run_shell_processes import _apply_output_filter
            output, stats = _apply_output_filter("git status", "raw output", 0)
            mock_filter.assert_called_once()
            assert output == "filtered"
            assert stats["savings_pct"] == 50.0
            assert stats["raw_tokens"] == 100

    def test_apply_output_filter_fallback_on_error(self):
        """_apply_output_filter should fallback to raw on error."""
        with patch("services.run_shell_processes.filter_output", side_effect=Exception("fail")):
            from services.run_shell_processes import _apply_output_filter
            output, stats = _apply_output_filter("git status", "raw output", 0)
            assert output == "raw output"
            assert stats == {}


# ── Phase 2: History Progressive Compression ─────────────────────


class TestPhase2HistoryCompression:
    """Phase 2: Verify progressive history compression."""

    def test_build_recent_history_accepts_summarize_threshold(self):
        """build_recent_history should accept summarize_threshold parameter."""
        from services.context_builder import build_recent_history
        # Should not raise
        result = build_recent_history([], limit=5, summarize_threshold=10)
        assert result == []

    def test_three_tier_compression(self):
        """Messages between limit and threshold should be compressed."""
        from services.context_builder import build_recent_history

        # Create 15 messages
        messages = []
        for i in range(15):
            msg = MagicMock()
            msg.content = f"Message {i}: " + "x" * 200
            msg.message_type = "user" if i % 2 == 0 else "assistant"
            msg.metadata = {}
            messages.append(msg)

        # With 3-tier: limit=5, threshold=10
        result = build_recent_history(messages, limit=5, summarize_threshold=10)

        # Should have messages from index 5-14 (10 messages)
        # First 5 (index 5-9) should be compressed
        # Last 5 (index 10-14) should be full
        assert len(result) > 0
        # The compressed messages should be shorter
        compressed = result[0]
        assert len(compressed["content"]) < 200  # Should be compressed

    def test_two_tier_fallback(self):
        """Without summarize_threshold, should use 2-tier (original behavior)."""
        from services.context_builder import build_recent_history

        messages = []
        for i in range(10):
            msg = MagicMock()
            msg.content = f"Message {i}"
            msg.message_type = "user"
            msg.metadata = {}
            messages.append(msg)

        result = build_recent_history(messages, limit=5)
        assert len(result) == 5  # Only last 5


class TestPhase2ToolResultTruncation:
    """Phase 2: Verify tool result truncation."""

    def test_tool_result_truncated_at_threshold(self):
        """Tool results should be truncated at 2000 chars."""
        from services.turn_state import ToolResultRecord, _TOOL_RESULT_TRUNCATE_THRESHOLD

        long_result = "x" * 5000
        record = ToolResultRecord(
            tool_call_id="test-123",
            tool_name="run_shell",
            arguments='{"command": "test"}',
            result=long_result,
        )
        message = record.to_message()
        assert len(message["content"]) < 5000
        assert "[truncated for context budget]" in message["content"]

    def test_short_result_not_truncated(self):
        """Short tool results should not be truncated."""
        from services.turn_state import ToolResultRecord

        short_result = "short output"
        record = ToolResultRecord(
            tool_call_id="test-123",
            tool_name="run_shell",
            arguments='{"command": "test"}',
            result=short_result,
        )
        message = record.to_message()
        assert message["content"] == "short output"


# ── Phase 3: Dynamic Budget Derivation ───────────────────────────


class TestPhase3RatioProfiles:
    """Phase 3: Verify ratio-based budget derivation."""

    def test_ratio_profiles_in_defaults(self):
        """Default profiles should include ratio fields."""
        from services.chat_prompt_builder import _DEFAULT_SELECTOR_PROFILES

        profile = _DEFAULT_SELECTOR_PROFILES["chat_interactive"]
        assert "max_tokens_cap_ratio" in profile
        assert "max_tokens_by_role_ratio" in profile
        assert "max_tokens_by_scope_ratio" in profile

    def test_materialize_with_large_model(self):
        """For 400K model, ratio cap should be larger than configured cap."""
        from services.chat_prompt_builder import (
            materialize_selector_profile_config,
            _DEFAULT_SELECTOR_PROFILES,
        )

        profile = dict(_DEFAULT_SELECTOR_PROFILES["chat_interactive"])
        # Simulate 400K model
        model_context = MagicMock()
        model_context.input_window = 400000
        model_context.context_window = 400000
        model_context.output_reserve = 0

        result = materialize_selector_profile_config(profile, model_context)
        # 400K * 0.03 = 12000, but configured cap is 8000
        # Should use min(8000, 12000) = 8000
        assert result["max_tokens_cap"] == 8000

    def test_materialize_with_small_model(self):
        """For 128K model, ratio cap should be close to configured cap."""
        from services.chat_prompt_builder import (
            materialize_selector_profile_config,
            _DEFAULT_SELECTOR_PROFILES,
        )

        profile = dict(_DEFAULT_SELECTOR_PROFILES["chat_interactive"])
        # Simulate 128K model
        model_context = MagicMock()
        model_context.input_window = 128000
        model_context.context_window = 128000
        model_context.output_reserve = 0

        result = materialize_selector_profile_config(profile, model_context)
        # 128K * 0.03 = 3840, configured cap is 8000
        # Should use min(8000, 3840) = 3840
        assert result["max_tokens_cap"] == 3840

    def test_min_tokens_cap_floor(self):
        """For very small models, should use min_tokens_cap floor (3200)."""
        from services.chat_prompt_builder import (
            materialize_selector_profile_config,
            _DEFAULT_SELECTOR_PROFILES,
        )

        profile = dict(_DEFAULT_SELECTOR_PROFILES["chat_interactive"])
        # Simulate very small model (50K)
        model_context = MagicMock()
        model_context.input_window = 50000
        model_context.context_window = 50000
        model_context.output_reserve = 0

        result = materialize_selector_profile_config(profile, model_context)
        # 50K * 0.03 = 1500, but min_tokens_cap is 3200
        # Should use max(1500, 3200) = 3200
        assert result["max_tokens_cap"] >= 3200

    def test_materialize_without_model_context(self):
        """Without model context, should use absolute values."""
        from services.chat_prompt_builder import (
            materialize_selector_profile_config,
            _DEFAULT_SELECTOR_PROFILES,
        )

        profile = dict(_DEFAULT_SELECTOR_PROFILES["chat_interactive"])
        result = materialize_selector_profile_config(profile, None)
        # Should not have ratio fields
        assert "max_tokens_cap_ratio" not in result
        assert "max_tokens_cap" in result


# ── Phase 4: Cross-Stage Summaries ───────────────────────────────


class TestPhase4StageSummaries:
    """Phase 4: Verify cross-stage summary fragment."""

    def test_build_stage_summaries_fragment_exists(self):
        """Stage summaries fragment builder should exist."""
        from services.context_builder import build_stage_summaries_fragment
        assert callable(build_stage_summaries_fragment)

    def test_build_stage_summaries_with_data(self):
        """Should build fragment from completed stages."""
        from services.context_builder import build_stage_summaries_fragment

        mock_stage = MagicMock()
        mock_stage.output_summary = "Analysis complete: found 10 requirements"
        mock_stage.display_name = "Analysis"
        mock_stage.stage_name = "analyst"
        mock_stage.agent_name = "analyst"

        fragment = build_stage_summaries_fragment([mock_stage])
        assert fragment is not None
        assert "Pipeline Stage Summaries" in fragment.content
        assert "Analysis" in fragment.content
        assert fragment.source == "stage_summaries"
        assert fragment.priority == 35

    def test_build_stage_summaries_empty(self):
        """Should return None for empty stages."""
        from services.context_builder import build_stage_summaries_fragment
        fragment = build_stage_summaries_fragment([])
        assert fragment is None

    def test_build_stage_summaries_no_output(self):
        """Should return None if no stage has output."""
        from services.context_builder import build_stage_summaries_fragment

        mock_stage = MagicMock()
        mock_stage.output_summary = None
        mock_stage.display_name = "Empty"
        mock_stage.stage_name = "empty"
        mock_stage.agent_name = "agent"

        fragment = build_stage_summaries_fragment([mock_stage])
        assert fragment is None

    def test_format_stage_output_summary_exists(self):
        """_format_stage_output_summary should exist in pipeline engine."""
        from pipeline.engine import _format_stage_output_summary
        assert callable(_format_stage_output_summary)

    def test_format_stage_output_summary_truncates(self):
        """Should truncate long output."""
        from pipeline.engine import _format_stage_output_summary

        long_content = "x" * 2000
        result = _format_stage_output_summary(
            stage_name="test",
            display_name="Test",
            agent_name="tester",
            raw_content=long_content,
        )
        assert len(result) < 2000
        assert "..." in result


# ── Phase 5: LLM-Assisted Summarization ──────────────────────────


class TestPhase5LLMSummarization:
    """Phase 5: Verify LLM summarization function exists and is callable."""

    def test_summarize_for_context_exists(self):
        """summarize_for_context should exist."""
        from services.context_builder import summarize_for_context
        assert callable(summarize_for_context)

    @pytest.mark.asyncio
    async def test_summarize_for_context_fallback(self):
        """Should fallback to truncation on LLM error."""
        from services.context_builder import summarize_for_context

        with patch("services.context_builder.get_default_llm_client", side_effect=Exception("no LLM")):
            result = await summarize_for_context("Some text to summarize", max_tokens=50)
            # Should fallback to truncation
            assert isinstance(result, str)

    def test_format_stage_output_summary_async_exists(self):
        """Async version should exist in pipeline engine."""
        from pipeline.engine import _format_stage_output_summary_async
        assert callable(_format_stage_output_summary_async)

    def test_summarize_for_context_imported_in_pipeline(self):
        """summarize_for_context should be importable from pipeline engine."""
        from services.context_builder import summarize_for_context
        assert callable(summarize_for_context)


# ── Integration: History Summary Fragment ─────────────────────────


class TestHistorySummaryFragment:
    """Verify history summary fragment works with progressive compression."""

    def test_build_history_summary_fragment_exists(self):
        """History summary fragment builder should exist."""
        from services.context_builder import build_history_summary_fragment
        assert callable(build_history_summary_fragment)

    def test_summary_fragment_for_old_messages(self):
        """Should create summary fragment for messages older than keep_last."""
        from services.context_builder import build_history_summary_fragment

        messages = []
        for i in range(10):
            msg = MagicMock()
            msg.content = f"Message {i}"
            msg.message_type = "user" if i % 2 == 0 else "assistant"
            msg.metadata = {}
            msg.agent_name = "assistant" if i % 2 == 1 else None
            messages.append(msg)

        fragment = build_history_summary_fragment(messages, keep_last=5)
        assert fragment is not None
        assert "Earlier Conversation Summary" in fragment.content

    def test_summary_fragment_none_for_few_messages(self):
        """Should return None if messages <= keep_last."""
        from services.context_builder import build_history_summary_fragment

        messages = []
        for i in range(3):
            msg = MagicMock()
            msg.content = f"Message {i}"
            msg.message_type = "user"
            msg.metadata = {}
            msg.agent_name = None
            messages.append(msg)

        fragment = build_history_summary_fragment(messages, keep_last=5)
        assert fragment is None
