from unittest.mock import MagicMock, patch

import pytest

from services.audit_recorder import make_nonstream_audit_callbacks, make_stream_audit_before_event
from services.nonstream_turn_executor import execute_non_stream_turn_loop
from services.stream_turn_executor import iter_stream_turn_events
from services.turn_state import TurnContextState


class FakeNonStreamLLMClient:
    model = "executor-nonstream-model"

    async def chat_with_tools(self, messages, tools):
        return {
            "content": "non-stream response",
            "tool_calls": [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }


class FakeStreamLLMClient:
    model = "executor-stream-model"

    async def chat_stream(self, messages, tools):
        yield {
            "type": "done",
            "full_content": "stream response",
            "tool_calls": [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }


@pytest.mark.asyncio
async def test_nonstream_executor_propagates_llm_client_model_into_audit_record():
    db = MagicMock()
    callbacks = make_nonstream_audit_callbacks(
        db=db,
        run_id=101,
        stage_id=None,
        agent_name="Developer",
    )

    with patch("services.audit_recorder.telemetry_writer") as writer:
        writer.create_llm_call.return_value = 1
        await execute_non_stream_turn_loop(
            llm_client=FakeNonStreamLLMClient(),
            tools=None,
            turn_state=TurnContextState(),
            assemble_messages=lambda _state: [{"role": "user", "content": "hello"}],
            execute_tool_call=_unexpected_tool_call,
            max_turns=1,
            before_llm_call=callbacks["before_llm_call"],
            on_llm_response=callbacks["on_llm_response"],
            on_llm_error=callbacks["on_llm_error"],
            on_tool_round=callbacks["on_tool_round"],
        )

        payload = writer.create_llm_call.call_args.args[0]

    assert payload["model"] == "executor-nonstream-model"


@pytest.mark.asyncio
async def test_stream_executor_propagates_llm_client_model_into_audit_record():
    db = MagicMock()
    before_event = make_stream_audit_before_event(
        db=db,
        run_id=202,
        stage_id=None,
        agent_name="Analyst",
    )

    with patch("services.audit_recorder.telemetry_writer") as writer:
        writer.create_llm_call.return_value = 2
        events = [
            event
            async for event in iter_stream_turn_events(
                llm_client=FakeStreamLLMClient(),
                tools=None,
                turn_state=TurnContextState(),
                agent_name="Analyst",
                client_turn_id="turn-1",
                assemble_messages=lambda _state: [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "hello"},
                ],
                execute_tool=_unexpected_stream_tool_call,
                build_llm_runtime_card=lambda frame, content, raw_tool_calls, llm_tool_calls, event: {
                    "model": frame.model,
                    "content": content,
                },
                snapshot_messages=lambda messages: list(messages),
                preview_tool_calls=lambda tool_calls: [],
                format_prompt_messages=lambda messages: messages,
                tool_result_success=lambda result: True,
                max_turns=1,
                before_event=before_event,
            )
        ]

        payload = writer.create_llm_call.call_args.args[0]

    assert payload["model"] == "executor-stream-model"
    assert events[0]["type"] == "agent_start"
    assert events[0]["model"] == "executor-stream-model"


async def _unexpected_tool_call(frame, tool_call):
    raise AssertionError("tool execution should not run in this test")


async def _unexpected_stream_tool_call(
    tool_name,
    tool_args,
    tool_args_str,
    tool_call_id,
    tool_index,
    turn_index,
):
    raise AssertionError("stream tool execution should not run in this test")
