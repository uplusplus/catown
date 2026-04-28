from services.orchestration_guards import fail_orchestration_preflight, fail_recovery_guard


def test_fail_orchestration_preflight_records_sync_and_stream_failures(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Orchestration guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        sync_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Sync orchestration guard",
            user_request="Coordinate.",
        )
        stream_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Stream orchestration guard",
            user_request="Coordinate.",
        )
        db.add_all([sync_run, stream_run])
        db.commit()
        db.refresh(sync_run)
        db.refresh(stream_run)

        fail_orchestration_preflight(
            db,
            sync_run,
            requested_agents=["analyst", "developer"],
            kind="no_valid_agents",
        )
        fail_orchestration_preflight(
            db,
            stream_run,
            requested_agents=["analyst", "developer"],
            kind="runtime_unprepared",
            streaming=True,
        )

        db.refresh(sync_run)
        db.refresh(stream_run)
        assert sync_run.status == "failed"
        assert sync_run.summary == "No valid agents resolved for orchestration."
        assert stream_run.status == "failed"
        assert stream_run.summary == "Streaming orchestration runtime was not prepared."
    finally:
        db.close()


def test_fail_recovery_guard_records_failure_and_returns_outcome(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery guard run",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        outcome = fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run.id,
            kind="no_runnable_steps",
            owner="instance-1",
            lease_expires_at=None,
            payload={"marker": "x"},
        )

        assert outcome.reason == "no_runnable_steps"
        assert outcome.status == "failed"
        assert outcome.detail == "Recovery failed: no runnable steps after rebuild."
        db.refresh(task_run)
        assert task_run.status == "failed"
        assert task_run.summary == "Recovery failed: no runnable steps after rebuild."
        assert task_run.events[-1].event_type == "task_run_recovery_failed"
    finally:
        db.close()
