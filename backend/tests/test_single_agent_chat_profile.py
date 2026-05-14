import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.single_agent_chat_profile import (  # noqa: E402
    build_single_agent_stream_chat_profile,
    build_single_agent_sync_chat_profile,
)
from services.single_agent_session_orchestrator import (  # noqa: E402
    build_single_agent_raw_runtime_inputs,
    build_single_agent_stream_failure_policy,
    build_single_agent_stream_loop_callbacks,
    build_single_agent_stream_transport_context,
)


def _runtime_inputs():
    async def _save_message(**kwargs):
        return kwargs

    async def _publish_message(*args, **kwargs):
        return None

    async def _extract_memories(*args, **kwargs):
        return None

    return build_single_agent_raw_runtime_inputs(
        db=object(),
        task_run=None,
        chatroom_id=1,
        client_turn_id="turn-1",
        agent_id=7,
        agent_name="Developer",
        agent_type="developer",
        user_message="Fix the bug",
        save_message=_save_message,
        publish_message=_publish_message,
        record_turn_completed=lambda *args, **kwargs: None,
        message_metadata=lambda client_turn_id=None: {"client_turn_id": client_turn_id},
        compact_summary=lambda content: str(content),
        completion_summary="done",
        failure_summary="failed",
        extract_memories=_extract_memories,
    )


def test_build_single_agent_sync_chat_profile_wraps_execution_callable():
    runtime_inputs = _runtime_inputs()

    async def _execute_turn():
        return "ok"

    profile = build_single_agent_sync_chat_profile(
        runtime_inputs=runtime_inputs,
        execute_turn=_execute_turn,
        on_empty=lambda: None,
    )

    assert profile.session is not None
    assert profile.session.callbacks is not None
    assert profile.session.stream_transport is None


def test_build_single_agent_stream_chat_profile_wraps_stream_execution_context():
    runtime_inputs = _runtime_inputs()

    async def _execute_tool(*args, **kwargs):
        return "ok"

    loop_callbacks = build_single_agent_stream_loop_callbacks(
        assemble_messages=lambda turn_state: [{"role": "user", "content": "hi"}],
        execute_tool=_execute_tool,
        build_llm_runtime_card=lambda *args, **kwargs: {},
        snapshot_messages=lambda messages: list(messages),
        preview_tool_calls=lambda raw: [],
        format_prompt_messages=lambda messages: messages,
        tool_result_success=lambda result: True,
    )
    transport = build_single_agent_stream_transport_context(
        serialize_payload=lambda payload: str(payload),
        store_runtime_card=_execute_tool,
        public_runtime_card_payload=lambda payload: payload,
    )

    profile = build_single_agent_stream_chat_profile(
        runtime_inputs=runtime_inputs,
        llm_client=object(),
        tools=None,
        turn_state=object(),
        loop_callbacks=loop_callbacks,
        transport=transport,
        max_turns=3,
        stream_failure=build_single_agent_stream_failure_policy(),
    )

    assert profile.session is not None
    assert profile.session.callbacks is not None
    assert profile.session.stream_transport is not None
