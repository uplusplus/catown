import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.orchestration_chat_profile import (  # noqa: E402
    build_orchestration_stream_turn_profile,
    build_orchestration_sync_turn_profile,
)
from services.turn_state import TurnContextState  # noqa: E402


def _runtime():
    return SimpleNamespace(
        llm_client=SimpleNamespace(model="fake-model"),
        agent_label="Developer",
        recent_messages=[],
        available_tools=[],
        runtime_kwargs={},
        turn_state=TurnContextState(),
    )


def test_build_orchestration_sync_turn_profile_exposes_turn_callbacks():
    profile = build_orchestration_sync_turn_profile(
        runtime=_runtime(),
        agent=SimpleNamespace(),
        db=object(),
        project=None,
        agents=[],
        chatroom=SimpleNamespace(id=1),
        user_message="Ship it",
        standalone_note="",
        task_run=None,
        client_turn_id="turn-1",
        max_turns=3,
        assemble_chat_messages=lambda **kwargs: [{"role": "user", "content": kwargs["user_message"]}],
        save_tool_progress=lambda payload: None,
        execute_tool=lambda *args, **kwargs: None,
        record_tool_call_started=lambda *args, **kwargs: None,
        record_tool_round=lambda *args, **kwargs: None,
        check_cancel=lambda *args, **kwargs: None,
        compaction_callback=None,
    )

    assert profile.runtime is not None
    assert callable(profile.assemble_messages)
    assert callable(profile.execute_tool_call)
    assert callable(profile.on_tool_round)


@pytest.mark.asyncio
async def test_build_orchestration_stream_turn_profile_passes_turn_index_to_tool_execution():
    captured = {}

    async def _execute_tool(
        tool_name,
        tool_args,
        tool_args_str,
        tool_call_id,
        tool_index,
        turn_index,
        progress_callback,
    ):
        captured.update(
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_args_str": tool_args_str,
                "tool_call_id": tool_call_id,
                "tool_index": tool_index,
                "turn_index": turn_index,
                "progress_callback": progress_callback,
            }
        )
        return "ok"

    profile = build_orchestration_stream_turn_profile(
        runtime=_runtime(),
        agent=SimpleNamespace(),
        db=object(),
        project=None,
        agents=[],
        chatroom=SimpleNamespace(id=1),
        user_message="Run tests",
        history_limit=4,
        standalone_note="",
        task_run=None,
        client_turn_id="turn-2",
        assemble_chat_messages=lambda **kwargs: [{"role": "user", "content": kwargs["user_message"]}],
        save_tool_progress=lambda payload: None,
        execute_tool=_execute_tool,
        record_tool_round=lambda *args, **kwargs: None,
        check_cancel=lambda *args, **kwargs: None,
        build_llm_card_payload=lambda **kwargs: kwargs,
    )

    result = await profile.execute_tool(
        "run_shell",
        {"command": "pytest -q"},
        '{"command":"pytest -q"}',
        "call-1",
        0,
        3,
    )

    assert result == "ok"
    assert captured == {
        "tool_name": "run_shell",
        "tool_args": {"command": "pytest -q"},
        "tool_args_str": '{"command":"pytest -q"}',
        "tool_call_id": "call-1",
        "tool_index": 0,
        "turn_index": 3,
        "progress_callback": captured["progress_callback"],
    }
    assert callable(captured["progress_callback"])
