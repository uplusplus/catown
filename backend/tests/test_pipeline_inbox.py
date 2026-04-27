from services.pipeline_inbox import (
    claim_messages_for_agent,
    consume_legacy_instruction_texts_for_agent,
    enqueue_message_delivery,
    mark_delivery_consumed,
    mark_delivery_failed,
    pop_instruction_texts_for_agent,
    pop_messages_for_agent,
    summarize_pipeline_run_inbox,
)
from services.run_ledger import build_task_run_checkpoint_snapshot


def test_pipeline_inbox_delivers_non_instruction_messages_once(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        run = fresh_db.PipelineRun(pipeline_id=1, run_number=1, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        message = fresh_db.PipelineMessage(
            run_id=run.id,
            message_type="AGENT_QUESTION",
            from_agent="analyst",
            to_agent="developer",
            content="Please inspect the API route.",
        )
        db.add(message)
        db.flush()
        enqueue_message_delivery(db, message)
        db.commit()

        first = pop_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            exclude_message_type="HUMAN_INSTRUCT",
        )
        second = pop_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            exclude_message_type="HUMAN_INSTRUCT",
        )

        assert len(first) == 1
        assert first[0]["message_id"] == message.id
        assert first[0]["content"] == "Please inspect the API route."
        assert second == []

        delivery = db.query(fresh_db.PipelineMessageDelivery).first()
        assert delivery.status == "consumed"
        assert delivery.consumed_at is not None
    finally:
        db.close()


def test_pipeline_inbox_claims_with_lease_and_acknowledges(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        run = fresh_db.PipelineRun(pipeline_id=1, run_number=1, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        message = fresh_db.PipelineMessage(
            run_id=run.id,
            message_type="AGENT_NOTE",
            from_agent="analyst",
            to_agent="developer",
            content="Lease this once.",
        )
        db.add(message)
        db.flush()
        enqueue_message_delivery(db, message)
        db.commit()

        claimed = claim_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            lease_owner="worker-a",
            lease_seconds=30,
        )
        assert len(claimed) == 1
        assert claimed[0]["delivery_id"] is not None
        assert claimed[0]["delivery_status"] == "inflight"
        assert claimed[0]["delivery_attempt_count"] == 1

        assert claim_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            lease_owner="worker-b",
            lease_seconds=30,
        ) == []

        assert mark_delivery_consumed(db, delivery_id=claimed[0]["delivery_id"], lease_owner="worker-a") is True
        delivery = db.query(fresh_db.PipelineMessageDelivery).first()
        assert delivery.status == "consumed"
        assert delivery.consumed_at is not None
        assert delivery.lease_owner is None
    finally:
        db.close()


def test_pipeline_inbox_reclaims_expired_lease_and_dead_letters(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        run = fresh_db.PipelineRun(pipeline_id=1, run_number=1, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        message = fresh_db.PipelineMessage(
            run_id=run.id,
            message_type="AGENT_NOTE",
            from_agent="analyst",
            to_agent="developer",
            content="Retry this if the lease expires.",
        )
        db.add(message)
        db.flush()
        enqueue_message_delivery(db, message)
        db.commit()

        first = claim_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            lease_owner="worker-a",
            lease_seconds=1,
        )
        delivery = db.query(fresh_db.PipelineMessageDelivery).first()
        delivery.lease_expires_at = fresh_db.datetime.now()
        db.commit()

        second = claim_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            lease_owner="worker-b",
            lease_seconds=30,
        )
        assert first[0]["delivery_id"] == second[0]["delivery_id"]
        assert second[0]["delivery_attempt_count"] == 2

        assert mark_delivery_failed(
            db,
            delivery_id=second[0]["delivery_id"],
            lease_owner="worker-b",
            error="tool crashed",
            retry=False,
        ) is True
        assert claim_messages_for_agent(
            db,
            run_id=run.id,
            agent_name="developer",
            lease_owner="worker-c",
            lease_seconds=30,
        ) == []

        db.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.last_error == "tool crashed"
        assert delivery.failed_at is not None
    finally:
        db.close()


def test_pipeline_inbox_projects_delivery_state_into_task_checkpoint(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Pipeline checkpoint")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="pipeline",
            status="running",
            title="Pipeline inbox projection",
            user_request="Inspect inbox state.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        pipeline_run = fresh_db.PipelineRun(
            pipeline_id=1,
            task_run_id=task_run.id,
            run_number=1,
            status="running",
        )
        db.add(pipeline_run)
        db.commit()
        db.refresh(pipeline_run)

        for index, status in enumerate(["pending", "inflight", "consumed", "dead_letter"], start=1):
            message = fresh_db.PipelineMessage(
                run_id=pipeline_run.id,
                message_type="AGENT_NOTE",
                from_agent="analyst",
                to_agent="developer",
                content=f"Message {index}",
            )
            db.add(message)
            db.flush()
            delivery = enqueue_message_delivery(db, message)
            delivery.status = status
            if status == "inflight":
                delivery.lease_owner = "worker-a"
                delivery.lease_expires_at = fresh_db.datetime.now()
            if status == "consumed":
                delivery.consumed_at = fresh_db.datetime.now()
            if status == "dead_letter":
                delivery.failed_at = fresh_db.datetime.now()
        db.commit()

        db.refresh(pipeline_run)
        projection = summarize_pipeline_run_inbox(pipeline_run)
        assert projection["status_counts"] == {
            "pending": 1,
            "inflight": 1,
            "consumed": 1,
            "dead_letter": 1,
        }
        assert projection["agents"] == [
            {
                "agent_name": "developer",
                "status_counts": {"pending": 1, "inflight": 1, "consumed": 1, "dead_letter": 1},
            }
        ]

        db.refresh(task_run)
        snapshot = build_task_run_checkpoint_snapshot(task_run)
        assert snapshot["pipeline_inbox"][0]["pipeline_run_id"] == pipeline_run.id
        assert snapshot["pipeline_inbox"][0]["dead_letter_delivery_count"] == 1
        assert snapshot["pipeline_inbox_summary"] == (
            "4 deliveries · 1 pending · 1 inflight · 1 dead-letter · 1 consumed"
        )
    finally:
        db.close()


def test_pipeline_inbox_consumes_instruction_and_backfills_legacy_delivery(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        run = fresh_db.PipelineRun(pipeline_id=1, run_number=1, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        current_instruction = fresh_db.PipelineMessage(
            run_id=run.id,
            message_type="HUMAN_INSTRUCT",
            from_agent="BOSS",
            to_agent="developer",
            content="Continue with the implementation.",
        )
        legacy_instruction = fresh_db.PipelineMessage(
            run_id=run.id,
            message_type="HUMAN_INSTRUCT",
            from_agent="BOSS",
            to_agent="developer",
            content="Legacy instruction.",
        )
        db.add_all([current_instruction, legacy_instruction])
        db.flush()
        enqueue_message_delivery(db, current_instruction)
        db.commit()

        assert pop_instruction_texts_for_agent(db, run_id=run.id, agent_name="developer") == [
            "Continue with the implementation."
        ]
        assert consume_legacy_instruction_texts_for_agent(db, run_id=run.id, agent_name="developer") == [
            "Legacy instruction."
        ]
        assert consume_legacy_instruction_texts_for_agent(db, run_id=run.id, agent_name="developer") == []

        deliveries = db.query(fresh_db.PipelineMessageDelivery).all()
        assert len(deliveries) == 2
        assert {delivery.status for delivery in deliveries} == {"consumed"}
    finally:
        db.close()
