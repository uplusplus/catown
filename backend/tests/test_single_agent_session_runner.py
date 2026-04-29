import pytest

from services.single_agent_session_runner import (
    build_single_agent_session_runner_deps_from_execution_context,
    build_single_agent_sync_execution_context,
    build_single_agent_sync_execution_context_from_raw_inputs,
    build_single_agent_sync_raw_execution_inputs,
    SingleAgentSessionRunnerDeps,
    run_single_agent_session,
)


@pytest.mark.asyncio
async def test_run_single_agent_session_finalizes_success():
    calls = []

    async def execute_turn():
        return "Hello world"

    async def finalize_success(content):
        calls.append(("success", content))

    result = await run_single_agent_session(
        SingleAgentSessionRunnerDeps(
            execute_turn=execute_turn,
            finalize_success=finalize_success,
        )
    )

    assert result.final_content == "Hello world"
    assert calls == [("success", "Hello world")]


@pytest.mark.asyncio
async def test_run_single_agent_session_handles_empty_and_failure():
    calls = []

    async def execute_empty():
        return ""

    async def on_empty():
        calls.append(("empty", None))

    async def execute_fail():
        raise RuntimeError("boom")

    async def finalize_failure(exc):
        calls.append(("failure", str(exc)))

    empty_result = await run_single_agent_session(
        SingleAgentSessionRunnerDeps(
            execute_turn=execute_empty,
            finalize_success=lambda content: None,
            on_empty=on_empty,
        )
    )
    failure_result = await run_single_agent_session(
        SingleAgentSessionRunnerDeps(
            execute_turn=execute_fail,
            finalize_success=lambda content: None,
            finalize_failure=finalize_failure,
        )
    )

    assert empty_result.final_content is None
    assert failure_result.final_content is None
    assert calls == [("empty", None), ("failure", "boom")]


@pytest.mark.asyncio
async def test_build_single_agent_session_runner_deps_from_execution_context_projects_fields():
    calls = []

    async def execute_turn():
        return "Hello"

    async def finalize_success(content):
        calls.append(("success", content))

    result = await run_single_agent_session(
        build_single_agent_session_runner_deps_from_execution_context(
            execution=build_single_agent_sync_execution_context(
                execute_turn=execute_turn,
            ),
            finalize_success=finalize_success,
        )
    )

    assert result.final_content == "Hello"
    assert calls == [("success", "Hello")]


@pytest.mark.asyncio
async def test_build_single_agent_sync_execution_context_from_raw_inputs_projects_fields():
    calls = []

    async def execute_turn():
        return "Hello"

    async def finalize_success(content):
        calls.append(("success", content))

    result = await run_single_agent_session(
        build_single_agent_session_runner_deps_from_execution_context(
            execution=build_single_agent_sync_execution_context_from_raw_inputs(
                build_single_agent_sync_raw_execution_inputs(
                    execute_turn=execute_turn,
                )
            ),
            finalize_success=finalize_success,
        )
    )

    assert result.final_content == "Hello"
    assert calls == [("success", "Hello")]
