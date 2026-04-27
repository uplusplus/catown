from datetime import datetime, timedelta

from services.approval_queue import (
    claim_approval_queue_resolution_lease,
    create_approval_queue_item,
    ensure_approval_queue_resume_token,
    resolve_approval_queue_item,
    serialize_approval_queue_item,
)
from services.approval_replay import build_pending_approval_continuation_cursor


def test_approval_queue_resume_token_and_resolution_lease(fresh_db):
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
