# -*- coding: utf-8 -*-
"""
数据库模型定义
"""
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
from pathlib import Path

# 数据库配置
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", str(DATA_DIR / "catown.db"))

# 创建引擎
engine = create_engine(
    f"sqlite:///{DATABASE_URL}",
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Agent(Base):
    """Agent 模型"""
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, nullable=False)          # role.title
    soul = Column(Text, default="{}")              # soul 配置 JSON
    tools = Column(Text, default="[]")             # tools JSON
    skills = Column(Text, default="[]")            # skills JSON
    config = Column(Text, default="{}")            # 完整 agent 配置 JSON
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    # 关系
    memories = relationship("Memory", back_populates="agent")
    messages = relationship("Message", back_populates="agent")

    @property
    def system_prompt(self) -> str:
        """从 SOUL JSON 动态组装 system_prompt"""
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

        # 角色职责
        responsibilities = soul_data.get("responsibilities", [])
        if responsibilities:
            parts.append("## 职责\n" + "\n".join(f"- {r}" for r in responsibilities))

        # 规则
        rules = soul_data.get("rules", [])
        if rules:
            parts.append("## 规则\n" + "\n".join(f"- {r}" for r in rules))

        # 如果有完整 config JSON，也从中提取 role 信息
        try:
            full_config = _json.loads(self.config) if self.config else {}
        except (json.JSONDecodeError, TypeError):
            full_config = {}

        role_cfg = full_config.get("role", {})
        if role_cfg.get("responsibilities") and not responsibilities:
            parts.append("## 职责\n" + "\n".join(f"- {r}" for r in role_cfg["responsibilities"]))
        if role_cfg.get("rules") and not rules:
            parts.append("## 规则\n" + "\n".join(f"- {r}" for r in role_cfg["rules"]))

        return "\n\n".join(parts) if parts else f"You are {self.name}, a {self.role}."


class Project(Base):
    """项目模型"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, default="active")  # active, completed, paused
    created_at = Column(DateTime, default=datetime.now)
    
    # 关系
    chatroom = relationship("Chatroom", uselist=False, back_populates="project")
    agent_assignments = relationship("AgentAssignment", back_populates="project")
    pipeline = relationship("Pipeline", uselist=False, back_populates="project")


class Chatroom(Base):
    """聊天室模型"""
    __tablename__ = "chatrooms"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), unique=True, nullable=False)
    message_visibility = Column(String, default="all")  # all=所有agent可见, target=仅目标agent可见
    created_at = Column(DateTime, default=datetime.now)
    
    # 关系
    project = relationship("Project", back_populates="chatroom")
    messages = relationship("Message", back_populates="chatroom")


class AgentAssignment(Base):
    """Agent 项目分配"""
    __tablename__ = "agent_assignments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.now)
    
    # 关系
    project = relationship("Project", back_populates="agent_assignments")
    agent = relationship("Agent")


class Message(Base):
    """消息模型"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)  # None 表示用户消息
    content = Column(Text, nullable=False)
    message_type = Column(String, default="text")  # text, tool_call, tool_response, system
    metadata_json = Column(Text, default="{}")  # 额外的元数据
    created_at = Column(DateTime, default=datetime.now)
    
    # 关系
    chatroom = relationship("Chatroom", back_populates="messages")
    agent = relationship("Agent", back_populates="messages")


class Memory(Base):
    """Agent 记忆模型"""
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    memory_type = Column(String, nullable=False)  # short_term, long_term, procedural
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, default="{}")
    importance = Column(Integer, default=5)  # 1-10
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    
    # 关系
    agent = relationship("Agent", back_populates="memories")


# ==================== Pipeline 模型 ====================

class Pipeline(Base):
    """Pipeline 定义（关联项目）"""
    __tablename__ = "pipelines"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), unique=True, nullable=False)
    pipeline_name = Column(String, nullable=False, default="default")  # 使用的 pipeline 模板名
    status = Column(String, default="pending")  # pending / running / paused / completed / failed
    current_stage_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关系
    project = relationship("Project")
    runs = relationship("PipelineRun", back_populates="pipeline", order_by="PipelineRun.run_number")


class PipelineRun(Base):
    """Pipeline 运行实例（支持重跑）"""
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(Integer, ForeignKey("pipelines.id"), nullable=False)
    run_number = Column(Integer, nullable=False, default=1)  # 第几次运行
    status = Column(String, default="pending")  # pending / running / paused / completed / failed
    input_requirement = Column(Text)  # 用户原始需求
    workspace_path = Column(String)  # 项目 workspace 目录
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    # 关系
    pipeline = relationship("Pipeline", back_populates="runs")
    stages = relationship("PipelineStage", back_populates="run", order_by="PipelineStage.stage_order")
    messages = relationship("PipelineMessage", back_populates="run")


class PipelineStage(Base):
    """Pipeline 阶段记录"""
    __tablename__ = "pipeline_stages"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    stage_name = Column(String, nullable=False)  # analysis / architecture / development / testing / release
    display_name = Column(String, nullable=False)  # 需求分析 / 架构设计 / ...
    stage_order = Column(Integer, nullable=False)  # 阶段顺序 0, 1, 2, ...
    agent_name = Column(String, nullable=False)  # 执行 Agent 名称
    status = Column(String, default="pending")  # pending / running / blocked / completed / failed
    gate_type = Column(String, default="auto")  # auto / manual / condition
    input_context = Column(Text)  # 传入的上下文（JSON）
    output_summary = Column(Text)  # 产出摘要
    error_message = Column(Text)  # 错误信息
    retry_count = Column(Integer, default=0)  # 重试次数
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    # 关系
    run = relationship("PipelineRun", back_populates="stages")
    artifacts = relationship("StageArtifact", back_populates="stage")


class StageArtifact(Base):
    """阶段产出物记录"""
    __tablename__ = "stage_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=False)
    artifact_type = Column(String, nullable=False)  # file / directory
    file_path = Column(String, nullable=False)  # workspace 中的相对路径
    summary = Column(Text)  # 内容摘要
    created_at = Column(DateTime, default=datetime.now)

    # 关系
    stage = relationship("PipelineStage", back_populates="artifacts")


class PipelineMessage(Base):
    """Agent 间协作消息"""
    __tablename__ = "pipeline_messages"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True)
    message_type = Column(String, nullable=False)  # STAGE_OUTPUT / AGENT_QUESTION / AGENT_REPLY / HUMAN_INSTRUCT
    from_agent = Column(String, nullable=False)  # 发送方 Agent 名称
    to_agent = Column(String, nullable=True)  # 接收方（NULL=广播）
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    # 关系
    run = relationship("PipelineRun", back_populates="messages")


def init_database():
    """初始化数据库（含审计表）"""
    # 导入审计模型，确保表被注册到 Base.metadata
    from models import audit  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("✅ Database initialized successfully")


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
