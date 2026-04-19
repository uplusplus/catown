# -*- coding: utf-8 -*-
"""
Database model definitions.
"""
from datetime import datetime
import os
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

# Database configuration
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", str(DATA_DIR / "catown.db"))

# Create engine
engine = create_engine(
    f"sqlite:///{DATABASE_URL}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Agent(Base):
    """Agent model."""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
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
    run_number = Column(Integer, nullable=False, default=1)
    status = Column(String, default="pending")
    input_requirement = Column(Text)
    workspace_path = Column(String)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    pipeline = relationship("Pipeline", back_populates="runs")
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
        existing_project_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(projects)")).fetchall()
        }
        if "default_chatroom_id" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN default_chatroom_id INTEGER"))
        if "workspace_path" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN workspace_path VARCHAR"))
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

    print("Database initialized successfully")


def get_db():
    """Yield a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
