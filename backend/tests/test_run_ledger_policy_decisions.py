import json
import importlib

from services.policy_decision_contracts import build_policy_decision_event_payload
from services.run_ledger import (
    build_task_run_checkpoint_snapshot,
    serialize_task_run_detail,
    serialize_task_run_summary,
)


def test_task_run_checkpoint_summarizes_policy_decision_events(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Policy Decision Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="running",
            title="Policy decision run",
            user_request="Check policy decisions.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        payload = build_policy_decision_event_payload(
            {
                "kind": "policy_decision",
                "version": 1,
                "decision_id": "policy-decision-action-1",
                "decision_type": "action_request_policy",
                "subject": {
                    "kind": "action_request",
                    "id": "req-1",
                    "type": "request_approval",
                },
                "accepted": False,
                "stage_name": "analysis",
                "violations": [
                    {
                        "code": "stage_gate_not_configured",
                        "message": "Stage has no manual gate.",
                        "severity": "error",
                    }
                ],
            }
        )
        db.add(
            fresh_db.TaskRunEvent(
                task_run_id=task_run.id,
                event_index=1,
                event_type="policy_decision_recorded",
                summary="Policy decision recorded.",
                payload_json=json.dumps(payload),
            )
        )
        db.commit()
        db.refresh(task_run)

        snapshot = build_task_run_checkpoint_snapshot(task_run)
        summary = serialize_task_run_summary(task_run)
        detail = serialize_task_run_detail(task_run)

        assert snapshot["policy_decision_summary"] == {
            "decision_count": 1,
            "accepted_count": 0,
            "rejected_count": 1,
            "error_count": 1,
            "warning_count": 0,
            "info_count": 0,
            "by_decision_type": {
                "action_request_policy": {
                    "count": 1,
                    "accepted": 0,
                    "rejected": 1,
                }
            },
        }
        assert summary["policy_decision_summary"] == snapshot["policy_decision_summary"]
        assert len(detail["policy_decisions"]) == 1
        policy_decision_entry = detail["policy_decisions"][0]
        assert policy_decision_entry["event_index"] == 1
        assert policy_decision_entry["policy_decision_summary"]["decision_id"] == "policy-decision-action-1"
        assert policy_decision_entry["policy_decision"]["subject"]["id"] == "req-1"
    finally:
        db.close()


def test_append_policy_decision_event_writes_standard_payload(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Policy Decision Append Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="running",
            title="Policy decision append run",
            user_request="Append policy decisions.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        import services.run_ledger as run_ledger_service

        run_ledger_service = importlib.reload(run_ledger_service)
        event = run_ledger_service.append_policy_decision_event(
            db,
            task_run,
            {
                "kind": "policy_decision",
                "version": 1,
                "decision_id": "policy-decision-artifact-1",
                "decision_type": "artifact_contract_policy",
                "subject": {
                    "kind": "artifact_contract",
                    "id": "artifact-1",
                    "type": "workspace.file",
                },
                "accepted": True,
                "stage_name": "testing",
            },
            agent_name="Tester",
        )

        assert event is not None
        assert event.event_type == "policy_decision_recorded"
        assert event.summary == "Policy decision accepted for artifact_contract artifact-1."
        detail = run_ledger_service.serialize_task_run_detail(task_run)
        assert detail["policy_decision_summary"]["accepted_count"] == 1
        assert detail["policy_decisions"][0]["policy_decision"]["decision_id"] == "policy-decision-artifact-1"
    finally:
        db.close()
