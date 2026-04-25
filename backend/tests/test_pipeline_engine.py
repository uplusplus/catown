"""
Pipeline engine runtime tests.
"""
import importlib
import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _reload_pipeline_engine():
    sys.modules.pop("models.audit", None)
    sys.modules.pop("pipeline.engine", None)
    import pipeline.engine as engine_mod

    return engine_mod


def _install_test_pipeline_template(engine_mod):
    from pipeline.config import PipelineConfig, StageConfig

    engine_mod.pipeline_config_manager.configs = {
        "default": PipelineConfig(
            name="default",
            description="Minimal template for pipeline runtime tests.",
            stages=[
                StageConfig(
                    name="analysis",
                    display_name="Analysis",
                    agent="analyst",
                    gate="auto",
                    timeout_minutes=5,
                    context_prompt="Inspect the request and propose the smallest safe slice.",
                )
            ],
        )
    }


def _seed_pipeline_project(fresh_db, db, *, project_name: str):
    project = fresh_db.Project(name=project_name)
    db.add(project)
    db.commit()
    db.refresh(project)

    chatroom = fresh_db.Chatroom(
        project_id=project.id,
        title="Project Chat",
        session_type="project-bound",
    )
    db.add(chatroom)
    db.commit()
    db.refresh(chatroom)

    project.default_chatroom_id = chatroom.id
    db.commit()
    db.refresh(project)

    pipeline = fresh_db.Pipeline(
        project_id=project.id,
        pipeline_name="default",
        status="pending",
        current_stage_index=0,
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return project, chatroom, pipeline


def test_start_pipeline_creates_task_run_ledger_bridge(fresh_db):
    engine_mod = _reload_pipeline_engine()
    _install_test_pipeline_template(engine_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        project, chatroom, pipeline = _seed_pipeline_project(
            fresh_db,
            db,
            project_name="Pipeline Ledger Bridge Project",
        )

        engine = engine_mod.PipelineEngine()
        engine._git_init = lambda run: None

        run = engine.start_pipeline(db, pipeline.id, "Bridge pipeline runs into the unified task ledger.")
        db.refresh(run)

        assert run.task_run_id is not None

        task_run = db.query(fresh_db.TaskRun).filter(fresh_db.TaskRun.id == run.task_run_id).first()
        assert task_run is not None
        assert task_run.run_kind == engine_mod.PIPELINE_TASK_RUN_KIND
        assert task_run.chatroom_id == chatroom.id
        assert task_run.project_id == project.id
        assert task_run.target_agent_name == "analyst"

        events = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.task_run_id == task_run.id)
            .order_by(fresh_db.TaskRunEvent.event_index.asc())
            .all()
        )

        assert [event.event_type for event in events] == ["pipeline_run_started"]

        payload = json.loads(events[0].payload_json)
        assert payload["pipeline_id"] == pipeline.id
        assert payload["pipeline_run_id"] == run.id
        assert payload["stage_count"] == 1
        assert payload["stage_names"] == ["Analysis"]
        assert payload["runner_policy"]["mode"] == "pipeline_governance"
        assert payload["runner_policy"]["pipeline_name"] == "default"
        assert payload["runner_policy"]["stage_count"] == 1
        assert payload["runner_policy"]["stages"][0]["stage_name"] == "analysis"
        assert payload["runner_policy"]["stages"][0]["approval"]["required"] is False
        assert payload["runner_policy"]["stages"][0]["metadata"]["tool_policy_summary"]["tool_count"] >= 1
        assert payload["runner_policy"]["metadata"]["stage_tool_packs"]["analysis"]["tool_policy_summary"]["tool_count"] >= 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_agent_stage_rebuilds_messages_from_turn_state(fresh_db, tmp_path):
    engine_mod = _reload_pipeline_engine()
    from pipeline.config import StageConfig

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        workspace = tmp_path / "workspace"
        (workspace / "backend" / "routes").mkdir(parents=True)
        (workspace / "backend" / "routes" / "api.py").write_text("print('hello')\n", encoding="utf-8")

        project = fresh_db.Project(name="Pipeline Runtime Project")
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(
            project_id=project.id,
            title="Project Chat",
            session_type="project-bound",
        )
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        project.default_chatroom_id = chatroom.id
        db.commit()
        db.refresh(project)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            project_id=project.id,
            run_kind=engine_mod.PIPELINE_TASK_RUN_KIND,
            status="running",
            title="Inspect the backend implementation",
            user_request="Inspect the backend implementation",
            initiator="user",
            target_agent_name="analyst",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        pipeline = fresh_db.Pipeline(
            project_id=project.id,
            pipeline_name="default",
            status="running",
            current_stage_index=0,
        )
        db.add(pipeline)
        db.commit()
        db.refresh(pipeline)

        run = fresh_db.PipelineRun(
            pipeline_id=pipeline.id,
            task_run_id=task_run.id,
            run_number=1,
            status="running",
            input_requirement="Inspect the backend implementation",
            workspace_path=str(workspace),
            started_at=datetime.now(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        stage = fresh_db.PipelineStage(
            run_id=run.id,
            stage_name="analysis",
            display_name="Analysis",
            stage_order=0,
            agent_name="analyst",
            status="running",
            gate_type="auto",
            started_at=datetime.now(),
        )
        db.add(stage)
        db.commit()
        db.refresh(stage)

        db.add(
            fresh_db.PipelineMessage(
                run_id=run.id,
                stage_id=None,
                message_type="HUMAN_INSTRUCT",
                from_agent="BOSS",
                to_agent="analyst",
                content="Prefer the smallest patch.",
            )
        )
        db.commit()

        inter_agent_message = fresh_db.PipelineMessage(
            run_id=run.id,
            stage_id=stage.id,
            message_type="AGENT_QUESTION",
            from_agent="architect",
            to_agent="analyst",
            content="Check the API route file next.",
        )
        db.add(inter_agent_message)
        db.flush()
        engine_mod._enqueue_message_delivery(db, inter_agent_message)
        db.commit()
        engine_mod.AGENT_TOOLS["analyst"] = ["list_files", "read_file"]

        seen_messages = []

        async def scripted_chat_with_tools(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            call_no = len(seen_messages)
            if call_no == 1:
                return {
                    "content": "I will inspect the backend tree first.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "list_files",
                                "arguments": "{\"dir_path\": \"backend\"}",
                            },
                        }
                    ],
                }
            if call_no == 2:
                return {
                    "content": "Now I should open the API route file.",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"file_path\": \"backend/routes/api.py\"}",
                            },
                        }
                    ],
                }
            return {
                "content": "Done inspecting the backend.",
                "tool_calls": None,
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_with_tools = scripted_chat_with_tools
        engine_mod.get_llm_client_for_agent = lambda agent_name: mock_llm

        engine = engine_mod.PipelineEngine()
        stage_cfg = StageConfig(
            name="analysis",
            display_name="Analysis",
            agent="analyst",
            gate="auto",
            timeout_minutes=5,
            context_prompt="Inspect the backend implementation",
        )

        summary = await engine._run_agent_stage(
            db=db,
            pipeline=pipeline,
            run=run,
            stage=stage,
            stage_cfg=stage_cfg,
            context="Pipeline context for backend inspection.",
        )

        assert summary == "Done inspecting the backend."
        assert len(seen_messages) == 3

        task_events = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.task_run_id == task_run.id)
            .order_by(fresh_db.TaskRunEvent.event_index.asc())
            .all()
        )
        assert [event.event_type for event in task_events] == [
            "agent_turn_started",
            "tool_round_recorded",
            "tool_round_recorded",
            "agent_turn_completed",
        ]
        assert task_events[-1].agent_name == "analyst"
        assert json.loads(task_events[1].payload_json)["tool_names"] == ["list_files"]
        assert json.loads(task_events[2].payload_json)["tool_names"] == ["read_file"]

        second_call_messages = seen_messages[1]
        third_call_messages = seen_messages[2]

        assert sum(
            1
            for message in second_call_messages
            if message.get("role") == "developer"
            and "## BOSS Instructions" in str(message.get("content") or "")
        ) == 1
        assert sum(
            1
            for message in third_call_messages
            if message.get("role") == "developer"
            and "## BOSS Instructions" in str(message.get("content") or "")
        ) == 1

        assert any(
            message.get("role") == "user"
            and "## Inter-Agent Messages" in str(message.get("content") or "")
            for message in second_call_messages
        )
        assert any(
            message.get("role") == "user"
            and "## Inter-Agent Messages" in str(message.get("content") or "")
            for message in third_call_messages
        )

        assert any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in second_call_messages
        )
        assert any(
            message.get("role") == "tool" and message.get("name") == "read_file"
            for message in third_call_messages
        )
        assert not any(
            message.get("role") == "tool" and message.get("name") == "list_files"
            for message in third_call_messages
        )
        assert any(
            message.get("role") == "user" and "## Tool Work So Far" in str(message.get("content") or "")
            for message in third_call_messages
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_agent_stage_records_blocked_tool_calls_in_ledger(fresh_db, tmp_path):
    engine_mod = _reload_pipeline_engine()
    from pipeline.config import StageConfig

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)

        project, chatroom, pipeline = _seed_pipeline_project(
            fresh_db,
            db,
            project_name="Pipeline Blocked Tool Project",
        )

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            project_id=project.id,
            run_kind=engine_mod.PIPELINE_TASK_RUN_KIND,
            status="running",
            title="Pipeline blocked tool run",
            user_request="Try a disallowed tool in the pipeline runtime.",
            target_agent_name="analyst",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        run = fresh_db.PipelineRun(
            pipeline_id=pipeline.id,
            task_run_id=task_run.id,
            run_number=1,
            status="running",
            input_requirement="Trigger a blocked tool call.",
            workspace_path=str(workspace),
            started_at=datetime.now(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        stage = fresh_db.PipelineStage(
            run_id=run.id,
            stage_name="analysis",
            display_name="Analysis",
            stage_order=0,
            agent_name="analyst",
            status="running",
            gate_type="auto",
        )
        db.add(stage)
        db.commit()
        db.refresh(stage)

        stage_cfg = StageConfig(
            name="analysis",
            display_name="Analysis",
            agent="analyst",
            gate="auto",
            timeout_minutes=5,
            context_prompt="Try a tool and report what happened.",
        )

        engine_mod.AGENT_TOOLS["analyst"] = ["list_files"]

        seen_messages = []

        async def scripted_chat_with_tools(messages, tools=None):
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            if len(seen_messages) == 1:
                return {
                    "content": "I should inspect a file directly.",
                    "tool_calls": [
                        {
                            "id": "blocked_call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"file_path\": \"README.md\"}",
                            },
                        }
                    ],
                }
            return {
                "content": "The tool call was blocked by policy.",
                "tool_calls": None,
            }

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_with_tools = scripted_chat_with_tools
        engine_mod.get_llm_client_for_agent = lambda agent_name: mock_llm

        engine = engine_mod.PipelineEngine()

        summary = await engine._run_agent_stage(
            db=db,
            pipeline=pipeline,
            run=run,
            stage=stage,
            stage_cfg=stage_cfg,
            context="Pipeline context for blocked tool classification.",
        )

        assert summary == "The tool call was blocked by policy."
        assert len(seen_messages) == 2

        task_events = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.task_run_id == task_run.id)
            .order_by(fresh_db.TaskRunEvent.event_index.asc())
            .all()
        )

        assert [event.event_type for event in task_events] == [
            "agent_turn_started",
            "tool_round_recorded",
            "approval_queue_item_created",
            "tool_call_blocked",
            "agent_turn_completed",
        ]

        round_payload = json.loads(task_events[1].payload_json)
        queue_payload = json.loads(task_events[2].payload_json)
        blocked_payload = json.loads(task_events[3].payload_json)
        queue_item = (
            db.query(fresh_db.ApprovalQueueItem)
            .filter(fresh_db.ApprovalQueueItem.task_run_id == task_run.id)
            .order_by(fresh_db.ApprovalQueueItem.id.desc())
            .first()
        )

        assert round_payload["tool_status_counts"]["approval_blocked"] == 1
        assert round_payload["blocked_tool_count"] == 1
        assert queue_payload["queue_kind"] == "approval"
        assert queue_payload["target_kind"] == "tool"
        assert queue_payload["target_name"] == "read_file"
        assert blocked_payload["tool_name"] == "read_file"
        assert blocked_payload["blocked_kind"] == "approval"
        assert blocked_payload["status"] == "approval_blocked"
        assert queue_item is not None
        assert queue_item.pipeline_run_id == run.id
        assert queue_item.pipeline_stage_id == stage.id
        request_payload = json.loads(queue_item.request_payload_json)
        assert request_payload["resume_supported"] is False
        assert request_payload["pipeline_run_id"] == run.id
        assert request_payload["pipeline_stage_id"] == stage.id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_send_message_creates_durable_delivery_and_consumes_once(fresh_db):
    engine_mod = _reload_pipeline_engine()
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        project = fresh_db.Project(name="Pipeline Messaging Project")
        db.add(project)
        db.commit()
        db.refresh(project)

        pipeline = fresh_db.Pipeline(project_id=project.id, pipeline_name="default", status="running")
        db.add(pipeline)
        db.commit()
        db.refresh(pipeline)

        run = fresh_db.PipelineRun(pipeline_id=pipeline.id, run_number=1, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        result = await engine_mod._handle_send_message(
            from_agent="analyst",
            run=run,
            arguments={
                "to_agent": "developer",
                "content": "Please review the API route file.",
                "message_type": "AGENT_QUESTION",
            },
            db=db,
            stage_id=None,
        )

        assert result == "Message sent to developer"

        deliveries = db.query(fresh_db.PipelineMessageDelivery).all()
        assert len(deliveries) == 1
        assert deliveries[0].to_agent == "developer"
        assert deliveries[0].status == "pending"

        first_batch = engine_mod._pop_messages_for_agent(db, run.id, "developer")
        second_batch = engine_mod._pop_messages_for_agent(db, run.id, "developer")

        assert len(first_batch) == 1
        assert first_batch[0]["from_agent"] == "analyst"
        assert first_batch[0]["content"] == "Please review the API route file."
        assert second_batch == []

        delivery = db.query(fresh_db.PipelineMessageDelivery).first()
        assert delivery.status == "consumed"
        assert delivery.consumed_at is not None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_instruct_appends_pipeline_task_run_event(fresh_db):
    engine_mod = _reload_pipeline_engine()
    _install_test_pipeline_template(engine_mod)
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        _, _, pipeline = _seed_pipeline_project(
            fresh_db,
            db,
            project_name="Pipeline Boss Instruction Project",
        )

        engine = engine_mod.PipelineEngine()
        engine._git_init = lambda run: None
        async def _noop_execute_pipeline(run_id):
            return None
        engine._execute_pipeline = _noop_execute_pipeline

        run = engine.start_pipeline(db, pipeline.id, "Keep the runtime bridge observable.")
        await engine.instruct(db, pipeline.id, "developer", "Inspect the run ledger wiring and report gaps.")

        db.refresh(run)
        task_run = db.query(fresh_db.TaskRun).filter(fresh_db.TaskRun.id == run.task_run_id).first()
        assert task_run is not None
        assert task_run.target_agent_name == "developer"

        events = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.task_run_id == task_run.id)
            .order_by(fresh_db.TaskRunEvent.event_index.asc())
            .all()
        )

        assert [event.event_type for event in events] == [
            "pipeline_run_started",
            "pipeline_boss_instruction",
        ]

        payload = json.loads(events[-1].payload_json)
        assert payload["pipeline_run_id"] == run.id
        assert payload["to_agent"] == "developer"
        assert "run ledger wiring" in payload["content_preview"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_stage_emits_compiled_stage_policy_for_manual_gate(fresh_db, tmp_path):
    engine_mod = _reload_pipeline_engine()
    from pipeline.config import PipelineConfig, StageConfig

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        workspace = tmp_path / "workspace"
        (workspace / "reports").mkdir(parents=True)
        (workspace / "reports" / "summary.md").write_text("# Summary\n", encoding="utf-8")

        project = fresh_db.Project(name="Pipeline Policy Project")
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(
            project_id=project.id,
            title="Policy Chat",
            session_type="project-bound",
        )
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        project.default_chatroom_id = chatroom.id
        db.commit()
        db.refresh(project)

        pipeline = fresh_db.Pipeline(
            project_id=project.id,
            pipeline_name="default",
            status="running",
            current_stage_index=0,
        )
        db.add(pipeline)
        db.commit()
        db.refresh(pipeline)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            project_id=project.id,
            run_kind=engine_mod.PIPELINE_TASK_RUN_KIND,
            status="running",
            title="Gate policy projection",
            user_request="Gate policy projection",
            initiator="user",
            target_agent_name="tester",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        run = fresh_db.PipelineRun(
            pipeline_id=pipeline.id,
            task_run_id=task_run.id,
            run_number=1,
            status="running",
            input_requirement="Gate policy projection",
            workspace_path=str(workspace),
            started_at=datetime.now(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        stage = fresh_db.PipelineStage(
            run_id=run.id,
            stage_name="qa_gate",
            display_name="QA Gate",
            stage_order=0,
            agent_name="tester",
            status="pending",
            gate_type="manual",
        )
        db.add(stage)
        db.commit()
        db.refresh(stage)

        stage_cfg = StageConfig(
            name="qa_gate",
            display_name="QA Gate",
            agent="tester",
            gate="manual",
            timeout_minutes=9,
            expected_artifacts=["reports/summary.md"],
            context_prompt="Validate the delivery and stop for approval.",
            rollback_on_blocker=True,
            max_rollback_count=2,
            rollback_target="analysis",
        )
        template = PipelineConfig(
            name="default",
            description="Manual gate policy test.",
            stages=[stage_cfg],
        )

        engine = engine_mod.PipelineEngine()
        engine._write_skill_full_files = lambda agent_name, stage_cfg_obj, workspace_path: None
        engine._git_commit = lambda run_obj, stage_name: None

        async def _fake_run_agent_stage(db, pipeline, run, stage, stage_cfg, context):
            return "Policy summary"

        engine._run_agent_stage = _fake_run_agent_stage

        success = await engine._execute_stage(
            db=db,
            pipeline=pipeline,
            run=run,
            stage=stage,
            stage_cfg=stage_cfg,
            template=template,
        )

        assert success is True
        db.refresh(stage)
        assert stage.status == "blocked"

        events = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.task_run_id == task_run.id)
            .order_by(fresh_db.TaskRunEvent.event_index.asc())
            .all()
        )
        assert [event.event_type for event in events] == [
            "pipeline_stage_started",
            "pipeline_stage_completed",
            "approval_queue_item_created",
            "pipeline_gate_blocked",
        ]

        started_payload = json.loads(events[0].payload_json)
        queue_payload = json.loads(events[2].payload_json)
        blocked_payload = json.loads(events[-1].payload_json)

        assert started_payload["stage_policy"]["stage_name"] == "qa_gate"
        assert started_payload["stage_policy"]["approval"]["required"] is True
        assert started_payload["stage_policy"]["delivery"]["expected_artifacts"] == ["reports/summary.md"]
        assert started_payload["stage_policy"]["rollback"]["enabled"] is True
        assert started_payload["stage_policy"]["rollback"]["target_stage"] == "analysis"
        assert started_payload["stage_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1

        assert blocked_payload["stage_policy"]["approval"]["kind"] == "manual"
        assert blocked_payload["stage_policy"]["delivery"]["required"] is True
        assert blocked_payload["stage_policy"]["metadata"]["tool_policy_summary"]["tool_count"] >= 1
        assert queue_payload["queue_kind"] == "approval"
        assert queue_payload["target_kind"] == "pipeline_gate"
        assert queue_payload["target_name"] == "qa_gate"
        queue_items = (
            db.query(fresh_db.ApprovalQueueItem)
            .filter(fresh_db.ApprovalQueueItem.task_run_id == task_run.id)
            .order_by(fresh_db.ApprovalQueueItem.created_at.desc())
            .all()
        )
        assert len(queue_items) == 1
        assert queue_items[0].queue_kind == "approval"
        assert queue_items[0].target_kind == "pipeline_gate"
        assert queue_items[0].target_name == "qa_gate"
        assert queue_items[0].status == "pending"

        artifacts = db.query(fresh_db.StageArtifact).filter(fresh_db.StageArtifact.stage_id == stage.id).all()
        assert len(artifacts) == 1
        assert artifacts[0].file_path == "reports/summary.md"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_approve_resolves_pipeline_gate_queue_item(fresh_db, tmp_path):
    engine_mod = _reload_pipeline_engine()
    from pipeline.config import PipelineConfig, StageConfig

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        workspace = tmp_path / "workspace"
        (workspace / "reports").mkdir(parents=True)
        (workspace / "reports" / "summary.md").write_text("# Summary\n", encoding="utf-8")

        project = fresh_db.Project(name="Pipeline Gate Queue Project")
        db.add(project)
        db.commit()
        db.refresh(project)

        chatroom = fresh_db.Chatroom(
            project_id=project.id,
            title="Queue Chat",
            session_type="project-bound",
        )
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        project.default_chatroom_id = chatroom.id
        db.commit()
        db.refresh(project)

        pipeline = fresh_db.Pipeline(
            project_id=project.id,
            pipeline_name="default",
            status="running",
            current_stage_index=0,
        )
        db.add(pipeline)
        db.commit()
        db.refresh(pipeline)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            project_id=project.id,
            run_kind=engine_mod.PIPELINE_TASK_RUN_KIND,
            status="running",
            title="Gate queue approval",
            user_request="Gate queue approval",
            initiator="user",
            target_agent_name="tester",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        run = fresh_db.PipelineRun(
            pipeline_id=pipeline.id,
            task_run_id=task_run.id,
            run_number=1,
            status="running",
            input_requirement="Gate queue approval",
            workspace_path=str(workspace),
            started_at=datetime.now(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        stage = fresh_db.PipelineStage(
            run_id=run.id,
            stage_name="qa_gate",
            display_name="QA Gate",
            stage_order=0,
            agent_name="tester",
            status="blocked",
            gate_type="manual",
        )
        db.add(stage)
        db.commit()
        db.refresh(stage)

        stage_cfg = StageConfig(
            name="qa_gate",
            display_name="QA Gate",
            agent="tester",
            gate="manual",
            timeout_minutes=9,
            expected_artifacts=["reports/summary.md"],
            context_prompt="Validate the delivery and stop for approval.",
            rollback_on_blocker=True,
            max_rollback_count=2,
            rollback_target="analysis",
        )
        template = PipelineConfig(
            name="default",
            description="Manual gate policy test.",
            stages=[stage_cfg],
        )
        engine_mod.pipeline_config_manager.configs["default"] = template

        queue_item = fresh_db.ApprovalQueueItem(
            task_run_id=task_run.id,
            chatroom_id=chatroom.id,
            project_id=project.id,
            pipeline_run_id=run.id,
            pipeline_stage_id=stage.id,
            queue_kind="approval",
            status="pending",
            source="pipeline_gate",
            title="Approve pipeline gate: QA Gate",
            summary="Pipeline is waiting for approval at QA Gate.",
            agent_name="tester",
            target_kind="pipeline_gate",
            target_name="qa_gate",
            request_key=f"pipeline_gate:{run.id}:qa_gate",
            request_payload_json=json.dumps(
                {
                    "pipeline_id": pipeline.id,
                    "pipeline_run_id": run.id,
                    "pipeline_stage_id": stage.id,
                    "stage_name": "qa_gate",
                    "display_name": "QA Gate",
                    "resume_supported": True,
                },
                ensure_ascii=False,
            ),
        )
        db.add(queue_item)
        db.commit()
        db.refresh(queue_item)

        engine = engine_mod.PipelineEngine()
        await engine.approve(db, pipeline.id)

        db.refresh(stage)
        db.refresh(queue_item)
        assert stage.status == "completed"
        assert queue_item.status == "approved"
        assert queue_item.resolved_by == "user"

        events = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.task_run_id == task_run.id)
            .order_by(fresh_db.TaskRunEvent.event_index.asc())
            .all()
        )
        assert [event.event_type for event in events] == [
            "pipeline_gate_approved",
            "approval_queue_item_resolved",
        ]
        resolved_payload = json.loads(events[-1].payload_json)
        assert resolved_payload["queue_item_id"] == queue_item.id
        assert resolved_payload["status"] == "approved"
        assert resolved_payload["target_kind"] == "pipeline_gate"
    finally:
        db.close()
