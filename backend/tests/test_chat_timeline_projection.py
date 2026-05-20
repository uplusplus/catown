from datetime import datetime, timedelta


def _append_task_event(*args, **kwargs):
    from services.run_ledger import append_task_event

    return append_task_event(*args, **kwargs)


def _build_task_run_timeline_projection(*args, **kwargs):
    from services.chat_timeline_projection import build_task_run_timeline_projection

    return build_task_run_timeline_projection(*args, **kwargs)


def _build_chatroom_timeline_projection(*args, **kwargs):
    from services.chat_timeline_projection import build_chatroom_timeline_projection

    return build_chatroom_timeline_projection(*args, **kwargs)


def test_task_run_timeline_uses_backend_sequence_over_timestamps(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Timeline")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="running",
            title="Timeline run",
            client_turn_id="turn-1",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        first = _append_task_event(
            db,
            task_run,
            "llm_request_created",
            agent_name="Valet",
            summary="Valet sent prompt.",
            payload={"turn": 1, "occurred_at": "2026-05-15T10:00:02"},
        )
        second = _append_task_event(
            db,
            task_run,
            "llm_response_completed",
            agent_name="Valet",
            summary="Valet received answer.",
            payload={"turn": 1, "occurred_at": "2026-05-15T10:00:01"},
        )
        assert first is not None
        assert second is not None

        db.refresh(task_run)
        timeline = _build_task_run_timeline_projection(task_run)

        assert timeline["version"] == 2
        assert [step["event_type"] for step in timeline["steps"]] == [
            "llm_request_created",
            "llm_response_completed",
        ]
        assert [step["sequence"] for step in timeline["steps"]] == [1, 2]
        assert timeline["steps"][0]["kind"] == "llm"
        assert timeline["steps"][0]["phase"] == "request"
        assert timeline["steps"][1]["phase"] == "response"
    finally:
        db.close()


def test_chatroom_timeline_aggregates_task_runs_with_server_sequence(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Chatroom Timeline")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        base_time = datetime(2026, 5, 15, 10, 0, 0)
        first_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="completed",
            title="First",
            created_at=base_time,
        )
        second_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="running",
            title="Second",
            created_at=base_time + timedelta(minutes=1),
        )
        db.add_all([first_run, second_run])
        db.commit()
        db.refresh(first_run)
        db.refresh(second_run)

        _append_task_event(db, first_run, "agent_turn_started", agent_name="Valet", summary="First start.")
        _append_task_event(db, second_run, "agent_turn_started", agent_name="Coder", summary="Second start.")

        db.refresh(first_run)
        db.refresh(second_run)
        timeline = _build_chatroom_timeline_projection([first_run, second_run], chatroom_id=chatroom.id)

        assert timeline["scope"] == "chatroom"
        assert [step["sequence"] for step in timeline["steps"]] == [1, 2]
        assert [step["scope_sequence"] for step in timeline["steps"]] == [1, 1]
        assert [step["actor"] for step in timeline["steps"]] == ["Valet", "Coder"]
    finally:
        db.close()


def test_task_run_timeline_projects_llm_fact_events(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="LLM Facts")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="completed",
            title="LLM fact run",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        _append_task_event(
            db,
            task_run,
            "llm_request_created",
            agent_name="Valet",
            payload={
                "turn": 1,
                "step_id": "llm:Valet:1",
                "system_prompt": "System rules",
                "prompt_messages": [
                    {"role": "system", "content": "System rules"},
                    {"role": "user", "content": "actual user request"},
                ],
                "prompt_message_count": 2,
            },
        )
        _append_task_event(
            db,
            task_run,
            "llm_response_started",
            agent_name="Valet",
            payload={"turn": 1, "step_id": "llm:Valet:1"},
        )
        _append_task_event(
            db,
            task_run,
            "llm_response_completed",
            agent_name="Valet",
            payload={"turn": 1, "step_id": "llm:Valet:1", "response_preview": "Done."},
        )

        db.refresh(task_run)
        timeline = _build_task_run_timeline_projection(task_run)

        assert [step["phase"] for step in timeline["steps"]] == ["request", "response_started", "response"]
        assert all(step["kind"] == "llm" for step in timeline["steps"])
        assert all(step["step_id"] == "llm:Valet:1" for step in timeline["steps"])
        assert timeline["steps"][0]["state"] == "done"
        assert "### Prompt Messages" in timeline["steps"][0]["detail_content"]
        assert "actual user request" in timeline["steps"][0]["detail_content"]
        assert "prompt_messages" not in timeline["steps"][0]["facts"]
        assert timeline["steps"][0]["facts"]["prompt_message_count"] == 2
        assert timeline["steps"][0]["facts"]["prompt_preview"] == "actual user request"
        assert timeline["steps"][-1]["facts"]["response_preview"] == "Done."
    finally:
        db.close()


def test_task_run_timeline_projects_delegated_task_dispatch_fact(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Delegation Timeline")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="running",
            title="Delegation run",
            client_turn_id="parent-turn",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        _append_task_event(
            db,
            task_run,
            "delegated_task_dispatched",
            agent_name="Valet",
            payload={
                "task_id": "task-123",
                "task_title": "Verify behavior",
                "task_description": "Run the verification.",
                "from_agent": "Valet",
                "to_agent": "Tester",
                "child_client_turn_id": "delegate-task-123",
                "parent_task_run_id": task_run.id,
            },
        )

        db.refresh(task_run)
        timeline = _build_task_run_timeline_projection(task_run)
        step = timeline["steps"][0]

        assert step["kind"] == "delegation"
        assert step["phase"] == "dispatched"
        assert step["state"] == "live"
        assert step["actor"] == "Valet"
        assert step["summary"] == "Valet delegated 'Verify behavior' to Tester."
        assert step["facts"]["child_client_turn_id"] == "delegate-task-123"
        assert step["facts"]["task_description"] == "Run the verification."
    finally:
        db.close()
