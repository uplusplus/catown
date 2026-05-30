from datetime import datetime, timedelta


def test_approval_queue_resume_token_and_resolution_lease(fresh_db):
    from services.approval_queue import (
        claim_approval_queue_resolution_lease,
        create_approval_queue_item,
        ensure_approval_queue_resume_token,
        resolve_approval_queue_item,
        serialize_approval_queue_item,
    )
    from services.approval_replay import build_pending_approval_continuation_cursor

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Approval lease")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        item = create_approval_queue_item(
            db,
            task_run=None,
            chatroom_id=chatroom.id,
            project_id=None,
            queue_kind="escalation",
            source="tool_call_blocked",
            title="Escalation needed",
            summary="sandbox blocked",
            agent_name="developer",
            target_kind="tool",
            target_name="write_file",
            request_key="sandbox-write-file",
            request_payload={"turn": 2, "blocked_kind": "sandbox"},
        )

        assert item.resume_token
        assert ensure_approval_queue_resume_token(db, item) == item.resume_token
        assert claim_approval_queue_resolution_lease(db, item, owner="api-a", lease_seconds=30) is True
        assert item.resolution_owner == "api-a"
        assert item.resolution_lease_expires_at is not None
        assert claim_approval_queue_resolution_lease(db, item, owner="api-b", lease_seconds=30) is False

        item.resolution_lease_expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
        assert claim_approval_queue_resolution_lease(db, item, owner="api-b", lease_seconds=30) is True
        assert item.resolution_owner == "api-b"

        cursor = build_pending_approval_continuation_cursor(item, request_payload={"turn": 2})
        assert cursor["resume_token"] == item.resume_token
        assert cursor["resolution_owner"] == "api-b"
        assert cursor["resolution_lease_expires_at"] is not None

        serialized = serialize_approval_queue_item(item)
        assert serialized["resume_token"] == item.resume_token
        assert serialized["resolution_owner"] == "api-b"

        resolved = resolve_approval_queue_item(db, item, status="approved", resolved_by="user")
        assert resolved.status == "approved"
        assert resolved.resolution_owner is None
        assert resolved.resolution_lease_expires_at is None
    finally:
        db.close()


def test_approval_queue_item_carries_stable_identity_snapshots(fresh_db):
    from services.approval_queue import create_approval_queue_item, serialize_approval_queue_item

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Approval identity chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            chatroom_public_id=chatroom.public_id,
            run_kind="project_single_agent",
            status="running",
            title="Approval identity run",
            user_request="Do something",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        item = create_approval_queue_item(
            db,
            task_run=task_run,
            chatroom_id=None,
            project_id=None,
            queue_kind="approval",
            source="runtime",
            title="Approval needed",
            agent_name="developer",
            target_kind="tool",
            target_name="run_shell",
            request_key="approval-identity-run-shell",
            request_payload={"turn": 1},
        )

        serialized = serialize_approval_queue_item(item)
        assert serialized["public_id"] == item.public_id
        assert serialized["chatroom_public_id"] == chatroom.public_id
        assert serialized["task_run_public_id"] == task_run.public_id
    finally:
        db.close()


def test_approval_queue_item_notification_payload_refers_to_durable_row(fresh_db):
    from services.approval_queue import (
        build_approval_queue_item_notification_payload,
        create_approval_queue_item,
        resolve_approval_queue_item,
    )

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Approval notification chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        item = create_approval_queue_item(
            db,
            task_run=None,
            chatroom_id=chatroom.id,
            project_id=None,
            queue_kind="approval",
            source="runtime",
            title="Approval needed",
            summary="sandbox blocked",
            agent_name="developer",
            target_kind="tool",
            target_name="run_shell",
            request_key="approval-notification-payload",
            request_payload={"turn": 3},
        )

        created_payload = build_approval_queue_item_notification_payload(item, reason="created")
        assert created_payload == {
            "queue_item_id": item.id,
            "queue_item_public_id": item.public_id,
            "task_run_id": item.task_run_id,
            "chatroom_id": chatroom.id,
            "project_id": item.project_id,
            "queue_kind": "approval",
            "target_kind": "tool",
            "target_name": "run_shell",
            "status": "pending",
            "reason": "created",
            "captured_at": created_payload["captured_at"],
        }
        assert isinstance(created_payload["captured_at"], str) and created_payload["captured_at"]

        resolved = resolve_approval_queue_item(db, item, status="approved", resolved_by="user")
        resolved_payload = build_approval_queue_item_notification_payload(resolved, reason="resolved")
        assert resolved_payload is not None
        assert resolved_payload["queue_item_id"] == item.id
        assert resolved_payload["chatroom_id"] == chatroom.id
        assert resolved_payload["status"] == "approved"
        assert resolved_payload["reason"] == "resolved"
    finally:
        db.close()
