# -*- coding: utf-8 -*-
"""
审计 API 路由 — 三表明细查询 + 汇总统计
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from models.database import get_telemetry_db
from models.audit import LLMCall, ToolCall, Event

logger = logging.getLogger("catown.audit")

router = APIRouter(prefix="/api/audit", tags=["audit"])


# ==================== LLM 调用查询 ====================

@router.get("/llm")
async def list_llm_calls(
    run_id: Optional[int] = Query(None),
    agent: Optional[str] = Query(None),
    stage_id: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_telemetry_db),
):
    """LLM 调用记录列表"""
    q = db.query(LLMCall).order_by(desc(LLMCall.created_at))
    if run_id is not None:
        q = q.filter(LLMCall.run_id == run_id)
    if agent:
        q = q.filter(LLMCall.agent_name == agent)
    if stage_id is not None:
        q = q.filter(LLMCall.stage_id == stage_id)

    total = q.count()
    calls = q.offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "run_id": c.run_id,
                "stage_id": c.stage_id,
                "agent_name": c.agent_name,
                "turn_index": c.turn_index,
                "model": c.model,
                "token_input": c.token_input,
                "token_output": c.token_output,
                "duration_ms": c.duration_ms,
                "error": c.error,
                "content_preview": (c.response_content or "")[:200],
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in calls
        ],
    }


@router.get("/llm/{call_id}")
async def get_llm_call(call_id: int, db: Session = Depends(get_telemetry_db)):
    """单条 LLM 调用详情（含完整 prompt 和 response）"""
    c = db.query(LLMCall).filter(LLMCall.id == call_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="LLM call not found")

    # 解析 messages JSON
    messages = None
    if c.messages:
        try:
            messages = json.loads(c.messages)
        except json.JSONDecodeError:
            messages = c.messages

    # 解析 tool_calls JSON
    tool_calls = None
    if c.response_tool_calls:
        try:
            tool_calls = json.loads(c.response_tool_calls)
        except json.JSONDecodeError:
            tool_calls = c.response_tool_calls

    # 关联的工具调用
    related_tools = db.query(ToolCall).filter(ToolCall.llm_call_id == c.id).all()

    return {
        "id": c.id,
        "run_id": c.run_id,
        "stage_id": c.stage_id,
        "agent_name": c.agent_name,
        "turn_index": c.turn_index,
        "model": c.model,
        "system_prompt": c.system_prompt,
        "messages": messages,
        "response_content": c.response_content,
        "response_tool_calls": tool_calls,
        "token_input": c.token_input,
        "token_output": c.token_output,
        "duration_ms": c.duration_ms,
        "error": c.error,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "tool_calls": [
            {
                "id": tc.id,
                "tool_name": tc.tool_name,
                "success": tc.success,
                "duration_ms": tc.duration_ms,
                "result_preview": tc.result_summary[:200] if tc.result_summary else None,
            }
            for tc in related_tools
        ],
    }


# ==================== 工具调用查询 ====================

@router.get("/tools")
async def list_tool_calls(
    run_id: Optional[int] = Query(None),
    agent: Optional[str] = Query(None),
    tool_name: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_telemetry_db),
):
    """工具调用记录列表"""
    q = db.query(ToolCall).order_by(desc(ToolCall.created_at))
    if run_id is not None:
        q = q.filter(ToolCall.run_id == run_id)
    if agent:
        q = q.filter(ToolCall.agent_name == agent)
    if tool_name:
        q = q.filter(ToolCall.tool_name == tool_name)

    total = q.count()
    calls = q.offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "llm_call_id": c.llm_call_id,
                "run_id": c.run_id,
                "agent_name": c.agent_name,
                "tool_name": c.tool_name,
                "success": c.success,
                "duration_ms": c.duration_ms,
                "result_length": c.result_length,
                "result_preview": c.result_summary[:200] if c.result_summary else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in calls
        ],
    }


# ==================== 事件流查询 ====================

@router.get("/events")
async def list_events(
    run_id: Optional[int] = Query(None),
    event_type: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_telemetry_db),
):
    """事件流查询"""
    q = db.query(Event).order_by(desc(Event.created_at))
    if run_id is not None:
        q = q.filter(Event.run_id == run_id)
    if event_type:
        q = q.filter(Event.event_type == event_type)
    if agent:
        q = q.filter(Event.agent_name == agent)

    total = q.count()
    events = q.offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": e.id,
                "run_id": e.run_id,
                "event_type": e.event_type,
                "agent_name": e.agent_name,
                "stage_name": e.stage_name,
                "summary": e.summary,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


# ==================== Token 汇总 ====================

@router.get("/tokens/summary")
async def token_summary(
    run_id: Optional[int] = Query(None),
    db: Session = Depends(get_telemetry_db),
):
    """Token 汇总统计 + 成本估算（GPT-4 pricing）"""
    q = db.query(
        LLMCall.agent_name,
        func.count(LLMCall.id).label("call_count"),
        func.sum(LLMCall.token_input).label("total_input"),
        func.sum(LLMCall.token_output).label("total_output"),
        func.sum(LLMCall.duration_ms).label("total_duration_ms"),
    )

    if run_id is not None:
        q = q.filter(LLMCall.run_id == run_id)

    q = q.group_by(LLMCall.agent_name)
    rows = q.all()

    # GPT-4 pricing (USD per 1K tokens) — 简化估算
    INPUT_PRICE = 0.03   # $0.03 / 1K input tokens
    OUTPUT_PRICE = 0.06  # $0.06 / 1K output tokens

    agents = []
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for row in rows:
        inp = row.total_input or 0
        out = row.total_output or 0
        cost = (inp / 1000 * INPUT_PRICE) + (out / 1000 * OUTPUT_PRICE)
        total_input += inp
        total_output += out
        total_cost += cost

        agents.append({
            "agent_name": row.agent_name,
            "call_count": row.call_count,
            "token_input": inp,
            "token_output": out,
            "token_total": inp + out,
            "duration_ms": row.total_duration_ms or 0,
            "estimated_cost_usd": round(cost, 4),
        })

    return {
        "run_id": run_id,
        "agents": agents,
        "total": {
            "call_count": sum(a["call_count"] for a in agents),
            "token_input": total_input,
            "token_output": total_output,
            "token_total": total_input + total_output,
            "estimated_cost_usd": round(total_cost, 4),
        },
        "pricing": {
            "model": "gpt-4 (estimate)",
            "input_per_1k": INPUT_PRICE,
            "output_per_1k": OUTPUT_PRICE,
        },
    }


# ==================== 聚合时间线 ====================

@router.get("/timeline")
async def audit_timeline(
    run_id: int = Query(...),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_telemetry_db),
):
    """聚合时间线：events + llm_calls + tool_calls 混合排序"""
    events = db.query(Event).filter(Event.run_id == run_id).all()
    llm_calls = db.query(LLMCall).filter(LLMCall.run_id == run_id).all()
    tool_calls = db.query(ToolCall).filter(ToolCall.run_id == run_id).all()

    timeline = []

    for e in events:
        timeline.append({
            "type": "event",
            "event_type": e.event_type,
            "agent_name": e.agent_name,
            "stage_name": e.stage_name,
            "summary": e.summary,
            "timestamp": e.created_at.isoformat() if e.created_at else None,
        })

    for c in llm_calls:
        timeline.append({
            "type": "llm_call",
            "id": c.id,
            "agent_name": c.agent_name,
            "turn_index": c.turn_index,
            "model": c.model,
            "token_input": c.token_input,
            "token_output": c.token_output,
            "duration_ms": c.duration_ms,
            "content_preview": (c.response_content or "")[:200],
            "timestamp": c.created_at.isoformat() if c.created_at else None,
        })

    for t in tool_calls:
        timeline.append({
            "type": "tool_call",
            "id": t.id,
            "agent_name": t.agent_name,
            "tool_name": t.tool_name,
            "success": t.success,
            "duration_ms": t.duration_ms,
            "result_preview": (t.result_summary or "")[:200],
            "timestamp": t.created_at.isoformat() if t.created_at else None,
        })

    # 按时间排序
    timeline.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    return {
        "run_id": run_id,
        "total": len(timeline),
        "timeline": timeline[:limit],
    }


@router.get("/overview")
async def audit_overview(
    run_id: Optional[int] = Query(None),
    agent: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    tool_name: Optional[str] = Query(None),
    limit: int = Query(120, ge=10, le=500),
    db: Session = Depends(get_telemetry_db),
):
    """Monitor-oriented audit snapshot with counts and a mixed activity feed."""
    llm_query = db.query(LLMCall)
    tool_query = db.query(ToolCall)
    event_query = db.query(Event)

    if run_id is not None:
        llm_query = llm_query.filter(LLMCall.run_id == run_id)
        tool_query = tool_query.filter(ToolCall.run_id == run_id)
        event_query = event_query.filter(Event.run_id == run_id)
    if agent:
        llm_query = llm_query.filter(LLMCall.agent_name == agent)
        tool_query = tool_query.filter(ToolCall.agent_name == agent)
        event_query = event_query.filter(Event.agent_name == agent)
    if tool_name:
        tool_query = tool_query.filter(ToolCall.tool_name == tool_name)
    if event_type:
        event_query = event_query.filter(Event.event_type == event_type)

    llm_count = llm_query.count()
    tool_count = tool_query.count()
    event_count = event_query.count()

    llm_rows = llm_query.order_by(desc(LLMCall.created_at), desc(LLMCall.id)).limit(limit).all()
    tool_rows = tool_query.order_by(desc(ToolCall.created_at), desc(ToolCall.id)).limit(limit).all()
    event_rows = event_query.order_by(desc(Event.created_at), desc(Event.id)).limit(limit).all()

    timeline: list[dict[str, object | None]] = []

    for row in llm_rows:
        timeline.append({
            "kind": "llm",
            "id": row.id,
            "run_id": row.run_id,
            "stage_id": row.stage_id,
            "agent_name": row.agent_name,
            "model": row.model,
            "turn_index": row.turn_index,
            "token_input": row.token_input,
            "token_output": row.token_output,
            "duration_ms": row.duration_ms,
            "error": row.error,
            "summary": (row.response_content or "")[:220] or None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    for row in tool_rows:
        timeline.append({
            "kind": "tool",
            "id": row.id,
            "run_id": row.run_id,
            "stage_id": row.stage_id,
            "agent_name": row.agent_name,
            "tool_name": row.tool_name,
            "llm_call_id": row.llm_call_id,
            "success": row.success,
            "duration_ms": row.duration_ms,
            "result_length": row.result_length,
            "summary": (row.result_summary or "")[:220] or None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    for row in event_rows:
        timeline.append({
            "kind": "event",
            "id": row.id,
            "run_id": row.run_id,
            "project_id": row.project_id,
            "stage_run_id": row.stage_run_id,
            "asset_id": row.asset_id,
            "event_type": row.event_type,
            "agent_name": row.agent_name,
            "stage_name": row.stage_name,
            "summary": row.summary,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    timeline.sort(key=lambda item: ((item.get("created_at") or ""), int(item.get("id") or 0)), reverse=True)
    visible_timeline = timeline[:limit]

    total_input = 0
    total_output = 0
    total_duration_ms = 0
    errored_llm_calls = 0
    models: dict[str, dict[str, int | str]] = {}
    agents_summary: dict[str, dict[str, int | str]] = {}
    tool_summary: dict[str, int] = {}
    event_summary: dict[str, int] = {}

    for row in llm_rows:
        total_input += int(row.token_input or 0)
        total_output += int(row.token_output or 0)
        total_duration_ms += int(row.duration_ms or 0)
        if row.error:
            errored_llm_calls += 1
        model_key = (row.model or "unknown").strip() or "unknown"
        model_bucket = models.setdefault(model_key, {"name": model_key, "calls": 0, "tokens": 0})
        model_bucket["calls"] = int(model_bucket["calls"]) + 1
        model_bucket["tokens"] = int(model_bucket["tokens"]) + int(row.token_input or 0) + int(row.token_output or 0)

        agent_key = (row.agent_name or "unknown").strip() or "unknown"
        agent_bucket = agents_summary.setdefault(
            agent_key,
            {"name": agent_key, "llm_calls": 0, "tool_calls": 0, "events": 0, "tokens": 0},
        )
        agent_bucket["llm_calls"] = int(agent_bucket["llm_calls"]) + 1
        agent_bucket["tokens"] = int(agent_bucket["tokens"]) + int(row.token_input or 0) + int(row.token_output or 0)

    for row in tool_rows:
        tool_key = (row.tool_name or "unknown").strip() or "unknown"
        tool_summary[tool_key] = tool_summary.get(tool_key, 0) + 1
        agent_key = (row.agent_name or "unknown").strip() or "unknown"
        agent_bucket = agents_summary.setdefault(
            agent_key,
            {"name": agent_key, "llm_calls": 0, "tool_calls": 0, "events": 0, "tokens": 0},
        )
        agent_bucket["tool_calls"] = int(agent_bucket["tool_calls"]) + 1

    for row in event_rows:
        event_key = (row.event_type or "unknown").strip() or "unknown"
        event_summary[event_key] = event_summary.get(event_key, 0) + 1
        agent_key = (row.agent_name or "unknown").strip() or "unknown"
        agent_bucket = agents_summary.setdefault(
            agent_key,
            {"name": agent_key, "llm_calls": 0, "tool_calls": 0, "events": 0, "tokens": 0},
        )
        agent_bucket["events"] = int(agent_bucket["events"]) + 1

    top_models = sorted(models.values(), key=lambda item: (int(item["calls"]), int(item["tokens"])), reverse=True)[:8]
    top_agents = sorted(
        agents_summary.values(),
        key=lambda item: (int(item["llm_calls"]) + int(item["tool_calls"]) + int(item["events"]), int(item["tokens"])),
        reverse=True,
    )[:12]
    top_tools = sorted(
        ({"name": name, "count": count} for name, count in tool_summary.items()),
        key=lambda item: (int(item["count"]), str(item["name"])),
        reverse=True,
    )[:12]
    top_events = sorted(
        ({"name": name, "count": count} for name, count in event_summary.items()),
        key=lambda item: (int(item["count"]), str(item["name"])),
        reverse=True,
    )[:12]

    return {
        "captured_at": datetime.now().isoformat(),
        "filters": {
            "run_id": run_id,
            "agent": agent,
            "event_type": event_type,
            "tool_name": tool_name,
            "limit": limit,
        },
        "counts": {
            "llm_calls": llm_count,
            "tool_calls": tool_count,
            "events": event_count,
            "timeline": llm_count + tool_count + event_count,
            "errored_llm_calls": errored_llm_calls,
        },
        "tokens": {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
        },
        "durations": {
            "llm_total_ms": total_duration_ms,
        },
        "top_models": top_models,
        "top_agents": top_agents,
        "top_tools": top_tools,
        "top_events": top_events,
        "timeline": visible_timeline,
    }
