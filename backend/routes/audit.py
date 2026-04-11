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

from models.database import get_db
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
    db: Session = Depends(get_db),
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
async def get_llm_call(call_id: int, db: Session = Depends(get_db)):
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
