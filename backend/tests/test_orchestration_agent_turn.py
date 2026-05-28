import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from services.orchestration_agent_turn import (
    OrchestrationAgentTurnDeps,
    StreamOrchestrationAgentTurnDeps,
    iter_stream_orchestration_agent_turn_events,
    run_orchestration_agent_turn,
)
from services.task_run_control import TaskRunCancelledError
from services.turn_state import TurnContextState


class FakeLLMClient:
    model = "fake-model"

    async def chat_with_tools(self, messages, tools):
        return {"content": "Implemented the requested orchestration slice.", "tool_calls": []}


class FakeStreamLLMClient:
    model = "fake-stream-model"

    async def chat_stream(self, messages, tools):
        yield {"type": "content", "delta": "Streamed "}
        yield {
            "type": "done",
            "full_content": "Streamed orchestration response.",
            "tool_calls": [],
            "usage": {"total_tokens": 5},
            "finish_reason": "stop",
            "timings": {"completed_ms": 7},
        }


class FakeStreamToolLLMClient:
    model = "fake-stream-tool-model"

    def __init__(self):
        self.calls = 0

    async def chat_stream(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "done",
                "full_content": "",
                "tool_calls": [
                    {
                        "id": "call-run-shell",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"command":"pytest -q"}',
                        },
                    }
                ],
                "usage": {"total_tokens": 8},
                "finish_reason": "tool_calls",
                "timings": {"completed_ms": 3},
            }
            return

        yield {
            "type": "done",
            "full_content": "Tests finished.",
            "tool_calls": [],
            "usage": {"total_tokens": 4},
            "finish_reason": "stop",
            "timings": {"completed_ms": 5},
        }


async def test_run_orchestration_agent_turn_records_lifecycle_and_saves_message(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    saved_messages = []
    memory_jobs = []
    collaboration_calls = []
    try:
        chatroom = fresh_db.Chatroom(title="Agent turn")
        agent = fresh_db.Agent(agent_type="developer", name="Developer", role="developer")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Run turn",
            user_request="Implement.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        def ensure_collaboration_context(agents, chatroom_id):
            collaboration_calls.append((len(agents), chatroom_id))

        async def prepare_chat_turn_runtime(**kwargs):
            assert kwargs["previous_agent_work"] == "Prior work"
            return SimpleNamespace(
                llm_client=FakeLLMClient(),
                agent_label="Developer",
                recent_messages=[],
                available_tools=[],
                tool_schemas=[],
                runtime_kwargs={},
                turn_state=TurnContextState(),
            )

        def build_context_compaction_callback(*args, **kwargs):
            return lambda *_args, **_kwargs: None

        def assemble_chat_messages(**kwargs):
            assert kwargs["agent_name"] == "Developer"
            assert kwargs["chatroom"].id == chatroom.id
            return [{"role": "user", "content": kwargs["user_message"]}]

        async def save_message(**kwargs):
            saved = SimpleNamespace(id=42, created_at=datetime.now(), **kwargs)
            saved_messages.append(saved)
            return saved

        def message_metadata(client_turn_id):
            return {"client_turn_id": client_turn_id}

        def schedule_memory_extraction(agent_obj, request, response):
            memory_jobs.append((agent_obj.id, request, response))

        deps = OrchestrationAgentTurnDeps(
            ensure_collaboration_context=ensure_collaboration_context,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            build_context_compaction_callback=build_context_compaction_callback,
            assemble_chat_messages=assemble_chat_messages,
            save_message=save_message,
            message_metadata=message_metadata,
            schedule_memory_extraction=schedule_memory_extraction,
            max_tool_iterations=2,
        )

        content, message = await run_orchestration_agent_turn(
            deps=deps,
            agent=agent,
            chatroom_id=chatroom.id,
            project=None,
            agents=[agent],
            user_message="Implement this.",
            extra_context="Prior work",
            db=db,
            client_turn_id="turn-1",
            task_run=task_run,
        )

        assert content == "Implemented the requested orchestration slice."
        assert message.id == 42
        assert saved_messages[0].metadata == {"client_turn_id": "turn-1"}
        assert collaboration_calls == [(1, chatroom.id)]
        assert memory_jobs == [(agent.id, "Implement this.", content)]
        event_types = [event.event_type for event in task_run.events]
        assert event_types == ["agent_turn_started", "agent_turn_completed"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_orchestration_agent_turn_passes_runtime_environment_context(fresh_db, monkeypatch):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    captured = {}
    try:
        chatroom = fresh_db.Chatroom(title="Runtime context turn")
        agent = fresh_db.Agent(agent_type="developer", name="Developer", role="developer")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)

        monkeypatch.setattr(
            "services.orchestration_chat_profile.build_runtime_environment_context",
            lambda project: "## Runtime Environment\n- Recommended Python command for this session: /usr/bin/python3",
        )
        monkeypatch.setattr(
            "services.orchestration_agent_turn.make_nonstream_audit_callbacks",
            lambda **kwargs: {
                "before_llm_call": None,
                "on_llm_response": None,
                "on_llm_error": None,
                "on_tool_round": None,
            },
        )

        async def prepare_chat_turn_runtime(**kwargs):
            return SimpleNamespace(
                llm_client=FakeLLMClient(),
                agent_label="Developer",
                recent_messages=[],
                available_tools=[],
                tool_schemas=[],
                runtime_kwargs={},
                turn_state=TurnContextState(),
            )

        def assemble_chat_messages(**kwargs):
            captured["runtime_context"] = kwargs.get("runtime_context")
            return [{"role": "user", "content": kwargs["user_message"]}]

        async def save_message(**kwargs):
            return SimpleNamespace(id=42, created_at=datetime.now(), **kwargs)

        deps = OrchestrationAgentTurnDeps(
            ensure_collaboration_context=lambda agents, chatroom_id: None,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            build_context_compaction_callback=lambda *args, **kwargs: None,
            assemble_chat_messages=assemble_chat_messages,
            save_message=save_message,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            schedule_memory_extraction=lambda *args, **kwargs: None,
            max_tool_iterations=1,
        )

        content, message = await run_orchestration_agent_turn(
            deps=deps,
            agent=agent,
            chatroom_id=chatroom.id,
            project=None,
            agents=[agent],
            user_message="Implement this.",
            extra_context="",
            db=db,
            client_turn_id="turn-ctx",
            task_run=None,
        )

        assert content == "Implemented the requested orchestration slice."
        assert message.id == 42
        assert captured["runtime_context"].startswith("## Runtime Environment")
        assert "/usr/bin/python3" in captured["runtime_context"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_iter_stream_orchestration_agent_turn_events_records_start_and_yields_agent(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    collaboration_calls = []
    try:
        chatroom = fresh_db.Chatroom(title="Stream agent turn")
        agent = fresh_db.Agent(agent_type="developer", name="Developer", role="developer")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Stream turn",
            user_request="Stream.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        def ensure_collaboration_context(agents, chatroom_id):
            collaboration_calls.append((len(agents), chatroom_id))

        async def prepare_chat_turn_runtime(**kwargs):
            assert kwargs["recent_message_limit"] == 6
            assert kwargs["inter_agent_messages"] == [{"content": "handoff"}]
            return SimpleNamespace(
                llm_client=FakeStreamLLMClient(),
                agent_label="Developer",
                recent_messages=[],
                available_tools=[],
                tool_schemas=[],
                runtime_kwargs={},
                turn_state=TurnContextState(),
            )

        def assemble_chat_messages(**kwargs):
            return [
                {"role": "system", "content": "system"},
                {"role": "user", "content": kwargs["user_message"]},
            ]

        deps = StreamOrchestrationAgentTurnDeps(
            ensure_collaboration_context=ensure_collaboration_context,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            assemble_chat_messages=assemble_chat_messages,
            build_llm_card_payload=lambda **kwargs: {"agent": kwargs["agent_name"], "duration_ms": kwargs["duration_ms"]},
            snapshot_messages=lambda messages: list(messages),
            preview_tool_calls=lambda tool_calls: [],
            format_prompt_messages=lambda messages: "formatted",
            tool_result_success=lambda result: True,
            max_tool_iterations=2,
        )

        events = [
            event
            async for event in iter_stream_orchestration_agent_turn_events(
                deps=deps,
                agent=agent,
                chatroom_id=chatroom.id,
                chatroom=chatroom,
                project=None,
                agents=[agent],
                user_message="Stream this.",
                db=db,
                client_turn_id="stream-turn",
                inter_agent_messages=[{"content": "handoff"}],
                task_run=task_run,
            )
        ]

        assert collaboration_calls == [(1, chatroom.id)]
        assert events[-1]["type"] == "turn_complete"
        assert events[-1]["agent"] is agent
        assert events[-1]["content"] == "Streamed orchestration response."
        db.refresh(task_run)
        assert task_run.events[0].event_type == "agent_turn_started"
        assert task_run.events[0].summary is None
        assert json.loads(task_run.events[0].payload_json)["client_turn_id"] == "stream-turn"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_iter_stream_orchestration_agent_turn_events_passes_runtime_environment_context(fresh_db, monkeypatch):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    captured = {}
    try:
        chatroom = fresh_db.Chatroom(title="Runtime context stream")
        agent = fresh_db.Agent(agent_type="developer", name="Developer", role="developer")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)

        monkeypatch.setattr(
            "services.orchestration_chat_profile.build_runtime_environment_context",
            lambda project: "## Runtime Environment\n- Host OS: Linux\n- Recommended Python command for this session: /usr/bin/python3",
        )
        monkeypatch.setattr(
            "services.orchestration_agent_turn.make_stream_audit_before_event",
            lambda **kwargs: None,
        )

        async def prepare_chat_turn_runtime(**kwargs):
            return SimpleNamespace(
                llm_client=FakeStreamLLMClient(),
                agent_label="Developer",
                recent_messages=[],
                available_tools=[],
                tool_schemas=[],
                runtime_kwargs={},
                turn_state=TurnContextState(),
            )

        def assemble_chat_messages(**kwargs):
            captured["runtime_context"] = kwargs.get("runtime_context")
            return [
                {"role": "system", "content": "system"},
                {"role": "user", "content": kwargs["user_message"]},
            ]

        deps = StreamOrchestrationAgentTurnDeps(
            ensure_collaboration_context=lambda agents, chatroom_id: None,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            assemble_chat_messages=assemble_chat_messages,
            build_llm_card_payload=lambda **kwargs: {"agent": kwargs["agent_name"], "duration_ms": kwargs["duration_ms"]},
            snapshot_messages=lambda messages: list(messages),
            preview_tool_calls=lambda tool_calls: [],
            format_prompt_messages=lambda messages: "formatted",
            tool_result_success=lambda result: True,
            max_tool_iterations=2,
        )

        events = [
            event
            async for event in iter_stream_orchestration_agent_turn_events(
                deps=deps,
                agent=agent,
                chatroom_id=chatroom.id,
                chatroom=chatroom,
                project=None,
                agents=[agent],
                user_message="Stream this.",
                db=db,
                client_turn_id="stream-ctx",
                task_run=None,
            )
        ]

        assert events[-1]["type"] == "turn_complete"
        assert captured["runtime_context"].startswith("## Runtime Environment")
        assert "/usr/bin/python3" in captured["runtime_context"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_iter_stream_orchestration_agent_turn_events_streams_run_shell_progress(fresh_db, monkeypatch):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    stored_cards = []
    captured_execute = {}
    try:
        chatroom = fresh_db.Chatroom(title="Stream shell progress")
        agent = fresh_db.Agent(agent_type="tester", name="Tester", role="tester")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Stream shell",
            user_request="Run tests.",
            client_turn_id="stream-turn",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        async def fake_store_runtime_card(chatroom_id, payload):
            stored_cards.append((chatroom_id, payload))

        async def fake_execute(tool_name, **kwargs):
            captured_execute.update({"tool_name": tool_name, **kwargs})
            await kwargs["progress_callback"](
                {
                    "tail_output": "collected 3 items\nbackend/tests/test_example.py .",
                    "duration_ms": 1234,
                    "pid": 987,
                    "tracked_process": {"token": "tracked-shell"},
                }
            )
            return {
                "__catown_tool_result__": True,
                "tool_name": tool_name,
                "success": True,
                "status": "succeeded",
                "result": "3 passed",
            }

        monkeypatch.setattr("services.orchestration_agent_turn.store_runtime_card", fake_store_runtime_card)
        monkeypatch.setattr("tools.tool_registry.execute", fake_execute)
        monkeypatch.setattr(
            "services.orchestration_agent_turn.make_stream_audit_before_event",
            lambda **kwargs: None,
        )

        async def prepare_chat_turn_runtime(**kwargs):
            return SimpleNamespace(
                llm_client=FakeStreamToolLLMClient(),
                agent_label="Tester",
                recent_messages=[],
                available_tools=["run_shell"],
                tool_schemas=[{"type": "function", "function": {"name": "run_shell", "parameters": {}}}],
                runtime_kwargs={},
                turn_state=TurnContextState(),
            )

        deps = StreamOrchestrationAgentTurnDeps(
            ensure_collaboration_context=lambda agents, chatroom_id: None,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            assemble_chat_messages=lambda **kwargs: [
                {"role": "system", "content": "system"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            build_llm_card_payload=lambda **kwargs: {"agent": kwargs["agent_name"], "duration_ms": kwargs["duration_ms"]},
            snapshot_messages=lambda messages: list(messages),
            preview_tool_calls=lambda tool_calls: [
                {"name": "run_shell", "args_preview": "pytest -q", "index": 0, "id": "call-run-shell"}
            ] if tool_calls else [],
            format_prompt_messages=lambda messages: "formatted",
            tool_result_success=lambda result: True,
            max_tool_iterations=3,
        )

        events = [
            event
            async for event in iter_stream_orchestration_agent_turn_events(
                deps=deps,
                agent=agent,
                chatroom_id=chatroom.id,
                chatroom=chatroom,
                project=None,
                agents=[agent],
                user_message="Run tests.",
                db=db,
                client_turn_id="stream-turn",
                task_run=task_run,
            )
        ]

        assert captured_execute["tool_name"] == "run_shell"
        assert captured_execute["task_run_id"] == task_run.id
        assert captured_execute["client_turn_id"] == "stream-turn"
        assert captured_execute["tool_call_id"] == "call-run-shell"
        assert captured_execute["turn"] == 1
        assert callable(captured_execute["progress_callback"])
        assert stored_cards == [
            (
                chatroom.id,
                {
                    "type": "tool_call",
                    "source": "chatroom",
                    "agent": "Tester",
                    "tool": "run_shell",
                    "arguments": '{"command":"pytest -q"}',
                    "success": None,
                    "status": "running",
                    "blocked": False,
                    "result": "collected 3 items\nbackend/tests/test_example.py .",
                    "duration_ms": 1234,
                    "pid": 987,
                    "tracked_process": {"token": "tracked-shell"},
                    "tool_call_index": 0,
                    "tool_call_id": "call-run-shell",
                    "client_turn_id": "stream-turn",
                    "run_id": task_run.id,
                    "turn": 1,
                },
            )
        ]
        assert any(event["type"] == "tool_start" for event in events)
        assert any(event["type"] == "tool_result" for event in events)
        assert events[-1]["type"] == "turn_complete"
        assert events[-1]["content"] == "Tests finished."
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_orchestration_agent_turn_allows_tool_arguments_to_override_runtime_agent_id_without_duplicate_keyword_error(
    fresh_db,
    monkeypatch,
):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Memory lookup")
        agent = fresh_db.Agent(agent_type="developer", name="Developer", role="developer")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)

        class FakeMemoryToolLLMClient:
            model = "fake-memory-tool-model"

            def __init__(self):
                self.calls = 0

            async def chat_with_tools(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-memory",
                                "type": "function",
                                "function": {
                                    "name": "retrieve_memory",
                                    "arguments": '{"query":"bug","agent_id":42}',
                                },
                            }
                        ],
                    }
                return {
                    "content": "Memory inspected.",
                    "tool_calls": [],
                }

        captured_execute = {}

        async def fake_execute(tool_name, **kwargs):
            captured_execute["tool_name"] = tool_name
            captured_execute["kwargs"] = dict(kwargs)
            return {
                "__catown_tool_result__": True,
                "tool_name": tool_name,
                "success": True,
                "status": "succeeded",
                "result": "memory results",
            }

        monkeypatch.setattr("tools.tool_registry.execute", fake_execute)
        monkeypatch.setattr(
            "services.orchestration_agent_turn.make_nonstream_audit_callbacks",
            lambda **kwargs: {
                "before_llm_call": None,
                "on_llm_response": None,
                "on_llm_error": None,
                "on_tool_round": None,
            },
        )

        async def prepare_chat_turn_runtime(**kwargs):
            return SimpleNamespace(
                llm_client=FakeMemoryToolLLMClient(),
                agent_label="Developer",
                recent_messages=[],
                available_tools=["retrieve_memory"],
                tool_schemas=[{"type": "function", "function": {"name": "retrieve_memory", "parameters": {}}}],
                runtime_kwargs={"agent_id": agent.id, "agent_name": "Developer", "chatroom_id": chatroom.id},
                turn_state=TurnContextState(),
            )

        async def fake_save_message(**kwargs):
            return SimpleNamespace(id=77, **kwargs)

        deps = OrchestrationAgentTurnDeps(
            ensure_collaboration_context=lambda agents, chatroom_id: None,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            build_context_compaction_callback=lambda *args, **kwargs: None,
            assemble_chat_messages=lambda **kwargs: [
                {"role": "system", "content": "system"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            save_message=fake_save_message,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            schedule_memory_extraction=lambda *args, **kwargs: None,
            max_tool_iterations=3,
        )

        content, message = await run_orchestration_agent_turn(
            deps=deps,
            agent=agent,
            chatroom_id=chatroom.id,
            project=None,
            agents=[agent],
            user_message="Check memory.",
            extra_context="",
            db=db,
            client_turn_id="turn-memory",
            task_run=None,
        )

        assert content == "Memory inspected."
        assert message.id == 77
        assert captured_execute["tool_name"] == "retrieve_memory"
        assert captured_execute["kwargs"]["agent_id"] == 42
        assert captured_execute["kwargs"]["caller_agent_id"] == agent.id
        assert captured_execute["kwargs"]["caller_agent_name"] == "Developer"
        assert captured_execute["kwargs"]["chatroom_id"] == chatroom.id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_orchestration_agent_turn_stops_before_llm_when_task_run_cancelled(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Cancelled agent turn")
        agent = fresh_db.Agent(agent_type="developer", name="Developer", role="developer")
        db.add_all([chatroom, agent])
        db.commit()
        db.refresh(chatroom)
        db.refresh(agent)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="cancelled",
            title="Cancelled turn",
            user_request="Stop.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        class UnexpectedLLMClient:
            model = "unexpected"

            async def chat_with_tools(self, messages, tools):
                raise AssertionError("LLM should not be called after cancellation")

        async def prepare_chat_turn_runtime(**kwargs):
            return SimpleNamespace(
                llm_client=UnexpectedLLMClient(),
                agent_label="Developer",
                recent_messages=[],
                available_tools=[],
                tool_schemas=[],
                runtime_kwargs={},
                turn_state=TurnContextState(),
            )

        deps = OrchestrationAgentTurnDeps(
            ensure_collaboration_context=lambda agents, chatroom_id: None,
            prepare_chat_turn_runtime=prepare_chat_turn_runtime,
            build_context_compaction_callback=lambda *args, **kwargs: None,
            assemble_chat_messages=lambda **kwargs: [{"role": "user", "content": "Stop."}],
            save_message=lambda **kwargs: None,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            schedule_memory_extraction=lambda agent_obj, request, response: None,
            max_tool_iterations=1,
        )

        with pytest.raises(TaskRunCancelledError):
            await run_orchestration_agent_turn(
                deps=deps,
                agent=agent,
                chatroom_id=chatroom.id,
                project=None,
                agents=[agent],
                user_message="Stop.",
                extra_context="",
                db=db,
                client_turn_id="turn-cancelled",
                task_run=task_run,
            )
    finally:
        db.close()
