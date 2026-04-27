from services.runtime_event_helpers import build_context_compaction_callback


def test_context_compaction_callback_deduplicates_and_formats_summary():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="analyst",
        extra_payload={"run_kind": "project_single_agent"},
    )

    diagnostics = {
        "compacted": True,
        "summary": {"dropped_count": 2, "truncated_count": 1},
    }

    callback(diagnostics)
    callback(diagnostics)

    assert len(events) == 1
    event_type, summary, payload = events[0]
    assert event_type == "context_compaction"
    assert summary == "analyst compacted context (dropped=2, truncated=1)."
    assert payload["compacted"] is True
    assert payload["selector_diagnostics"] == diagnostics
    assert payload["run_kind"] == "project_single_agent"


def test_context_compaction_callback_skips_non_compacted_payloads():
    events = []
    callback = build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: events.append((event_type, summary, payload)),
        agent_name="developer",
        summary_noun="pipeline context",
    )

    callback({"compacted": False})
    callback({})

    assert events == []
