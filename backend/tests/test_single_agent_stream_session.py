import pytest

from services.single_agent_stream_session import (
    build_single_agent_stream_execution_context,
    build_single_agent_stream_execution_context_from_raw_inputs,
    build_single_agent_stream_loop_callbacks,
    build_single_agent_stream_raw_execution_inputs,
    build_single_agent_stream_session_deps,
    build_single_agent_stream_session_deps_from_execution_context,
    build_single_agent_stream_transport_context,
    iter_single_agent_stream_session,
)


class FakeLLMClient:
    model = "test-model"

    async def chat_stream(self, messages, tools):
        yield {"type": "content", "delta": "Hi"}
        yield {"type": "done", "full_content": "Hello world", "tool_calls": None}


@pytest.mark.asyncio
async def test_iter_single_agent_stream_session_renders_chunks_and_final_content():
    stored = []

    async def store_runtime_card(chatroom_id, payload):
        stored.append((chatroom_id, payload))

    results = [
        item
        async for item in iter_single_agent_stream_session(
            build_single_agent_stream_session_deps(
                llm_client=FakeLLMClient(),
                tools=None,
                turn_state=type("TurnState", (), {"protocol_messages": lambda self: []})(),
                agent_name="Analyst",
                client_turn_id="turn-1",
                assemble_messages=lambda turn_state: [{"role": "user", "content": "hello"}],
                execute_tool=lambda *args, **kwargs: None,
                build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
                snapshot_messages=lambda messages: list(messages),
                preview_tool_calls=lambda raw_tool_calls: [],
                format_prompt_messages=lambda messages: "formatted",
                tool_result_success=lambda result: True,
                serialize_payload=lambda payload: '{"type":"content","delta":"Hi"}' if payload.get("type") == "content" else "{}",
                store_runtime_card=store_runtime_card,
                public_runtime_card_payload=lambda payload: payload,
                chatroom_id=7,
                max_turns=1,
            )
        )
    ]

    assert results[0].chunk == "data: {}\n\n"
    assert results[1].chunk == 'data: {"type":"content","delta":"Hi"}\n\n'
    assert results[2].chunk == "data: {}\n\n"
    assert results[3].final_content == "Hello world"
    assert stored == [
        (
            7,
            {
                "type": "llm_call",
                "agent": "Analyst",
                "source": "chatroom",
                "client_turn_id": "turn-1",
            },
        )
    ]


def test_build_single_agent_stream_session_deps_from_execution_context_projects_runtime_fields():
    async def store_runtime_card(*args, **kwargs):
        return None

    execution = build_single_agent_stream_execution_context(
        llm_client=FakeLLMClient(),
        tools=None,
        turn_state=type("TurnState", (), {"protocol_messages": lambda self: []})(),
        assemble_messages=lambda turn_state: [{"role": "user", "content": "hello"}],
        execute_tool=lambda *args, **kwargs: None,
        build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
        snapshot_messages=lambda messages: list(messages),
        preview_tool_calls=lambda raw_tool_calls: [],
        format_prompt_messages=lambda messages: "formatted",
        tool_result_success=lambda result: True,
        serialize_payload=lambda payload: "{}",
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=lambda payload: payload,
        max_turns=1,
    )

    deps = build_single_agent_stream_session_deps_from_execution_context(
        execution=execution,
        agent_name="Analyst",
        client_turn_id="turn-1",
        chatroom_id=7,
    )

    assert deps.agent_name == "Analyst"
    assert deps.client_turn_id == "turn-1"
    assert deps.chatroom_id == 7
    assert deps.max_turns == 1


def test_build_single_agent_stream_execution_context_from_raw_inputs_projects_fields():
    async def store_runtime_card(*args, **kwargs):
        return None

    execution = build_single_agent_stream_execution_context_from_raw_inputs(
        build_single_agent_stream_raw_execution_inputs(
            llm_client=FakeLLMClient(),
            tools=None,
            turn_state=type("TurnState", (), {"protocol_messages": lambda self: []})(),
            assemble_messages=lambda turn_state: [{"role": "user", "content": "hello"}],
            execute_tool=lambda *args, **kwargs: None,
            build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
            snapshot_messages=lambda messages: list(messages),
            preview_tool_calls=lambda raw_tool_calls: [],
            format_prompt_messages=lambda messages: "formatted",
            tool_result_success=lambda result: True,
            serialize_payload=lambda payload: "{}",
            store_runtime_card=store_runtime_card,
            public_runtime_card_payload=lambda payload: payload,
            max_turns=1,
        )
    )

    assert execution.max_turns == 1
    assert execution.tools is None


def test_build_single_agent_stream_loop_callbacks_projects_fields():
    callbacks = build_single_agent_stream_loop_callbacks(
        assemble_messages=lambda turn_state: [{"role": "user", "content": "hello"}],
        execute_tool=lambda *args, **kwargs: None,
        build_llm_runtime_card=lambda *args, **kwargs: {"agent": "Analyst"},
        snapshot_messages=lambda messages: list(messages),
        preview_tool_calls=lambda raw_tool_calls: [],
        format_prompt_messages=lambda messages: "formatted",
        tool_result_success=lambda result: True,
    )

    assert callbacks.format_prompt_messages([]) == "formatted"
    assert callbacks.preview_tool_calls([]) == []


def test_build_single_agent_stream_transport_context_projects_fields():
    async def store_runtime_card(*args, **kwargs):
        return None

    transport = build_single_agent_stream_transport_context(
        serialize_payload=lambda payload: "{}",
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=lambda payload: payload,
    )

    assert transport.serialize_payload({}) == "{}"
    assert transport.public_runtime_card_payload({"type": "x"}) == {"type": "x"}
