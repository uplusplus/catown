from datetime import datetime
from types import SimpleNamespace

import importlib
import pytest

from services.orchestration_scheduler import build_orchestration_schedule


class DummyAgent:
    def __init__(self, agent_id, name, agent_type):
        self.id = agent_id
        self.name = name
        self.agent_type = agent_type


def _rebuild_no_runnable(db, task_run, queue):
    queue.pop_ready()
    return ([], {}, "", [])


async def _unexpected_execute_turn(**kwargs):
    raise AssertionError("execute_turn should not be called for no_runnable_steps")


@pytest.mark.asyncio
async def test_run_orchestration_recovery_runtime_completes_and_finalizes(fresh_db):
    import services.orchestration_recovery_runner as runner_mod

    runner_mod = importlib.reload(runner_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery runner")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery runner",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        resolved_agents = [DummyAgent(1, "Analyst", "analyst"), DummyAgent(2, "Developer", "developer")]
        plan = build_orchestration_schedule([("analyst", resolved_agents[0]), ("developer", resolved_agents[1])])

        async def execute_turn(**kwargs):
            return f"{kwargs['agent'].name} recovered.", SimpleNamespace(id=kwargs["agent"].id, created_at=datetime.now())

        async def publish_message(*args, **kwargs):
            return None

        lease_ticks = []
        policy = SimpleNamespace(to_payload=lambda: {"mode": "linear_blocking_chain"}, stages=[])
        result = await runner_mod.run_orchestration_recovery_runtime(
            db=db,
            task_run=task_run,
            task_run_id=task_run.id,
            chatroom=chatroom,
            project=None,
            agents=resolved_agents,
            agent_names=["analyst", "developer"],
            resolved_agents=resolved_agents,
            plan=plan,
            orchestration_policy=policy,
            trigger="startup",
            lease_expires_at=datetime.now(),
            deps=runner_mod.OrchestrationRecoveryRuntimeDeps(
                build_checkpoint_snapshot=lambda current_task_run: {"event_count": len(current_task_run.events or [])},
                describe_recovery_continuation_state=lambda snapshot: {"next_action": "resume_scheduler"},
                rebuild_recovery_state=lambda db, task_run, queue: ([], {}, "", []),
                execute_turn=execute_turn,
                publish_message=publish_message,
                message_metadata={"client_turn_id": "turn-recovery"},
                renew_lease=lambda: lease_ticks.append("tick") or datetime.now(),
                recovery_owner="instance-1",
            ),
        )

        assert result.resumed is True
        assert result.reason == "completed"
        assert result.status == "completed"
        assert "Developer recovered." in result.detail
        assert lease_ticks == ["tick", "tick", "tick"]
        db.refresh(task_run)
        assert task_run.status == "completed"
        assert any(event.event_type == "task_run_recovery_completed" for event in task_run.events)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_orchestration_recovery_runtime_returns_no_runnable_steps_outcome(fresh_db):
    import services.orchestration_recovery_runner as runner_mod

    runner_mod = importlib.reload(runner_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Recovery no runnable")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Recovery no runnable",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        resolved_agents = [DummyAgent(1, "Analyst", "analyst")]
        plan = build_orchestration_schedule([("analyst", resolved_agents[0])])

        policy = SimpleNamespace(to_payload=lambda: {"mode": "linear_blocking_chain"}, stages=[])
        result = await runner_mod.run_orchestration_recovery_runtime(
            db=db,
            task_run=task_run,
            task_run_id=task_run.id,
            chatroom=chatroom,
            project=None,
            agents=resolved_agents,
            agent_names=["analyst"],
            resolved_agents=resolved_agents,
            plan=plan,
            orchestration_policy=policy,
            trigger="startup",
            lease_expires_at=datetime.now(),
            deps=runner_mod.OrchestrationRecoveryRuntimeDeps(
                build_checkpoint_snapshot=lambda current_task_run: {"event_count": len(current_task_run.events or [])},
                describe_recovery_continuation_state=lambda snapshot: {"next_action": "resume_scheduler"},
                rebuild_recovery_state=_rebuild_no_runnable,
                execute_turn=_unexpected_execute_turn,
                publish_message=lambda *args, **kwargs: None,
                message_metadata={"client_turn_id": "turn-recovery"},
                renew_lease=lambda: datetime.now(),
                recovery_owner="instance-1",
            ),
        )

        assert result.resumed is False
        assert result.reason == "no_runnable_steps"
        assert result.status == "failed"
        db.refresh(task_run)
        assert task_run.status == "failed"
        assert task_run.summary == "Recovery failed: no runnable steps after rebuild."
    finally:
        db.close()
