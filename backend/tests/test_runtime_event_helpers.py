from services.runtime_event_helpers import build_context_compaction_callback, build_runtime_event_payload


class StubPolicy:
    def to_payload(self):
        return {"stage_name": "analysis"}


def test_runtime_event_payload_omits_absent_fields_and_serializes_policy():
    payload = build_runtime_event_payload(
        client_turn_id="turn-1",
        stage_policy=StubPolicy(),
        target_agent_name="Analyst",
        pipeline_id=None,
        extra_payload={"pipeline_run_id": 7, "ignored": None},
    )

    assert payload == {
        "target_agent_name": "Analyst",
        "client_turn_id": "turn-1",
        "stage_policy": {"stage_name": "analysis"},
        "pipeline_run_id": 7,
    }


def test_context_compaction_callback_deduplicates_and_formats_summary():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="analyst",
        extra_payload={"run_kind": "project_single_agent"},
    )

    diagnostics = {
        "selection_changed": True,
        "semantic_compaction": False,
        "event_kind": "selection_truncation",
        "summary": {"dropped_count": 2, "truncated_count": 1},
    }

    callback(diagnostics)
    callback(diagnostics)

    assert len(events) == 1
    event_type, summary, payload = events[0]
    assert event_type == "context_budget_event"
    assert summary == "analyst adjusted context budget (dropped=2, truncated=1)."
    assert payload["semantic_compaction"] is False
    assert payload["event_kind"] == "selection_truncation"
    assert payload["selector_diagnostics"] == diagnostics
    assert payload["run_kind"] == "project_single_agent"


def test_context_compaction_callback_keeps_semantic_compaction_event_type():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="analyst",
    )

    diagnostics = {
        "selection_changed": True,
        "semantic_compaction": True,
        "event_kind": "semantic_compaction",
        "summary": {"dropped_count": 4, "truncated_count": 0},
    }

    callback(diagnostics)

    assert len(events) == 1
    event_type, summary, payload = events[0]
    assert event_type == "context_compaction"
    assert summary == "analyst semantically compacted context (dropped=4, truncated=0)."
    assert payload["semantic_compaction"] is True
    assert payload["event_kind"] == "semantic_compaction"
    assert payload["selector_diagnostics"] == diagnostics


def test_context_compaction_callback_skips_non_compacted_payloads():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="developer",
        summary_noun="pipeline context",
    )

    callback({"selection_changed": False})
    callback({})

    assert events == []


def test_context_compaction_callback_emits_tool_output_budget_event():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="developer",
    )

    diagnostics = {
        "selection_changed": False,
        "semantic_compaction": False,
        "event_kind": "tool_output_budget",
        "prompt": {
            "tool_output_budget": {
                "summarized_message_count": 1,
                "estimated_saved_tokens": 2400,
                "estimated_savings_pct": 78.4,
            }
        },
        "summary": {"dropped_count": 0, "truncated_count": 0},
    }

    callback(diagnostics)

    assert len(events) == 1
    event_type, summary, payload = events[0]
    assert event_type == "context_budget_event"
    assert summary == "developer summarized tool output for context budget."
    assert payload["event_kind"] == "tool_output_budget"
    assert payload["semantic_compaction"] is False


def test_context_compaction_callback_emits_tool_schema_budget_event():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="developer",
    )

    diagnostics = {
        "selection_changed": False,
        "semantic_compaction": False,
        "event_kind": "tool_schema_budget",
        "prompt": {
            "tool_schema_budget": {
                "tool_count": 2,
                "tokens": 320,
            }
        },
        "summary": {"dropped_count": 0, "truncated_count": 0},
    }

    callback(diagnostics)

    assert len(events) == 1
    event_type, summary, payload = events[0]
    assert event_type == "context_budget_event"
    assert summary == "developer recorded tool schema cost for context budget."
    assert payload["event_kind"] == "tool_schema_budget"
    assert payload["semantic_compaction"] is False


def test_context_compaction_callback_emits_tool_schema_filter_savings_event():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="developer",
    )

    diagnostics = {
        "selection_changed": False,
        "semantic_compaction": False,
        "prompt": {
            "tool_schema_budget": {
                "tool_count": 0,
                "tokens": 0,
                "estimated_saved_tokens": 320,
            }
        },
        "summary": {"dropped_count": 0, "truncated_count": 0},
    }

    callback(diagnostics)

    assert len(events) == 1
    event_type, summary, payload = events[0]
    assert event_type == "context_budget_event"
    assert summary == "developer recorded tool schema cost for context budget."
    assert payload["event_kind"] == "tool_schema_budget"
