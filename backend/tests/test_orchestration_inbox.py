from services.orchestration_inbox import (
    claim_orchestration_handoffs_for_step,
    create_orchestration_handoff_delivery,
    mark_orchestration_handoffs_consumed,
    mark_orchestration_handoffs_failed,
    summarize_orchestration_handoff_inbox,
    summarize_orchestration_handoff_projection,
)
from services.run_ledger import build_task_run_checkpoint_snapshot


def test_orchestration_inbox_claims_acknowledges_and_projects_checkpoint(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Orchestration inbox")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Inbox run",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        create_orchestration_handoff_delivery(
            db,
            task_run=task_run,
            from_agent="Analyst",
            to_agent="Developer",
            from_step_id="step-1",
            to_step_id="step-2",
            dispatch_kind="blocking",
            attached_to_step_id=None,
            content="Implementation plan.",
        )
        db.commit()

        claimed = claim_orchestration_handoffs_for_step(
            db,
            task_run_id=task_run.id,
            step_id="step-2",
            agent_name="Developer",
            lease_owner="lease-1",
        )

        assert claimed.durable_present is True
        assert claimed.messages[0]["content"] == "Implementation plan."
        assert claimed.messages[0]["delivery_status"] == "inflight"
        assert mark_orchestration_handoffs_consumed(
            db,
            delivery_ids=claimed.delivery_ids,
            lease_owner="lease-1",
        ) == 1

        db.refresh(task_run)
        projection = summarize_orchestration_handoff_inbox(task_run)
        assert projection["consumed_delivery_count"] == 1
        assert summarize_orchestration_handoff_projection(projection) == (
            "Orchestration handoffs 1 total / 0 pending / 0 inflight / 0 dead-letter / 1 consumed"
        )

        snapshot = build_task_run_checkpoint_snapshot(task_run)
        assert snapshot["orchestration_handoff_inbox"]["consumed_delivery_count"] == 1
        assert snapshot["orchestration_handoff_inbox_summary"] == (
            "Orchestration handoffs 1 total / 0 pending / 0 inflight / 0 dead-letter / 1 consumed"
        )
    finally:
        db.close()


def test_orchestration_inbox_retries_and_dead_letters_failed_claims(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Orchestration inbox retry")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Inbox retry run",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        create_orchestration_handoff_delivery(
            db,
            task_run=task_run,
            from_agent="Analyst",
            to_agent="Developer",
            from_step_id="step-1",
            to_step_id="step-2",
            dispatch_kind="blocking",
            attached_to_step_id=None,
            content="Implementation plan.",
        )
        db.commit()

        first = claim_orchestration_handoffs_for_step(
            db,
            task_run_id=task_run.id,
            step_id="step-2",
            agent_name="Developer",
            lease_owner="lease-1",
        )
        assert first.messages[0]["delivery_attempt_count"] == 1
        assert mark_orchestration_handoffs_failed(
            db,
            delivery_ids=first.delivery_ids,
            lease_owner="lease-1",
            error="LLM failed",
            retry=True,
        ) == 1

        second = claim_orchestration_handoffs_for_step(
            db,
            task_run_id=task_run.id,
            step_id="step-2",
            agent_name="Developer",
            lease_owner="lease-2",
        )
        assert second.messages[0]["delivery_attempt_count"] == 2
        assert mark_orchestration_handoffs_failed(
            db,
            delivery_ids=second.delivery_ids,
            lease_owner="lease-2",
            error="Too many retries",
            retry=False,
        ) == 1

        db.refresh(task_run)
        projection = summarize_orchestration_handoff_inbox(task_run)
        assert projection["dead_letter_delivery_count"] == 1
        assert projection["pending_delivery_count"] == 0
    finally:
        db.close()
