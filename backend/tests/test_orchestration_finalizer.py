from services.orchestration_finalizer import (
    fail_orchestration_task_run,
    finalize_orchestration_task_run,
    summarize_orchestration_result,
)


def test_summarize_orchestration_result_prefers_blocking_then_results_then_turns():
    assert summarize_orchestration_result(
        last_blocking_result="Blocking result",
        results=[{"content": "Last result"}],
        completed_turns=[{"content": "Last turn"}],
        fallback="Fallback",
    ) == "Blocking result"
    assert summarize_orchestration_result(
        results=[{"content": "Last result"}],
        completed_turns=[{"content": "Last turn"}],
        fallback="Fallback",
    ) == "Last result"
    assert summarize_orchestration_result(
        completed_turns=[{"content": "Last turn"}],
        fallback="Fallback",
    ) == "Last turn"
    assert summarize_orchestration_result(fallback="Fallback") == "Fallback"


def test_finalize_orchestration_task_run_completes_with_shared_summary(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Finalizer")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Finalize orchestration",
            user_request="Finish.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        finalized = finalize_orchestration_task_run(
            db,
            task_run,
            completed_turns=[{"agent": "developer", "content": "Implemented final change."}],
            fallback="No output.",
        )

        assert finalized.status == "completed"
        assert finalized.summary == "Implemented final change."
        assert finalized.completed_at is not None
    finally:
        db.close()


def test_fail_orchestration_task_run_records_terminal_event(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Failure finalizer")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Fail orchestration",
            user_request="Fail.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        finalized = fail_orchestration_task_run(
            db,
            task_run,
            summary="Orchestration failed at developer.",
            agent_name="developer",
            payload={"step_id": "step-1"},
        )

        assert finalized.status == "failed"
        assert finalized.summary == "Orchestration failed at developer."
        assert finalized.completed_at is not None
        assert len(finalized.events) == 1
        assert finalized.events[0].event_type == "task_run_failed"
        assert finalized.events[0].agent_name == "developer"
    finally:
        db.close()
