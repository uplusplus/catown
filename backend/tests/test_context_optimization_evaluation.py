# -*- coding: utf-8 -*-
"""Tests for ADR-035 Phase 5 evaluation helpers."""


def test_context_optimization_evaluation_passes_when_targets_are_met():
    from services.context_optimization_evaluation import evaluate_context_optimization_metrics

    result = evaluate_context_optimization_metrics(
        {
            "false_early_semantic_compactions": 0,
            "inline_tool_output_tokens_before": 10000,
            "inline_tool_output_tokens_after": 3500,
            "average_input_tokens_before": 20000,
            "average_input_tokens_after": 12000,
            "time_to_first_token_ms_before": 2400,
            "time_to_first_token_ms_after": 1800,
            "task_success_regression_count": 0,
        }
    )

    assert result == {
        "kind": "context_optimization_evaluation",
        "overall_status": "pass",
        "metrics": {
            "false_early_semantic_compactions": {
                "value": 0,
                "status": "pass",
                "target": {"max": 0},
            },
            "inline_tool_output_token_reduction_ratio": {
                "value": 0.65,
                "status": "pass",
                "target": {"min": 0.60},
            },
            "average_input_token_reduction_ratio": {
                "value": 0.4,
                "status": "pass",
                "target": {"min": 0.30},
            },
            "time_to_first_token_improvement_ratio": {
                "value": 0.25,
                "status": "pass",
                "target": {"min": 0.0},
            },
            "task_success_regression_count": {
                "value": 0,
                "status": "pass",
                "target": {"max": 0},
            },
        },
    }


def test_context_optimization_evaluation_flags_failures_and_missing_data():
    from services.context_optimization_evaluation import evaluate_context_optimization_metrics

    result = evaluate_context_optimization_metrics(
        {
            "false_early_semantic_compactions": 1,
            "inline_tool_output_token_reduction_ratio": 0.25,
            "average_input_tokens_before": 20000,
            "average_input_tokens_after": 18000,
        }
    )

    assert result["overall_status"] == "needs_data"
    assert result["metrics"]["false_early_semantic_compactions"]["status"] == "fail"
    assert result["metrics"]["inline_tool_output_token_reduction_ratio"]["status"] == "fail"
    assert result["metrics"]["average_input_token_reduction_ratio"]["status"] == "fail"
    assert result["metrics"]["time_to_first_token_improvement_ratio"]["status"] == "missing"
    assert result["metrics"]["task_success_regression_count"]["status"] == "missing"


def test_context_optimization_evaluation_builds_observation_from_context_budget_events():
    from services.context_optimization_evaluation import (
        build_observation_from_context_budget_events,
        evaluate_context_budget_event_observations,
    )

    event = {
        "payload": {
            "selector_diagnostics": {
                "semantic_compaction": False,
                "context_pressure_kind": "tool_output_budget",
                "prompt": {
                    "total": {"tokens": 12000},
                    "tool_output_budget": {
                        "estimated_original_tokens": 10000,
                        "prompt_visible_tokens": 3500,
                        "estimated_saved_tokens": 6500,
                    },
                    "tool_schema_budget": {
                        "estimated_saved_tokens": 1500,
                    },
                },
            }
        }
    }

    observation = build_observation_from_context_budget_events(
        [event],
        time_to_first_token_ms_before=2400,
        time_to_first_token_ms_after=1800,
    )

    assert observation == {
        "event_count": 1,
        "tool_heavy_event_count": 1,
        "false_early_semantic_compactions": 0,
        "inline_tool_output_tokens_before": 10000,
        "inline_tool_output_tokens_after": 3500,
        "average_input_tokens_before": 20000.0,
        "average_input_tokens_after": 12000.0,
        "time_to_first_token_ms_before": 2400,
        "time_to_first_token_ms_after": 1800,
        "task_success_regression_count": 0,
    }

    result = evaluate_context_budget_event_observations(
        [event],
        time_to_first_token_ms_before=2400,
        time_to_first_token_ms_after=1800,
    )

    assert result["overall_status"] == "pass"
    assert result["observation"] == {"event_count": 1, "tool_heavy_event_count": 1}
    assert result["metrics"]["inline_tool_output_token_reduction_ratio"]["value"] == 0.65
    assert result["metrics"]["average_input_token_reduction_ratio"]["value"] == 0.4


def test_context_optimization_evaluation_counts_semantic_budget_compactions_as_false_early():
    from services.context_optimization_evaluation import build_observation_from_context_budget_events

    observation = build_observation_from_context_budget_events(
        [
            {
                "payload": {
                    "selector_diagnostics": {
                        "semantic_compaction": True,
                        "context_pressure_kind": "selector_budget_pressure",
                        "prompt": {
                            "total": {
                                "tokens": 3200,
                            }
                        },
                    }
                }
            }
        ]
    )

    assert observation["event_count"] == 1
    assert observation["false_early_semantic_compactions"] == 1


def test_context_optimization_evaluation_scores_representative_catown_traces():
    from services.context_optimization_evaluation import evaluate_context_budget_event_observations

    tool_heavy_event = {
        "payload": {
            "selector_diagnostics": {
                "semantic_compaction": False,
                "context_pressure_kind": "tool_output_budget",
                "prompt": {
                    "total": {"tokens": 12000},
                    "tool_output_budget": {
                        "estimated_original_tokens": 10000,
                        "prompt_visible_tokens": 3500,
                        "estimated_saved_tokens": 6500,
                    },
                    "tool_schema_budget": {"estimated_saved_tokens": 1500},
                },
            }
        }
    }
    schema_only_event = {
        "payload": {
            "selector_diagnostics": {
                "semantic_compaction": False,
                "context_pressure_kind": "tool_schema_budget",
                "prompt": {
                    "total": {"tokens": 4500},
                    "tool_schema_budget": {"estimated_saved_tokens": 900},
                },
            }
        }
    }

    result = evaluate_context_budget_event_observations(
        [tool_heavy_event, schema_only_event],
        time_to_first_token_ms_before=2400,
        time_to_first_token_ms_after=1900,
        task_success_regression_count=0,
    )

    assert result["overall_status"] == "pass"
    assert result["observation"] == {"event_count": 2, "tool_heavy_event_count": 1}
    assert result["metrics"]["false_early_semantic_compactions"]["value"] == 0
    assert result["metrics"]["inline_tool_output_token_reduction_ratio"]["value"] == 0.65
    assert result["metrics"]["average_input_token_reduction_ratio"]["value"] > 0.3
    assert result["metrics"]["time_to_first_token_improvement_ratio"]["value"] > 0
