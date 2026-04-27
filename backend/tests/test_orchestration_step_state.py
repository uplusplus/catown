from services.orchestration_step_state import OrchestrationStepOutputState, record_orchestration_step_output


def test_record_orchestration_step_output_tracks_results_turns_and_blocking_result():
    state = OrchestrationStepOutputState()

    record_orchestration_step_output(
        state,
        agent_name="analyst",
        content="Analysis complete.",
        dispatch_kind="blocking",
    )
    record_orchestration_step_output(
        state,
        agent_name="tester",
        content="Tests complete.",
        dispatch_kind="sidecar",
        include_result=False,
    )
    record_orchestration_step_output(
        state,
        agent_name="developer",
        content="",
        dispatch_kind="blocking",
    )

    assert state.results == [{"agent": "analyst", "content": "Analysis complete."}]
    assert state.completed_turns == [
        {"agent": "analyst", "content": "Analysis complete."},
        {"agent": "tester", "content": "Tests complete."},
    ]
    assert state.last_blocking_result == "Analysis complete."
