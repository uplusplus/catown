import pytest

from services.single_agent_session_orchestrator import (
    build_unified_stream_single_agent_session_spec,
    build_unified_sync_single_agent_session_spec,
    ManagedSingleAgentSessionCallbacks,
    ManagedSingleAgentSessionSpec,
    iter_managed_single_agent_stream_session,
    run_managed_single_agent_sync_session,
    iter_unified_single_agent_stream_session,
    run_unified_single_agent_sync_session,
)
from services.single_agent_stream_session import SingleAgentStreamSessionDeps


@pytest.mark.asyncio
async def test_unified_single_agent_stream_session_delegates_to_stream_runner():
    class FakeLLM:
        model = "test"

        async def chat_stream(self, messages, tools):
            yield {"type": "done", "full_content": "Hello", "tool_calls": None}

    async def store_runtime_card(*args, **kwargs):
        return None

    rendered = [
        item
        async for item in iter_unified_single_agent_stream_session(
            build_unified_stream_single_agent_session_spec(
                deps=SingleAgentStreamSessionDeps(
                    llm_client=FakeLLM(),
                    tools=None,
                    turn_state=type("TurnState", (), {"protocol_messages": lambda self: []})(),
                    agent_name="Analyst",
                    client_turn_id="turn-1",
                    assemble_messages=lambda turn_state: [{"role": "user", "content": "hi"}],
                    execute_tool=lambda *args, **kwargs: None,
                    build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
                    snapshot_messages=lambda messages: list(messages),
                    preview_tool_calls=lambda raw_tool_calls: [],
                    format_prompt_messages=lambda messages: "formatted",
                    tool_result_success=lambda result: True,
                    serialize_payload=lambda payload: "{}",
                    store_runtime_card=store_runtime_card,
                    public_runtime_card_payload=lambda payload: payload,
                    chatroom_id=7,
                    max_turns=1,
                ),
            )
        )
    ]

    assert rendered[-1].final_content == "Hello"


@pytest.mark.asyncio
async def test_unified_single_agent_sync_session_delegates_to_sync_runner():
    async def execute_turn():
        return "Hello"

    result = await run_unified_single_agent_sync_session(
        build_unified_sync_single_agent_session_spec(
            execute_turn=execute_turn,
        )
    )

    assert result.final_content == "Hello"


@pytest.mark.asyncio
async def test_managed_single_agent_sync_session_delegates_to_unified_sync():
    calls = []

    async def execute_turn():
        return "Hello"

    async def finalize_success(content):
        calls.append(content)

    result = await run_managed_single_agent_sync_session(
        ManagedSingleAgentSessionSpec(
            session=build_unified_sync_single_agent_session_spec(
                execute_turn=execute_turn,
            ),
            callbacks=ManagedSingleAgentSessionCallbacks(
                finalize_success=finalize_success,
                finalize_failure=lambda exc: _async_stream_failure(str(exc)),
                serialize_payload=None,
            ),
        )
    )

    assert result.final_content == "Hello"
    assert calls == ["Hello"]


@pytest.mark.asyncio
async def test_managed_single_agent_stream_session_yields_terminal_payload():
    class FakeLLM:
        model = "test"

        async def chat_stream(self, messages, tools):
            yield {"type": "done", "full_content": "Hello", "tool_calls": None}

    async def store_runtime_card(*args, **kwargs):
        return None

    outcomes = [
        outcome
        async for outcome in iter_managed_single_agent_stream_session(
            ManagedSingleAgentSessionSpec(
                session=build_unified_stream_single_agent_session_spec(
                    deps=SingleAgentStreamSessionDeps(
                        llm_client=FakeLLM(),
                        tools=None,
                        turn_state=type("TurnState", (), {"protocol_messages": lambda self: []})(),
                        agent_name="Analyst",
                        client_turn_id="turn-1",
                        assemble_messages=lambda turn_state: [{"role": "user", "content": "hi"}],
                        execute_tool=lambda *args, **kwargs: None,
                        build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
                        snapshot_messages=lambda messages: list(messages),
                        preview_tool_calls=lambda raw_tool_calls: [],
                        format_prompt_messages=lambda messages: "formatted",
                        tool_result_success=lambda result: True,
                        serialize_payload=lambda payload: "{}",
                        store_runtime_card=store_runtime_card,
                        public_runtime_card_payload=lambda payload: payload,
                        chatroom_id=7,
                        max_turns=1,
                    ),
                ),
                callbacks=ManagedSingleAgentSessionCallbacks(
                    finalize_success=lambda final_content: _async_stream_finalize(final_content),
                    finalize_failure=lambda exc: _async_stream_failure(str(exc)),
                    serialize_payload=lambda payload: '{"type":"done"}',
                ),
            )
        )
    ]

    assert outcomes[-1].chunk == 'data: {"type":"done"}\n\n'
    assert outcomes[-1].payload == {"type": "done"}


async def _async_stream_finalize(final_content):
    return type("Result", (), {"payload": {"type": "done"}})()


async def _async_stream_failure(error_text):
    return type("Result", (), {"payload": {"type": "error", "error": error_text}})()
