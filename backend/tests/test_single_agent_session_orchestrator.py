import pytest

from services.single_agent_session_orchestrator import (
    StreamSingleAgentSessionSpec,
    SyncSingleAgentSessionSpec,
    iter_stream_single_agent_session,
    run_sync_single_agent_session,
)
from services.single_agent_session_runner import SingleAgentSessionRunnerDeps
from services.single_agent_stream_session import SingleAgentStreamSessionDeps


@pytest.mark.asyncio
async def test_run_sync_single_agent_session_delegates_to_runner():
    calls = []

    async def execute_turn():
        return "Hello"

    async def finalize_success(content):
        calls.append(content)

    result = await run_sync_single_agent_session(
        SyncSingleAgentSessionSpec(
            deps=SingleAgentSessionRunnerDeps(
                execute_turn=execute_turn,
                finalize_success=finalize_success,
            )
        )
    )

    assert result.final_content == "Hello"
    assert calls == ["Hello"]


@pytest.mark.asyncio
async def test_iter_stream_single_agent_session_delegates_to_stream_runner():
    class FakeLLM:
        model = "test"

        async def chat_stream(self, messages, tools):
            yield {"type": "done", "full_content": "Hello", "tool_calls": None}

    async def store_runtime_card(*args, **kwargs):
        return None

    rendered = [
        item
        async for item in iter_stream_single_agent_session(
            StreamSingleAgentSessionSpec(
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
                )
            )
        )
    ]

    assert rendered[-1].final_content == "Hello"
