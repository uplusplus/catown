# -*- coding: utf-8 -*-
"""
Pipeline API 路由

提供 Pipeline 的 CRUD、生命周期控制、Agent 协作消息等 REST 接口。
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import (
    get_db, Pipeline, PipelineRun, PipelineStage,
    StageArtifact, PipelineMessage, Project,
)
from pipeline.engine import pipeline_engine, event_bus
from pipeline.config import pipeline_config_manager

logger = logging.getLogger("catown.pipeline.api")

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


# ==================== Pydantic Schemas ====================

class CreatePipelineRequest(BaseModel):
    project_id: int
    pipeline_name: str = "default"


class StartPipelineRequest(BaseModel):
    requirement: str


class RejectRequest(BaseModel):
    rollback_to: Optional[str] = None


class InstructRequest(BaseModel):
    agent_name: str
    message: str


class PipelineStageOut(BaseModel):
    id: int
    stage_name: str
    display_name: str
    stage_order: int
    agent_name: str
    status: str
    gate_type: str
    output_summary: Optional[str]
    error_message: Optional[str]
    retry_count: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class PipelineRunOut(BaseModel):
    id: int
    run_number: int
    status: str
    input_requirement: Optional[str]
    workspace_path: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    stages: List[PipelineStageOut] = []

    class Config:
        from_attributes = True


class PipelineOut(BaseModel):
    id: int
    project_id: int
    pipeline_name: str
    status: str
    current_stage_index: int
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class PipelineDetailOut(PipelineOut):
    runs: List[PipelineRunOut] = []


class MessageOut(BaseModel):
    id: int
    message_type: str
    from_agent: str
    to_agent: Optional[str]
    content: str
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class ArtifactOut(BaseModel):
    id: int
    artifact_type: str
    file_path: str
    summary: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# ==================== Pipeline CRUD ====================

@router.post("", response_model=PipelineOut)
async def create_pipeline(req: CreatePipelineRequest, db: Session = Depends(get_db)):
    """创建 Pipeline（关联项目）"""
    try:
        pipeline = pipeline_engine.create_pipeline(db, req.project_id, req.pipeline_name)
        return pipeline
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[PipelineOut])
async def list_pipelines(db: Session = Depends(get_db)):
    """列出所有 Pipeline"""
    return db.query(Pipeline).order_by(Pipeline.created_at.desc()).all()


@router.get("/{pipeline_id}", response_model=PipelineDetailOut)
async def get_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    """获取 Pipeline 详情（含 runs 和 stages）"""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return pipeline


# ==================== 生命周期控制 ====================

@router.post("/{pipeline_id}/start")
async def start_pipeline(pipeline_id: int, req: StartPipelineRequest, db: Session = Depends(get_db)):
    """启动 Pipeline"""
    try:
        run = pipeline_engine.start_pipeline(db, pipeline_id, req.requirement)
        return {"status": "started", "run_id": run.id, "run_number": run.run_number}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pipeline_id}/pause")
async def pause_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    """暂停 Pipeline"""
    try:
        await pipeline_engine.pause(db, pipeline_id)
        return {"status": "paused"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    """恢复 Pipeline"""
    try:
        await pipeline_engine.resume(db, pipeline_id)
        return {"status": "resumed"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pipeline_id}/approve")
async def approve_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    """审批通过当前 Gate"""
    try:
        await pipeline_engine.approve(db, pipeline_id)
        return {"status": "approved"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pipeline_id}/reject")
async def reject_pipeline(pipeline_id: int, req: RejectRequest = None, db: Session = Depends(get_db)):
    """拒绝当前 Gate（打回重做）"""
    try:
        rollback_to = req.rollback_to if req else None
        await pipeline_engine.reject(db, pipeline_id, rollback_to)
        return {"status": "rejected"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pipeline_id}/instruct")
async def instruct_agent(pipeline_id: int, req: InstructRequest, db: Session = Depends(get_db)):
    """给指定 Agent 发指令"""
    try:
        await pipeline_engine.instruct(db, pipeline_id, req.agent_name, req.message)
        return {"status": "sent"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 查询 ====================

@router.get("/{pipeline_id}/messages", response_model=List[MessageOut])
async def get_messages(pipeline_id: int, limit: int = 100, db: Session = Depends(get_db)):
    """获取 Agent 协作消息"""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.run_number.desc())
        .first()
    )
    if not run:
        return []

    return (
        db.query(PipelineMessage)
        .filter(PipelineMessage.run_id == run.id)
        .order_by(PipelineMessage.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/{pipeline_id}/artifacts")
async def get_artifacts(pipeline_id: int, db: Session = Depends(get_db)):
    """获取产出物列表"""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.run_number.desc())
        .first()
    )
    if not run:
        return []

    stages = db.query(PipelineStage).filter(PipelineStage.run_id == run.id).all()
    stage_ids = [s.id for s in stages]

    artifacts = (
        db.query(StageArtifact)
        .filter(StageArtifact.stage_id.in_(stage_ids))
        .order_by(StageArtifact.created_at)
        .all()
    )

    result = []
    for a in artifacts:
        stage = next((s for s in stages if s.id == a.stage_id), None)
        result.append({
            "id": a.id,
            "stage": stage.stage_name if stage else None,
            "artifact_type": a.artifact_type,
            "file_path": a.file_path,
            "summary": a.summary,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return result


@router.get("/{pipeline_id}/stages", response_model=List[PipelineStageOut])
async def get_stages(pipeline_id: int, db: Session = Depends(get_db)):
    """获取当前 run 的阶段详情"""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.run_number.desc())
        .first()
    )
    if not run:
        return []

    return (
        db.query(PipelineStage)
        .filter(PipelineStage.run_id == run.id)
        .order_by(PipelineStage.stage_order)
        .all()
    )


# ==================== 产出物文件查看/编辑 ====================

@router.get("/{pipeline_id}/files")
async def read_file(pipeline_id: int, path: str, db: Session = Depends(get_db)):
    """读取 workspace 内的文件内容"""
    from pathlib import Path
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.run_number.desc())
        .first()
    )
    if not run or not run.workspace_path:
        raise HTTPException(status_code=404, detail="No workspace found")

    workspace = Path(run.workspace_path).resolve()
    target = (workspace / path).resolve()

    # 防止路径穿越
    if not str(target).startswith(str(workspace)):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    if target.is_dir():
        entries = []
        for p in sorted(target.iterdir()):
            stat = p.stat()
            entries.append({
                "name": p.name,
                "path": str(p.relative_to(workspace)),
                "type": "directory" if p.is_dir() else "file",
                "size": stat.st_size if p.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return {"type": "directory", "path": path, "entries": entries}

    # 文件内容（限制 1MB）
    if target.stat().st_size > 1_048_576:
        raise HTTPException(status_code=413, detail="File too large (>1MB)")

    content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "type": "file",
        "path": path,
        "content": content,
        "size": target.stat().st_size,
    }


@router.put("/{pipeline_id}/files")
async def write_file(pipeline_id: int, body: dict, db: Session = Depends(get_db)):
    """编辑 workspace 内的文件"""
    from pathlib import Path
    file_path = body.get("path")
    content = body.get("content")
    if not file_path or content is None:
        raise HTTPException(status_code=400, detail="Missing 'path' or 'content'")

    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.run_number.desc())
        .first()
    )
    if not run or not run.workspace_path:
        raise HTTPException(status_code=404, detail="No workspace found")

    workspace = Path(run.workspace_path).resolve()
    target = (workspace / file_path).resolve()

    if not str(target).startswith(str(workspace)):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return {"status": "written", "path": file_path, "size": len(content)}


# ==================== 配置 ====================

@router.get("/config/templates")
async def list_templates():
    """列出所有可用 Pipeline 模板"""
    templates = pipeline_config_manager.load()
    return [
        {
            "name": name,
            "display_name": cfg.name,
            "description": cfg.description,
            "stages": [
                {"name": s.name, "display_name": s.display_name, "agent": s.agent, "gate": s.gate}
                for s in cfg.stages
            ],
        }
        for name, cfg in templates.items()
    ]


@router.get("/engine/status")
async def engine_status():
    """引擎状态 — 并发数、运行中的 pipeline"""
    return {
        "max_concurrent": pipeline_engine._max_concurrent,
        "running_count": pipeline_engine.running_count(),
        "running_pipeline_ids": pipeline_engine.get_running_pipelines(),
    }


# ==================== WebSocket 实时推送 ====================

@router.websocket("/ws")
async def pipeline_websocket(websocket: WebSocket):
    """Pipeline 实时事件 WebSocket"""
    from routes.websocket import websocket_manager

    await websocket_manager.connect(websocket)

    # 注册事件监听 → 转发到 WebSocket
    async def forward_event(event_type: str, data: dict):
        try:
            await websocket.send_json({"type": event_type, **data})
        except Exception:
            pass

    event_bus.on(forward_event)

    try:
        while True:
            data = await websocket.receive_json()
            # 支持 join pipeline room
            msg_type = data.get("type")
            if msg_type == "subscribe":
                pipeline_id = data.get("pipeline_id")
                if pipeline_id:
                    await websocket.send_json({
                        "type": "subscribed",
                        "pipeline_id": pipeline_id,
                    })
    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket)
    except Exception:
        await websocket_manager.disconnect(websocket)
