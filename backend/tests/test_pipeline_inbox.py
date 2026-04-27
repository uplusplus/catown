from services.pipeline_inbox import (
    consume_legacy_instruction_texts_for_agent,
    enqueue_message_delivery,
    pop_instruction_texts_for_agent,
    pop_messages_for_agent,
)


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
