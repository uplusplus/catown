import asyncio

import pytest

from services.single_agent_session_orchestrator import (
    build_single_agent_stream_runtime_profile_from_runtime,
    build_managed_single_agent_stream_session_spec,
    build_single_agent_sync_runtime_profile_from_runtime,
    build_managed_single_agent_sync_session_spec,
    build_unified_stream_single_agent_session_spec,
    build_unified_sync_single_agent_session_spec,
    iter_managed_single_agent_stream_runtime_profile,
    ManagedSingleAgentSessionCallbacks,
    ManagedSingleAgentSessionSpec,
    iter_managed_single_agent_stream_session,
    run_managed_single_agent_sync_runtime_profile,
    run_managed_single_agent_sync_session,
    iter_unified_single_agent_stream_session,
    run_unified_single_agent_sync_session,
)
from services.single_agent_session_runner import (
    build_single_agent_sync_execution_context,
)
from services.single_agent_stream_session import (
    build_single_agent_stream_execution_context,
    build_single_agent_stream_session_deps,
)


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
                deps=build_single_agent_stream_session_deps(
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
        build_managed_single_agent_sync_session_spec(
            execution=build_single_agent_sync_execution_context(
                execute_turn=execute_turn,
            ),
            callbacks=ManagedSingleAgentSessionCallbacks(
                finalize_success=finalize_success,
                finalize_failure=lambda exc: _async_stream_failure(str(exc)),
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
            build_managed_single_agent_stream_session_spec(
                deps=build_single_agent_stream_session_deps(
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
                    serialize_payload=lambda payload: '{"type":"done"}',
                    store_runtime_card=store_runtime_card,
                    public_runtime_card_payload=lambda payload: payload,
                    chatroom_id=7,
                    max_turns=1,
                ),
                callbacks=ManagedSingleAgentSessionCallbacks(
                    finalize_success=lambda final_content: _async_stream_finalize(final_content),
                    finalize_failure=lambda exc: _async_stream_failure(str(exc)),
                ),
            )
        )
    ]

    assert outcomes[-1].chunk == 'data: {"type":"done"}\n\n'
    assert outcomes[-1].payload == {"type": "done"}


@pytest.mark.asyncio
async def test_managed_single_agent_stream_session_requires_transport():
    class FakeLLM:
        model = "test"

        async def chat_stream(self, messages, tools):
            yield {"type": "done", "full_content": "Hello", "tool_calls": None}

    async def store_runtime_card(*args, **kwargs):
        return None

    with pytest.raises(
        ValueError,
        match="Managed single-agent stream session requires stream transport.",
    ):
        async for _ in iter_managed_single_agent_stream_session(
            ManagedSingleAgentSessionSpec(
                session=build_unified_stream_single_agent_session_spec(
                    deps=build_single_agent_stream_session_deps(
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
                ),
            )
        ):
            pass


@pytest.mark.asyncio
async def test_managed_single_agent_sync_session_profile_builder_composes_callbacks():
    calls = []

    async def execute_turn():
        return "Hello"

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return type("Saved", (), {"id": 7, "created_at": None})()

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    async def extract_memories(agent_id, agent_type, user_message, agent_response):
        calls.append(("memory", {"agent_id": agent_id, "content": agent_response}))

    result = await run_managed_single_agent_sync_runtime_profile(
        build_single_agent_sync_runtime_profile_from_runtime(
            db=object(),
            task_run=None,
            chatroom_id=7,
            client_turn_id="turn-1",
            agent_id=9,
            agent_name="Analyst",
            agent_type="Analyst",
            user_message="Need help",
            save_message=save_message,
            publish_message=publish_message,
            record_turn_completed=record_turn_completed,
            message_metadata=lambda client_turn_id, extra=None: {"client_turn_id": client_turn_id, "extra": extra},
            compact_summary=lambda content: content[:5],
            completion_summary="Analyst completed the turn.",
            failure_summary=lambda error: f"Agent response failed: {error}",
            extract_memories=extract_memories,
            stream_failure_message_metadata=lambda client_turn_id, extra=None: {"client_turn_id": client_turn_id, "extra": extra},
            execute_turn=execute_turn,
        )
    )
    await asyncio.sleep(0)

    assert result.final_content == "Hello"
    assert [name for name, _ in calls] == ["save", "publish", "complete"]


@pytest.mark.asyncio
async def test_managed_single_agent_stream_session_profile_builder_yields_terminal_payload():
    class FakeLLM:
        model = "test"

        async def chat_stream(self, messages, tools):
            yield {"type": "done", "full_content": "Hello", "tool_calls": None}

    async def store_runtime_card(*args, **kwargs):
        return None

    async def save_message(**kwargs):
        return type("Saved", (), {"id": 11, "created_at": None})()

    async def publish_message(*args, **kwargs):
        return None

    def record_turn_completed(*args, **kwargs):
        return None

    async def extract_memories(agent_id, agent_type, user_message, agent_response):
        return None

    outcomes = [
        outcome
        async for outcome in iter_managed_single_agent_stream_runtime_profile(
            build_single_agent_stream_runtime_profile_from_runtime(
                db=object(),
                task_run=None,
                chatroom_id=7,
                client_turn_id="turn-1",
                agent_id=9,
                agent_name="Analyst",
                agent_type="Analyst",
                user_message="Need help",
                save_message=save_message,
                publish_message=publish_message,
                record_turn_completed=record_turn_completed,
                message_metadata=lambda client_turn_id, extra=None: {"client_turn_id": client_turn_id, "extra": extra},
                compact_summary=lambda content: content[:5],
                completion_summary="Analyst completed the streaming turn.",
                failure_summary=lambda error: f"Streaming execution failed: {error}",
                extract_memories=extract_memories,
                stream_failure_message_metadata=lambda client_turn_id, extra=None: {"client_turn_id": client_turn_id, "extra": extra},
                llm_client=FakeLLM(),
                tools=None,
                turn_state=type("TurnState", (), {"protocol_messages": lambda self: []})(),
                assemble_messages=lambda turn_state: [{"role": "user", "content": "hi"}],
                execute_tool=lambda *args, **kwargs: None,
                build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
                snapshot_messages=lambda messages: list(messages),
                preview_tool_calls=lambda raw_tool_calls: [],
                format_prompt_messages=lambda messages: "formatted",
                tool_result_success=lambda result: True,
                serialize_payload=lambda payload: '{"type":"done"}',
                store_runtime_card=store_runtime_card,
                public_runtime_card_payload=lambda payload: payload,
                max_turns=1,
            )
        )
    ]

    assert outcomes[-1].chunk == 'data: {"type":"done"}\n\n'
    assert outcomes[-1].payload == {
        "type": "done",
        "agent_name": "Analyst",
        "message_id": 11,
        "client_turn_id": "turn-1",
    }


async def _async_stream_finalize(final_content):
    return type("Result", (), {"payload": {"type": "done"}})()


async def _async_stream_failure(error_text):
    return type("Result", (), {"payload": {"type": "error", "error": error_text}})()
