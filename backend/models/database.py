# -*- coding: utf-8 -*-
"""
Database model definitions.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

from agents.identity import (
    DEFAULT_AGENT_TYPE,
    default_agent_name,
    is_legacy_default_agent_name,
    normalize_agent_type,
)
from config import settings

# Create engine
_engine_kwargs = {}
if settings.SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
if not settings.SQLALCHEMY_DATABASE_URL.startswith("sqlite:///:memory:"):
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
    _engine_kwargs["pool_timeout"] = settings.DB_POOL_TIMEOUT

engine = create_engine(
    settings.SQLALCHEMY_DATABASE_URL,
    **_engine_kwargs,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Agent(Base):
    """Agent model."""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    agent_type = Column(String, unique=True, index=True, nullable=True)
    name = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, nullable=False)
    soul = Column(Text, default="{}")
    tools = Column(Text, default="[]")
    skills = Column(Text, default="[]")
    config = Column(Text, default="{}")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    memories = relationship("Memory", back_populates="agent")
    messages = relationship("Message", back_populates="agent")

    @property
    def system_prompt(self) -> str:
        """Assemble a system prompt from the stored SOUL data."""
        import json as _json

        try:
            soul_data = _json.loads(self.soul) if self.soul else {}
        except (_json.JSONDecodeError, TypeError):
            soul_data = {}

        parts = []
        identity = soul_data.get("identity", "")
        values = soul_data.get("values", [])
        style = soul_data.get("style", "")

        if identity:
            parts.append(f"你是 {self.name}。{identity}")
        else:
            parts.append(f"你是 {self.name}，一个{self.role}。")

        if values:
            parts.append("你的原则：\n" + "\n".join(f"- {v}" for v in values))
        if style:
            parts.append(f"沟通风格：{style}")

        responsibilities = soul_data.get("responsibilities", [])
        if responsibilities:
            parts.append("## 职责\n" + "\n".join(f"- {r}" for r in responsibilities))

        rules = soul_data.get("rules", [])
        if rules:
            parts.append("## 规则\n" + "\n".join(f"- {r}" for r in rules))

        try:
            full_config = _json.loads(self.config) if self.config else {}
        except (_json.JSONDecodeError, TypeError):
            full_config = {}

        role_cfg = full_config.get("role", {})
        if role_cfg.get("responsibilities") and not responsibilities:
            parts.append("## 职责\n" + "\n".join(f"- {r}" for r in role_cfg["responsibilities"]))
        if role_cfg.get("rules") and not rules:
            parts.append("## 规则\n" + "\n".join(f"- {r}" for r in role_cfg["rules"]))

        return "\n\n".join(parts) if parts else f"You are {self.name}, a {self.role}."

    @property
    def type(self) -> str:
        return normalize_agent_type(self.agent_type or self.name)


class Project(Base):
    """Project model."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, default="active")
    display_order = Column(Integer, default=0, nullable=False)
    default_chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=True)
    workspace_path = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    repo_url = Column(String, nullable=True)
    repo_full_name = Column(String, nullable=True)
    clone_ref = Column(String, nullable=True)
    slug = Column(String, unique=True, index=True, nullable=True)
    one_line_vision = Column(Text)
    target_users_json = Column(Text, default="[]")
    target_platforms_json = Column(Text, default="[]")
    primary_outcome = Column(Text)
    references_json = Column(Text, default="[]")
    current_stage = Column(String)
    execution_mode = Column(String, default="autopilot")
    health_status = Column(String, default="healthy")
    autopilot_enabled = Column(Boolean, default=True)
    current_focus = Column(Text)
    blocking_reason = Column(Text)
    latest_summary = Column(Text)
    last_decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=True)
    last_activity_at = Column(DateTime, default=datetime.now)
    released_at = Column(DateTime)
    legacy_mode = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    chatroom = relationship(
        "Chatroom",
        uselist=False,
        back_populates="project",
        foreign_keys="Chatroom.project_id",
    )
    default_chatroom = relationship("Chatroom", foreign_keys=[default_chatroom_id], post_update=True)
    agent_assignments = relationship("AgentAssignment", back_populates="project")
    pipeline = relationship("Pipeline", uselist=False, back_populates="project")
    task_runs = relationship("TaskRun", back_populates="project", order_by="TaskRun.created_at.desc()")
    assets = relationship("Asset", back_populates="project")
    decisions = relationship("Decision", back_populates="project", foreign_keys="Decision.project_id")
    stage_runs = relationship("StageRun", back_populates="project")


class Chatroom(Base):
    """Chatroom model."""

    __tablename__ = "chatrooms"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), unique=True, nullable=True)
    title = Column(String, nullable=False, default="New Chat")
    session_type = Column(String, nullable=False, default="standalone")
    is_visible_in_chat_list = Column(Boolean, default=True)
    source_chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=True)
    message_visibility = Column(String, default="all")
    created_at = Column(DateTime, default=datetime.now)

    project = relationship("Project", back_populates="chatroom", foreign_keys=[project_id])
    source_chatroom = relationship("Chatroom", remote_side=[id], foreign_keys=[source_chatroom_id])
    messages = relationship("Message", back_populates="chatroom")
    task_runs = relationship("TaskRun", back_populates="chatroom", order_by="TaskRun.created_at.desc()")


class AgentAssignment(Base):
    """Agent-project assignment."""

    __tablename__ = "agent_assignments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.now)

    project = relationship("Project", back_populates="agent_assignments")
    agent = relationship("Agent")


class Message(Base):
    """Chat message model."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    content = Column(Text, nullable=False)
    message_type = Column(String, default="text")
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)

    chatroom = relationship("Chatroom", back_populates="messages")
    agent = relationship("Agent", back_populates="messages")


class TaskRun(Base):
    """Run-level ledger for chat/runtime orchestration."""

    __tablename__ = "task_runs"

    id = Column(Integer, primary_key=True, index=True)
    chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    origin_message_id = Column(Integer, ForeignKey("messages.id"), nullable=True, unique=True, index=True)
    client_turn_id = Column(String, nullable=True, index=True)
    run_kind = Column(String, nullable=False, default="chat_turn", index=True)
    status = Column(String, nullable=False, default="running", index=True)
    title = Column(String, nullable=False, default="Task run")
    user_request = Column(Text)
    initiator = Column(String, nullable=True)
    target_agent_name = Column(String, nullable=True, index=True)
    blocked_by_queue_item_id = Column(Integer, ForeignKey("approval_queue_items.id"), nullable=True, index=True)
    recovery_owner = Column(String, nullable=True, index=True)
    recovery_claimed_at = Column(DateTime, nullable=True, index=True)
    recovery_lease_expires_at = Column(DateTime, nullable=True, index=True)
    summary = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    completed_at = Column(DateTime, nullable=True, index=True)

    chatroom = relationship("Chatroom", back_populates="task_runs")
    project = relationship("Project", back_populates="task_runs")
    origin_message = relationship("Message", foreign_keys=[origin_message_id])
    events = relationship(
        "TaskRunEvent",
        back_populates="task_run",
        cascade="all, delete-orphan",
        order_by="TaskRunEvent.event_index.asc()",
    )
    checkpoints = relationship(
        "TaskRunCheckpoint",
        back_populates="task_run",
        cascade="all, delete-orphan",
        order_by="TaskRunCheckpoint.event_index.desc()",
    )
    approval_queue_items = relationship(
        "ApprovalQueueItem",
        back_populates="task_run",
        cascade="all, delete-orphan",
        order_by="ApprovalQueueItem.created_at.desc()",
        foreign_keys="[ApprovalQueueItem.task_run_id]",
    )
    blocked_by_queue_item = relationship(
        "ApprovalQueueItem",
        foreign_keys=[blocked_by_queue_item_id],
        post_update=True,
    )
    pipeline_runs = relationship("PipelineRun", back_populates="task_run", order_by="PipelineRun.run_number.asc()")
    orchestration_handoff_deliveries = relationship(
        "OrchestrationHandoffDelivery",
        back_populates="task_run",
        order_by="OrchestrationHandoffDelivery.created_at.asc()",
        cascade="all, delete-orphan",
    )


class TaskRunEvent(Base):
    """Ordered entries for a chat/runtime orchestration run."""

    __tablename__ = "task_run_events"

    id = Column(Integer, primary_key=True, index=True)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=False, index=True)
    event_index = Column(Integer, nullable=False, default=1)
    event_type = Column(String, nullable=False, index=True)
    agent_name = Column(String, nullable=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=True, index=True)
    summary = Column(Text)
    payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)

    task_run = relationship("TaskRun", back_populates="events")
    message = relationship("Message", foreign_keys=[message_id])


class TaskRunCheckpoint(Base):
    """Periodic state snapshots for fast recovery of long-running task runs."""

    __tablename__ = "task_run_checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=False, index=True)
    event_index = Column(Integer, nullable=False, default=0)
    snapshot_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)

    task_run = relationship("TaskRun", back_populates="checkpoints")


class OrchestrationHandoffDelivery(Base):
    """Durable orchestration handoff inbox entry for a scheduler step."""

    __tablename__ = "orchestration_handoff_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=False, index=True)
    from_agent = Column(String, nullable=False)
    to_agent = Column(String, nullable=False, index=True)
    from_step_id = Column(String, nullable=False, index=True)
    to_step_id = Column(String, nullable=False, index=True)
    dispatch_kind = Column(String, nullable=True)
    attached_to_step_id = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    leased_at = Column(DateTime, nullable=True, index=True)
    lease_owner = Column(String, nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    consumed_at = Column(DateTime, nullable=True, index=True)
    failed_at = Column(DateTime, nullable=True, index=True)

    task_run = relationship("TaskRun", back_populates="orchestration_handoff_deliveries")


class ApprovalQueueItem(Base):
    """Pending approval/escalation request attached to runtime execution."""

    __tablename__ = "approval_queue_items"

    id = Column(Integer, primary_key=True, index=True)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=True, index=True)
    chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    pipeline_run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    pipeline_stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True, index=True)
    queue_kind = Column(String, nullable=False, default="approval", index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    source = Column(String, nullable=False, default="runtime", index=True)
    title = Column(String, nullable=False)
    summary = Column(Text)
    agent_name = Column(String, nullable=True, index=True)
    target_kind = Column(String, nullable=False, default="tool", index=True)
    target_name = Column(String, nullable=True, index=True)
    request_key = Column(String, nullable=True, index=True)
    resume_token = Column(String, nullable=True, index=True)
    resolution_owner = Column(String, nullable=True, index=True)
    resolution_lease_expires_at = Column(DateTime, nullable=True, index=True)
    request_payload_json = Column(Text, default="{}")
    resolution_note = Column(Text)
    resolution_payload_json = Column(Text, default="{}")
    resolved_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    resolved_at = Column(DateTime, nullable=True, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)

    task_run = relationship("TaskRun", back_populates="approval_queue_items", foreign_keys=[task_run_id])
    chatroom = relationship("Chatroom")
    project = relationship("Project")
    pipeline_run = relationship("PipelineRun")
    pipeline_stage = relationship("PipelineStage")


class ToolExecutionPreference(Base):
    """Persistent authorization/preference rules for specific tool invocations."""

    __tablename__ = "tool_execution_preferences"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=True, index=True)
    agent_name = Column(String, nullable=True, index=True)
    tool_name = Column(String, nullable=False, index=True)
    scope = Column(String, nullable=False, default="project", index=True)
    matcher_type = Column(String, nullable=False, default="opaque", index=True)
    matcher_value = Column(Text, nullable=True)
    decision_kind = Column(String, nullable=False, default="allow", index=True)
    preference_key = Column(String, nullable=False, index=True)
    preference_kind = Column(String, nullable=False, default="timeout_behavior", index=True)
    preference_value = Column(String, nullable=False, default="default")
    constraints_json = Column(Text, default="{}")
    expires_at = Column(DateTime, nullable=True, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    command_preview = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    project = relationship("Project")
    chatroom = relationship("Chatroom")


class ApprovalAuditLog(Base):
    """Immutable audit log for manual and remembered approval decisions."""

    __tablename__ = "approval_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_kind = Column(String, nullable=False, index=True)
    decision = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False, default="runtime", index=True)
    resolved_by = Column(String, nullable=True, index=True)
    queue_item_id = Column(Integer, ForeignKey("approval_queue_items.id"), nullable=True, index=True)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=True, index=True)
    chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    pipeline_run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    pipeline_stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True, index=True)
    preference_id = Column(Integer, ForeignKey("tool_execution_preferences.id"), nullable=True, index=True)
    agent_name = Column(String, nullable=True, index=True)
    target_kind = Column(String, nullable=True, index=True)
    target_name = Column(String, nullable=True, index=True)
    tool_name = Column(String, nullable=True, index=True)
    scope = Column(String, nullable=True, index=True)
    matcher_type = Column(String, nullable=True, index=True)
    matcher_value = Column(Text, nullable=True)
    command_preview = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    request_payload_json = Column(Text, default="{}")
    resolution_payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)

    queue_item = relationship("ApprovalQueueItem")
    task_run = relationship("TaskRun")
    chatroom = relationship("Chatroom")
    project = relationship("Project")
    preference = relationship("ToolExecutionPreference")


class Memory(Base):
    """Agent memory model."""

    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    memory_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, default="{}")
    importance = Column(Integer, default=5)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

    agent = relationship("Agent", back_populates="memories")


class Pipeline(Base):
    """Pipeline definition."""

    __tablename__ = "pipelines"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), unique=True, nullable=False)
    pipeline_name = Column(String, nullable=False, default="default")
    status = Column(String, default="pending")
    current_stage_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    project = relationship("Project")
    runs = relationship("PipelineRun", back_populates="pipeline", order_by="PipelineRun.run_number")


class PipelineRun(Base):
    """Pipeline run instance."""

    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(Integer, ForeignKey("pipelines.id"), nullable=False)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=True, index=True)
    run_number = Column(Integer, nullable=False, default=1)
    status = Column(String, default="pending")
    input_requirement = Column(Text)
    workspace_path = Column(String)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    pipeline = relationship("Pipeline", back_populates="runs")
    task_run = relationship("TaskRun", back_populates="pipeline_runs")
    stages = relationship("PipelineStage", back_populates="run", order_by="PipelineStage.stage_order")
    messages = relationship("PipelineMessage", back_populates="run")


class PipelineStage(Base):
    """Pipeline stage record."""

    __tablename__ = "pipeline_stages"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    stage_name = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    stage_order = Column(Integer, nullable=False)
    agent_name = Column(String, nullable=False)
    status = Column(String, default="pending")
    gate_type = Column(String, default="auto")
    input_context = Column(Text)
    output_summary = Column(Text)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    run = relationship("PipelineRun", back_populates="stages")
    artifacts = relationship("StageArtifact", back_populates="stage")


class StageArtifact(Base):
    """Stage artifact record."""

    __tablename__ = "stage_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=False)
    artifact_type = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    summary = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    stage = relationship("PipelineStage", back_populates="artifacts")


class PipelineMessage(Base):
    """Inter-agent pipeline message."""

    __tablename__ = "pipeline_messages"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True)
    message_type = Column(String, nullable=False)
    from_agent = Column(String, nullable=False)
    to_agent = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    run = relationship("PipelineRun", back_populates="messages")
    deliveries = relationship("PipelineMessageDelivery", back_populates="message", cascade="all, delete-orphan")


class PipelineMessageDelivery(Base):
    """Durable inbox entry for a pipeline message recipient."""

    __tablename__ = "pipeline_message_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("pipeline_messages.id"), nullable=False, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    to_agent = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    leased_at = Column(DateTime, nullable=True, index=True)
    lease_owner = Column(String, nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    failed_at = Column(DateTime, nullable=True, index=True)
    consumed_at = Column(DateTime, nullable=True, index=True)

    message = relationship("PipelineMessage", back_populates="deliveries")
    run = relationship("PipelineRun")


class Asset(Base):
    """Project asset."""

    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    asset_type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text)
    content_json = Column(Text, default="{}")
    content_markdown = Column(Text)
    version = Column(Integer, default=1)
    status = Column(String, default="draft", index=True)
    is_current = Column(Boolean, default=True)
    owner_agent = Column(String)
    produced_by_stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=True)
    supersedes_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    approval_decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=True)
    source_input_refs_json = Column(Text, default="[]")
    storage_path = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    approved_at = Column(DateTime)

    project = relationship("Project", back_populates="assets")
    produced_by_stage_run = relationship("StageRun", back_populates="produced_assets", foreign_keys=[produced_by_stage_run_id])


class Decision(Base):
    """Human decision object."""

    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=True, index=True)
    decision_type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    context_summary = Column(Text)
    recommended_option = Column(String)
    alternative_options_json = Column(Text, default="[]")
    impact_summary = Column(Text)
    requested_action = Column(Text)
    status = Column(String, default="pending", index=True)
    resolved_option = Column(String)
    resolution_note = Column(Text)
    blocking_stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=True)
    created_by_system_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime)
    expires_at = Column(DateTime)

    project = relationship("Project", back_populates="decisions", foreign_keys=[project_id])
    stage_run = relationship("StageRun", back_populates="decisions", foreign_keys=[stage_run_id])


class StageRun(Base):
    """Stage progression instance."""

    __tablename__ = "stage_runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_type = Column(String, nullable=False, index=True)
    run_index = Column(Integer, default=1)
    status = Column(String, default="queued", index=True)
    triggered_by = Column(String)
    trigger_reason = Column(Text)
    execution_mode_snapshot = Column(String)
    summary = Column(Text)
    checkpoint_summary = Column(Text)
    failed_reason = Column(Text)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    project = relationship("Project", back_populates="stage_runs")
    decisions = relationship("Decision", back_populates="stage_run", foreign_keys="Decision.stage_run_id")
    produced_assets = relationship("Asset", back_populates="produced_by_stage_run", foreign_keys="Asset.produced_by_stage_run_id")


class AssetLink(Base):
    """Asset dependency relation."""

    __tablename__ = "asset_links"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    from_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    to_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    relation_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class DecisionAsset(Base):
    """Decision to asset relation."""

    __tablename__ = "decision_assets"

    id = Column(Integer, primary_key=True, index=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    relation_role = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class StageRunAsset(Base):
    """Stage-run input/output asset relation."""

    __tablename__ = "stage_run_assets"

    id = Column(Integer, primary_key=True, index=True)
    stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    direction = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


def init_database():
    """Initialize the database."""
    from models import audit  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        existing_agent_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(agents)")).fetchall()
        }
        if "agent_type" not in existing_agent_columns:
            connection.execute(text("ALTER TABLE agents ADD COLUMN agent_type VARCHAR"))
        agent_rows = connection.execute(text("SELECT id, agent_type, name FROM agents ORDER BY id ASC")).fetchall()
        grouped_agents: dict[str, list[tuple[int, str]]] = {}
        for agent_id, agent_type, name in agent_rows:
            normalized_type = normalize_agent_type(agent_type or name)
            grouped_agents.setdefault(normalized_type, []).append((agent_id, (name or "").strip()))

        chosen_names: dict[str, str] = {}
        used_names: set[str] = set()
        for normalized_type, rows in grouped_agents.items():
            preferred_name = next(
                (
                    raw_name
                    for _, raw_name in rows
                    if raw_name and not is_legacy_default_agent_name(raw_name, normalized_type)
                ),
                default_agent_name(normalized_type),
            )
            resolved_name = preferred_name
            if resolved_name in used_names:
                resolved_name = default_agent_name(normalized_type)
            chosen_names[normalized_type] = resolved_name
            used_names.add(resolved_name)

        for normalized_type, rows in grouped_agents.items():
            keeper_id, _ = rows[0]
            duplicate_ids = [agent_id for agent_id, _ in rows[1:]]

            for duplicate_id in duplicate_ids:
                connection.execute(
                    text("UPDATE agent_assignments SET agent_id = :keeper_id WHERE agent_id = :duplicate_id"),
                    {"keeper_id": keeper_id, "duplicate_id": duplicate_id},
                )
                connection.execute(
                    text("UPDATE messages SET agent_id = :keeper_id WHERE agent_id = :duplicate_id"),
                    {"keeper_id": keeper_id, "duplicate_id": duplicate_id},
                )
                connection.execute(
                    text("UPDATE memories SET agent_id = :keeper_id WHERE agent_id = :duplicate_id"),
                    {"keeper_id": keeper_id, "duplicate_id": duplicate_id},
                )
                connection.execute(text("DELETE FROM agents WHERE id = :duplicate_id"), {"duplicate_id": duplicate_id})

            connection.execute(
                text("UPDATE agents SET agent_type = :agent_type, name = :name WHERE id = :agent_id"),
                {
                    "agent_type": normalized_type,
                    "name": chosen_names[normalized_type],
                    "agent_id": keeper_id,
                },
            )

        duplicate_assignments = connection.execute(
            text(
                "SELECT MIN(id) AS keeper_id, project_id, agent_id "
                "FROM agent_assignments GROUP BY project_id, agent_id HAVING COUNT(*) > 1"
            )
        ).fetchall()
        for keeper_id, project_id, agent_id in duplicate_assignments:
            connection.execute(
                text(
                    "DELETE FROM agent_assignments "
                    "WHERE project_id = :project_id AND agent_id = :agent_id AND id != :keeper_id"
                ),
                {"project_id": project_id, "agent_id": agent_id, "keeper_id": keeper_id},
            )

        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_agent_type ON agents (agent_type)"))

        existing_project_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(projects)")).fetchall()
        }
        if "default_chatroom_id" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN default_chatroom_id INTEGER"))
        if "workspace_path" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN workspace_path VARCHAR"))
        if "source_type" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN source_type VARCHAR"))
        if "repo_url" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN repo_url VARCHAR"))
        if "repo_full_name" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN repo_full_name VARCHAR"))
        if "clone_ref" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN clone_ref VARCHAR"))
        if "display_order" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN display_order INTEGER DEFAULT 0"))
        ordered_project_ids = [
            row[0]
            for row in connection.execute(text("SELECT id FROM projects ORDER BY display_order ASC, id ASC")).fetchall()
        ]
        for index, project_id in enumerate(ordered_project_ids):
            connection.execute(
                text("UPDATE projects SET display_order = :display_order WHERE id = :project_id"),
                {"display_order": index, "project_id": project_id},
            )

        existing_chatroom_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(chatrooms)")).fetchall()
        }
        if "title" not in existing_chatroom_columns:
            connection.execute(text("ALTER TABLE chatrooms ADD COLUMN title VARCHAR"))
            connection.execute(text("UPDATE chatrooms SET title = 'Project Chat' WHERE title IS NULL"))
        if "session_type" not in existing_chatroom_columns:
            connection.execute(text("ALTER TABLE chatrooms ADD COLUMN session_type VARCHAR DEFAULT 'standalone'"))
            connection.execute(
                text(
                    "UPDATE chatrooms SET session_type = CASE "
                    "WHEN project_id IS NULL THEN 'standalone' ELSE 'project-bound' END "
                    "WHERE session_type IS NULL"
                )
            )
        if "is_visible_in_chat_list" not in existing_chatroom_columns:
            connection.execute(text("ALTER TABLE chatrooms ADD COLUMN is_visible_in_chat_list BOOLEAN DEFAULT 1"))
            connection.execute(
                text(
                    "UPDATE chatrooms SET is_visible_in_chat_list = CASE "
                    "WHEN project_id IS NULL THEN 1 ELSE 0 END "
                    "WHERE is_visible_in_chat_list IS NULL"
                )
            )
        if "source_chatroom_id" not in existing_chatroom_columns:
            connection.execute(text("ALTER TABLE chatrooms ADD COLUMN source_chatroom_id INTEGER"))

        existing_approval_queue_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(approval_queue_items)")).fetchall()
        }
        if existing_approval_queue_columns:
            if "resume_token" not in existing_approval_queue_columns:
                connection.execute(text("ALTER TABLE approval_queue_items ADD COLUMN resume_token VARCHAR"))
            if "resolution_owner" not in existing_approval_queue_columns:
                connection.execute(text("ALTER TABLE approval_queue_items ADD COLUMN resolution_owner VARCHAR"))
            if "resolution_lease_expires_at" not in existing_approval_queue_columns:
                connection.execute(text("ALTER TABLE approval_queue_items ADD COLUMN resolution_lease_expires_at DATETIME"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_approval_queue_items_resume_token ON approval_queue_items (resume_token)")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_approval_queue_items_resolution_owner "
                    "ON approval_queue_items (resolution_owner)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_approval_queue_items_resolution_lease_expires_at "
                    "ON approval_queue_items (resolution_lease_expires_at)"
                )
            )
            missing_token_rows = connection.execute(
                text("SELECT id FROM approval_queue_items WHERE resume_token IS NULL OR resume_token = ''")
            ).fetchall()
            for (queue_item_id,) in missing_token_rows:
                connection.execute(
                    text("UPDATE approval_queue_items SET resume_token = :resume_token WHERE id = :id"),
                    {"resume_token": uuid4().hex, "id": queue_item_id},
                )

        existing_approval_audit_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(approval_audit_logs)")).fetchall()
        }
        if existing_approval_audit_columns:
            approval_audit_columns = {
                "event_kind": "VARCHAR",
                "decision": "VARCHAR",
                "source": "VARCHAR DEFAULT 'runtime'",
                "resolved_by": "VARCHAR",
                "queue_item_id": "INTEGER",
                "task_run_id": "INTEGER",
                "chatroom_id": "INTEGER",
                "project_id": "INTEGER",
                "pipeline_run_id": "INTEGER",
                "pipeline_stage_id": "INTEGER",
                "preference_id": "INTEGER",
                "agent_name": "VARCHAR",
                "target_kind": "VARCHAR",
                "target_name": "VARCHAR",
                "tool_name": "VARCHAR",
                "scope": "VARCHAR",
                "matcher_type": "VARCHAR",
                "matcher_value": "TEXT",
                "command_preview": "TEXT",
                "reason": "TEXT",
                "request_payload_json": "TEXT DEFAULT '{}'",
                "resolution_payload_json": "TEXT DEFAULT '{}'",
                "created_at": "DATETIME",
            }
            for column_name, column_type in approval_audit_columns.items():
                if column_name not in existing_approval_audit_columns:
                    connection.execute(text(f"ALTER TABLE approval_audit_logs ADD COLUMN {column_name} {column_type}"))

        for index_sql in [
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_event_kind ON approval_audit_logs (event_kind)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_decision ON approval_audit_logs (decision)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_source ON approval_audit_logs (source)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_resolved_by ON approval_audit_logs (resolved_by)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_queue_item_id ON approval_audit_logs (queue_item_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_task_run_id ON approval_audit_logs (task_run_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_chatroom_id ON approval_audit_logs (chatroom_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_project_id ON approval_audit_logs (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_pipeline_run_id ON approval_audit_logs (pipeline_run_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_pipeline_stage_id ON approval_audit_logs (pipeline_stage_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_preference_id ON approval_audit_logs (preference_id)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_agent_name ON approval_audit_logs (agent_name)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_target_kind ON approval_audit_logs (target_kind)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_target_name ON approval_audit_logs (target_name)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_tool_name ON approval_audit_logs (tool_name)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_scope ON approval_audit_logs (scope)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_matcher_type ON approval_audit_logs (matcher_type)",
            "CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_created_at ON approval_audit_logs (created_at)",
        ]:
            connection.execute(text(index_sql))

        existing_task_run_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(task_runs)")).fetchall()
        }
        if "recovery_owner" not in existing_task_run_columns:
            connection.execute(text("ALTER TABLE task_runs ADD COLUMN recovery_owner VARCHAR"))
        if "recovery_claimed_at" not in existing_task_run_columns:
            connection.execute(text("ALTER TABLE task_runs ADD COLUMN recovery_claimed_at DATETIME"))
        if "recovery_lease_expires_at" not in existing_task_run_columns:
            connection.execute(text("ALTER TABLE task_runs ADD COLUMN recovery_lease_expires_at DATETIME"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_runs_recovery_owner ON task_runs (recovery_owner)"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_task_runs_recovery_lease_expires_at "
                "ON task_runs (recovery_lease_expires_at)"
            )
        )
        connection.execute(
            text(
                "UPDATE task_runs SET "
                "recovery_owner = NULL, "
                "recovery_claimed_at = NULL, "
                "recovery_lease_expires_at = NULL "
                "WHERE status IS NULL OR status != 'running'"
            )
        )

        existing_tool_execution_preference_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(tool_execution_preferences)")).fetchall()
        }
        if existing_tool_execution_preference_columns:
            if "agent_name" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN agent_name VARCHAR"))
            if "scope" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN scope VARCHAR DEFAULT 'project'"))
            if "matcher_type" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN matcher_type VARCHAR DEFAULT 'opaque'"))
            if "matcher_value" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN matcher_value TEXT"))
            if "decision_kind" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN decision_kind VARCHAR DEFAULT 'allow'"))
            if "constraints_json" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN constraints_json TEXT DEFAULT '{}'"))
            if "expires_at" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN expires_at DATETIME"))
            if "revoked_at" not in existing_tool_execution_preference_columns:
                connection.execute(text("ALTER TABLE tool_execution_preferences ADD COLUMN revoked_at DATETIME"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_lookup "
                    "ON tool_execution_preferences (tool_name, preference_key, preference_kind)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_project "
                    "ON tool_execution_preferences (project_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_chatroom "
                    "ON tool_execution_preferences (chatroom_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_scope "
                    "ON tool_execution_preferences (scope)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_matcher "
                    "ON tool_execution_preferences (tool_name, matcher_type)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_decision "
                    "ON tool_execution_preferences (decision_kind)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_agent_name "
                    "ON tool_execution_preferences (agent_name)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_expires_at "
                    "ON tool_execution_preferences (expires_at)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tool_execution_preferences_revoked_at "
                    "ON tool_execution_preferences (revoked_at)"
                )
            )
            connection.execute(
                text(
                    "UPDATE tool_execution_preferences "
                    "SET scope = CASE "
                    "WHEN project_id IS NOT NULL THEN 'project' "
                    "WHEN chatroom_id IS NOT NULL THEN 'chatroom' "
                    "ELSE 'global' END "
                    "WHERE scope IS NULL OR scope = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE tool_execution_preferences "
                    "SET matcher_type = CASE "
                    "WHEN tool_name = 'run_shell' THEN 'command_fingerprint' "
                    "ELSE 'tool_target' END "
                    "WHERE matcher_type IS NULL OR matcher_type = '' OR matcher_type = 'opaque'"
                )
            )
            connection.execute(
                text(
                    "UPDATE tool_execution_preferences "
                    "SET matcher_value = preference_key "
                    "WHERE (matcher_value IS NULL OR matcher_value = '') AND preference_key IS NOT NULL AND preference_key != ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE tool_execution_preferences "
                    "SET decision_kind = CASE "
                    "WHEN preference_kind = 'timeout_behavior' AND preference_value = 'wait_forever' THEN 'allow_no_timeout' "
                    "ELSE 'allow' END "
                    "WHERE decision_kind IS NULL OR decision_kind = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE tool_execution_preferences "
                    "SET constraints_json = '{}' "
                    "WHERE constraints_json IS NULL OR constraints_json = ''"
                )
            )

        existing_pipeline_run_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(pipeline_runs)")).fetchall()
        }
        if "task_run_id" not in existing_pipeline_run_columns:
            connection.execute(text("ALTER TABLE pipeline_runs ADD COLUMN task_run_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_task_run_id ON pipeline_runs (task_run_id)"))

        existing_pipeline_delivery_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(pipeline_message_deliveries)")).fetchall()
        }
        if existing_pipeline_delivery_columns:
            if "leased_at" not in existing_pipeline_delivery_columns:
                connection.execute(text("ALTER TABLE pipeline_message_deliveries ADD COLUMN leased_at DATETIME"))
            if "lease_owner" not in existing_pipeline_delivery_columns:
                connection.execute(text("ALTER TABLE pipeline_message_deliveries ADD COLUMN lease_owner VARCHAR"))
            if "lease_expires_at" not in existing_pipeline_delivery_columns:
                connection.execute(text("ALTER TABLE pipeline_message_deliveries ADD COLUMN lease_expires_at DATETIME"))
            if "attempt_count" not in existing_pipeline_delivery_columns:
                connection.execute(
                    text("ALTER TABLE pipeline_message_deliveries ADD COLUMN attempt_count INTEGER DEFAULT 0")
                )
            if "last_error" not in existing_pipeline_delivery_columns:
                connection.execute(text("ALTER TABLE pipeline_message_deliveries ADD COLUMN last_error TEXT"))
            if "failed_at" not in existing_pipeline_delivery_columns:
                connection.execute(text("ALTER TABLE pipeline_message_deliveries ADD COLUMN failed_at DATETIME"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_pipeline_message_deliveries_leased_at "
                    "ON pipeline_message_deliveries (leased_at)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_pipeline_message_deliveries_lease_owner "
                    "ON pipeline_message_deliveries (lease_owner)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_pipeline_message_deliveries_lease_expires_at "
                    "ON pipeline_message_deliveries (lease_expires_at)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_pipeline_message_deliveries_failed_at "
                    "ON pipeline_message_deliveries (failed_at)"
                )
            )

        orphan_message_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM messages "
                "WHERE chatroom_id NOT IN (SELECT id FROM chatrooms)"
            )
        ).scalar() or 0
        if orphan_message_count:
            connection.execute(
                text(
                    "DELETE FROM messages "
                    "WHERE chatroom_id NOT IN (SELECT id FROM chatrooms)"
                )
            )

        dangling_source_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM chatrooms "
                "WHERE source_chatroom_id IS NOT NULL "
                "AND source_chatroom_id NOT IN (SELECT id FROM chatrooms)"
            )
        ).scalar() or 0
        if dangling_source_count:
            connection.execute(
                text(
                    "UPDATE chatrooms SET source_chatroom_id = NULL "
                    "WHERE source_chatroom_id IS NOT NULL "
                    "AND source_chatroom_id NOT IN (SELECT id FROM chatrooms)"
                )
            )

        project_rows = connection.execute(
            text("SELECT id, default_chatroom_id FROM projects ORDER BY id ASC")
        ).fetchall()
        repaired_default_chatrooms = 0
        for project_id, default_chatroom_id in project_rows:
            if default_chatroom_id is not None:
                exists = connection.execute(
                    text("SELECT 1 FROM chatrooms WHERE id = :chatroom_id"),
                    {"chatroom_id": default_chatroom_id},
                ).fetchone()
                if exists:
                    continue

            fallback = connection.execute(
                text(
                    "SELECT id FROM chatrooms "
                    "WHERE project_id = :project_id "
                    "ORDER BY id ASC LIMIT 1"
                ),
                {"project_id": project_id},
            ).fetchone()
            next_default_chatroom_id = fallback[0] if fallback else None
            connection.execute(
                text(
                    "UPDATE projects SET default_chatroom_id = :default_chatroom_id "
                    "WHERE id = :project_id"
                ),
                {
                    "default_chatroom_id": next_default_chatroom_id,
                    "project_id": project_id,
                },
            )
            repaired_default_chatrooms += 1

    print(
        "Database initialized successfully "
        f"(repaired_messages={orphan_message_count}, "
        f"repaired_sources={dangling_source_count}, "
        f"repaired_project_defaults={repaired_default_chatrooms})"
    )


def get_db():
    """Yield a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
