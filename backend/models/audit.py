# -*- coding: utf-8 -*-
"""
审计数据模型 — 三表采集管道

llm_calls:  LLM 对话全记录（prompt + response + tokens）
tool_calls: 工具执行记录（入参 + 返回摘要 + 耗时）
events:     事件流（阶段流转 / gate / agent 消息 / 错误）
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from models.database import Base


class LLMCall(Base):
    """LLM 对话全记录"""
    __tablename__ = "llm_calls"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True)
    agent_name = Column(String, nullable=False, index=True)
    turn_index = Column(Integer, default=0)
    model = Column(String)
    system_prompt = Column(Text)
    messages = Column(Text)  # JSON
    response_content = Column(Text)
    response_tool_calls = Column(Text)  # JSON
    token_input = Column(Integer, default=0)
    token_output = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    # 关联
    tool_calls = relationship("ToolCall", back_populates="llm_call")

    __table_args__ = (
        Index("ix_llm_calls_run_agent", "run_id", "agent_name"),
    )


class ToolCall(Base):
    """工具执行记录"""
    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    llm_call_id = Column(Integer, ForeignKey("llm_calls.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True)
    agent_name = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False, index=True)
    arguments = Column(Text)  # JSON
    result_summary = Column(Text)  # 前 500 字
    result_length = Column(Integer, default=0)
    success = Column(Boolean, default=True)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now, index=True)

    # 关联
    llm_call = relationship("LLMCall", back_populates="tool_calls")

    __table_args__ = (
        Index("ix_tool_calls_run_agent", "run_id", "agent_name"),
    )


class Event(Base):
    """事件流"""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    # event_type 枚举: stage_start, stage_end, stage_retry, gate_blocked,
    #   gate_approved, gate_rejected, rollback, agent_message, boss_instruction,
    #   error, timeout, llm_call, tool_call
    agent_name = Column(String, nullable=True)
    stage_name = Column(String, nullable=True)
    summary = Column(Text)  # 一句话摘要
    payload = Column(Text)  # JSON 完整详情
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index("ix_events_run_type", "run_id", "event_type"),
    )
