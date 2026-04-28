from datetime import datetime, timedelta

from services.orchestration_recovery_lease import (
    RecoveryLeaseLostError,
    claim_recovery_lease,
    ensure_recovery_lease,
)


def test_claim_recovery_lease_covers_claimed_and_leased_paths(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery lease")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery lease run",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        claimed = claim_recovery_lease(
            db,
            task_run_id=task_run.id,
            recoverable_run_kinds={"multi_agent_orchestration"},
            owner="instance-a",
            lease_seconds=300,
        )
        assert claimed.reason == "claimed"
        assert claimed.owner == "instance-a"
        assert claimed.lease_expires_at is not None

        leased = claim_recovery_lease(
            db,
            task_run_id=task_run.id,
            recoverable_run_kinds={"multi_agent_orchestration"},
            owner="instance-b",
            lease_seconds=300,
        )
        assert leased.reason == "leased"
        assert leased.owner == "instance-a"
        assert "already being recovered" in (leased.detail or "")
    finally:
        db.close()


def test_ensure_recovery_lease_renews_and_raises_on_loss(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery lease renew")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery renew run",
            user_request="Recover.",
            recovery_owner="instance-a",
            recovery_claimed_at=datetime.now(),
            recovery_lease_expires_at=datetime.now() + timedelta(minutes=5),
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        renewed = ensure_recovery_lease(
            db,
            task_run_id=task_run.id,
            owner="instance-a",
            lease_seconds=300,
        )
        assert renewed is not None

        task_run.recovery_owner = "instance-b"
        db.add(task_run)
        db.commit()

        try:
            ensure_recovery_lease(
                db,
                task_run_id=task_run.id,
                owner="instance-a",
                lease_seconds=300,
            )
        except RecoveryLeaseLostError as exc:
            assert exc.task_run_id == task_run.id
        else:
            raise AssertionError("expected RecoveryLeaseLostError")
    finally:
        db.close()
