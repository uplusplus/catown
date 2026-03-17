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
    role = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=False)
    tools = Column(Text, default="[]")  # JSON 字符串
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    
    # 关系
    memories = relationship("Memory", back_populates="agent")
    messages = relationship("Message", back_populates="agent")


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


class Chatroom(Base):
    """聊天室模型"""
    __tablename__ = "chatrooms"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), unique=True, nullable=False)
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


def init_database():
    """初始化数据库"""
    Base.metadata.create_all(bind=engine)
    print("✅ Database initialized successfully")


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
