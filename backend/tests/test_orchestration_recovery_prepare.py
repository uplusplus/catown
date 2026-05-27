import importlib


def test_prepare_orchestration_recovery_context_resolves_prepared_runtime(fresh_db):
    import services.orchestration_recovery_prepare as prep_mod

    prep_mod = importlib.reload(prep_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery prepare")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery prepare",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        prepared = prep_mod.prepare_orchestration_recovery_context(
            db=db,
            task_run=task_run,
            task_run_id=task_run.id,
            recovery_owner="instance-1",
            lease_expires_at=None,
            resolve_chatroom=lambda current_db, chatroom_id: chatroom,
            resolve_chatroom_project=lambda current_db, current_chatroom: None,
            serialize_project_agents=lambda current_db, project_id: [],
            list_global_agents=lambda current_db: ["analyst-agent"],
            recover_agent_names=lambda current_task_run: ["analyst"],
            prepare_orchestration_runtime=lambda **kwargs: type(
                "Prepared",
                (),
                {
                    "resolved_agents": ["analyst-agent"],
                    "plan": "plan",
                    "runner_policy": "policy",
                },
            )(),
        )

        assert isinstance(prepared, prep_mod.PreparedOrchestrationRecoveryContext)
        assert prepared.chatroom is chatroom
        assert prepared.agent_names == ["analyst"]
        assert prepared.resolved_agents == ["analyst-agent"]
        assert prepared.plan == "plan"
        assert prepared.orchestration_policy == "policy"
    finally:
        db.close()


def test_prepare_orchestration_recovery_context_returns_guard_outcome_when_chatroom_missing(fresh_db):
    import services.orchestration_recovery_prepare as prep_mod

    prep_mod = importlib.reload(prep_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery prepare missing")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery prepare missing",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        outcome = prep_mod.prepare_orchestration_recovery_context(
            db=db,
            task_run=task_run,
            task_run_id=task_run.id,
            recovery_owner="instance-1",
            lease_expires_at=None,
            resolve_chatroom=lambda current_db, chatroom_id: None,
            resolve_chatroom_project=lambda current_db, current_chatroom: None,
            serialize_project_agents=lambda current_db, project_id: [],
            list_global_agents=lambda current_db: [],
            recover_agent_names=lambda current_task_run: [],
            prepare_orchestration_runtime=lambda **kwargs: None,
        )

        assert outcome.reason == "chatroom_missing"
        assert outcome.status == "failed"
        db.refresh(task_run)
        assert task_run.summary == "Recovery failed: chatroom missing."
    finally:
        db.close()


def test_prepare_orchestration_recovery_context_fails_when_chatroom_public_identity_mismatches(fresh_db):
    import services.orchestration_recovery_prepare as prep_mod

    prep_mod = importlib.reload(prep_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        original_chatroom = fresh_db.Chatroom(title="Recovery identity original")
        db.add(original_chatroom)
        db.commit()
        db.refresh(original_chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=original_chatroom.id,
            chatroom_public_id=original_chatroom.public_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery identity mismatch",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        replacement_chatroom = fresh_db.Chatroom(title="Recovery identity replacement")
        db.add(replacement_chatroom)
        db.commit()
        db.refresh(replacement_chatroom)

        outcome = prep_mod.prepare_orchestration_recovery_context(
            db=db,
            task_run=task_run,
            task_run_id=task_run.id,
            recovery_owner="instance-1",
            lease_expires_at=None,
            resolve_chatroom=lambda current_db, chatroom_id: replacement_chatroom,
            resolve_chatroom_project=lambda current_db, current_chatroom: None,
            serialize_project_agents=lambda current_db, project_id: [],
            list_global_agents=lambda current_db: [],
            recover_agent_names=lambda current_task_run: [],
            prepare_orchestration_runtime=lambda **kwargs: None,
        )

        assert outcome.reason == "chatroom_identity_mismatch"
        assert outcome.status == "failed"
        db.refresh(task_run)
        assert task_run.summary == "Recovery failed: chatroom identity mismatch."
    finally:
        db.close()
