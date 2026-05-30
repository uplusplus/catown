# -*- coding: utf-8 -*-
"""Telemetry audit models stored in the dedicated telemetry database."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from models.database import NetworkAuditBase, TelemetryBase


class LLMCall(TelemetryBase):
    """Captured metadata for one LLM turn."""

    __tablename__ = "llm_calls"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    stage_id = Column(Integer, nullable=True)
    agent_name = Column(String, nullable=False, index=True)
    turn_index = Column(Integer, default=0)
    model = Column(String)
    system_prompt = Column(Text)
    messages = Column(Text)
    response_content = Column(Text)
    response_tool_calls = Column(Text)
    token_input = Column(Integer, default=0)
    token_output = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index("ix_llm_calls_run_agent", "run_id", "agent_name"),
    )


class ToolCall(TelemetryBase):
    """Captured metadata for one tool execution."""

    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    llm_call_id = Column(Integer, nullable=True, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    stage_id = Column(Integer, nullable=True)
    agent_name = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False, index=True)
    arguments = Column(Text)
    result_summary = Column(Text)
    result_length = Column(Integer, default=0)
    success = Column(Boolean, default=True)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index("ix_tool_calls_run_agent", "run_id", "agent_name"),
    )


class Event(TelemetryBase):
    """Audit event stream for runtime milestones and errors."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    stage_run_id = Column(Integer, nullable=True, index=True)
    asset_id = Column(Integer, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    agent_name = Column(String, nullable=True)
    stage_name = Column(String, nullable=True)
    summary = Column(Text)
    payload = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index("ix_events_run_type", "run_id", "event_type"),
    )


class MonitorNetworkBlob(NetworkAuditBase):
    """File-backed raw payload attached to one network audit record."""

    __tablename__ = "monitor_network_blobs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True, nullable=False)
    kind = Column(String, nullable=False, index=True)
    content_sha256 = Column(String, nullable=False, index=True)
    content_bytes = Column(Integer, default=0)
    storage_path = Column(Text, nullable=False)
    content_type = Column(String, nullable=False, default="")

    __table_args__ = (
        Index("ix_monitor_network_blobs_created_kind", "created_at", "kind"),
    )


class MonitorNetworkRecord(NetworkAuditBase):
    """Persisted monitor network events for crash-safe troubleshooting."""

    __tablename__ = "monitor_network_records"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True, nullable=False)
    task_run_id = Column(Integer, nullable=True, index=True)
    chatroom_id = Column(Integer, nullable=True, index=True)
    category = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False, index=True)
    protocol = Column(String, nullable=False)
    from_entity = Column(String, nullable=False, index=True)
    to_entity = Column(String, nullable=False, index=True)
    method = Column(String, nullable=False, default="")
    url = Column(Text, nullable=False, default="")
    host = Column(String, nullable=False, default="", index=True)
    path = Column(String, nullable=False, default="", index=True)
    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=True)
    request_bytes = Column(Integer, default=0)
    response_bytes = Column(Integer, default=0)
    total_bytes = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    content_type = Column(String, nullable=False, default="")
    preview = Column(Text, nullable=False, default="")
    error = Column(Text, nullable=False, default="")
    client_source = Column(String, nullable=False, default="", index=True)
    raw_request = Column(Text, nullable=False, default="")
    raw_response = Column(Text, nullable=False, default="")
    raw_request_blob_id = Column(Integer, nullable=True, index=True)
    raw_response_blob_id = Column(Integer, nullable=True, index=True)
    request_headers_json = Column(Text, nullable=False, default="{}")
    response_headers_json = Column(Text, nullable=False, default="{}")
    metadata_json = Column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_monitor_network_records_category_id", "category", "id"),
        Index("ix_monitor_network_records_created_at_id", "created_at", "id"),
    )
