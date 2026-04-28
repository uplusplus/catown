import pytest

from services.single_agent_stream_session import (
    SingleAgentStreamSessionDeps,
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
            SingleAgentStreamSessionDeps(
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
