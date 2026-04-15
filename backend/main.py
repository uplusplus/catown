# -*- coding: utf-8 -*-
"""
Catown - Multi-Agent Collaboration Platform
后端主入口
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import logging
import time
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict

# 加载 .env 文件（仅基础设施配置：HOST, PORT, DATABASE_URL, LOG_LEVEL）
# LLM 配置已迁移至 agents.json
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from config import settings

# 结构化日志配置
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("catown")

# 导入路由
from routes.api import router as api_router
from routes.audit import router as audit_router
from routes.projects_v2 import router as projects_v2_router
from routes.assets_v2 import router as assets_v2_router
from routes.decisions_v2 import router as decisions_v2_router
from routes.stage_runs_v2 import router as stage_runs_v2_router
from routes.dashboard_v2 import router as dashboard_v2_router
from routes.websocket import websocket_manager

# 导入初始化模块
from models.database import init_database
from agents.registry import register_builtin_agents

# ==================== 速率限制中间件 ====================

class RateLimiter:
    """简单基于 IP 的速率限制"""
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
    
    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        # 清理过期记录
        self._requests[client_ip] = [t for t in self._requests[client_ip] if t > window_start]
        if len(self._requests[client_ip]) >= self.max_requests:
            return False
        self._requests[client_ip].append(now)
        return True

_rate_limit_max = int(os.getenv("RATE_LIMIT_MAX", "0"))
rate_limiter = RateLimiter(
    max_requests=_rate_limit_max if _rate_limit_max > 0 else 999_999,
    window_seconds=int(os.getenv("RATE_LIMIT_WINDOW", "60"))
)

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "testclient"
        if not rate_limiter.is_allowed(client_ip):
            from fastapi.responses import JSONResponse
            logger.warning(f"[RateLimit] IP {client_ip} exceeded rate limit")
            return JSONResponse(status_code=429, content={"error": "Rate limit exceeded. Try again later."})
        return await call_next(request)


# 创建 FastAPI 应用
app = FastAPI(
    title="Catown API",
    description="Multi-Agent Collaboration Platform",
    version="1.0.0"
)

# CORS 配置（白名单，不再允许所有来源）
allowed_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001")
ALLOWED_ORIGINS = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]
logger.info(f"[Config] CORS allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# 速率限制
app.add_middleware(RateLimitMiddleware)
logger.info(f"[Config] Rate limiter: {rate_limiter.max_requests} req / {rate_limiter.window_seconds}s per IP")


# ==================== 请求日志 & 错误追踪中间件 ====================

import traceback as _traceback

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录请求耗时和错误"""

    async def dispatch(self, request: Request, call_next):
        import time as _time
        start = _time.time()
        path = request.url.path
        method = request.method

        try:
            response = await call_next(request)
            duration_ms = (_time.time() - start) * 1000

            if response.status_code >= 400:
                logger.warning(f"[HTTP] {method} {path} → {response.status_code} ({duration_ms:.0f}ms)")
            elif duration_ms > 2000:
                logger.info(f"[HTTP] {method} {path} → {response.status_code} ({duration_ms:.0f}ms) SLOW")
            else:
                logger.debug(f"[HTTP] {method} {path} → {response.status_code} ({duration_ms:.0f}ms)")

            return response

        except Exception as e:
            duration_ms = (_time.time() - start) * 1000
            logger.error(
                f"[HTTP] {method} {path} FAILED ({duration_ms:.0f}ms)\n"
                f"  Error: {e}\n"
                f"{_traceback.format_exc()}"
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error", "detail": str(e)}
            )


app.add_middleware(RequestLoggingMiddleware)

# 初始化数据库
logger.info("[DB] Initializing database...")
init_database()

# 注册内置 Agent
logger.info(f"[Agent] Registering built-in agents")
register_builtin_agents()

# 初始化协作系统
logger.info("[Collab] Initializing collaboration system...")
from agents.collaboration import collaboration_coordinator
from tools import init_collaboration_tools
init_collaboration_tools(collaboration_coordinator)
logger.info("[Collab] Collaboration tools connected to coordinator")

# 启动前端文件监听（开发热重载）
import asyncio as _asyncio
from routes.file_watcher import file_watcher

@app.on_event("startup")
async def _start_file_watcher():
    loop = _asyncio.get_event_loop()
    file_watcher.start(loop)

@app.on_event("shutdown")
async def _stop_file_watcher():
    file_watcher.stop()

# 包含 API 路由
app.include_router(api_router, prefix="/api")
app.include_router(projects_v2_router)
app.include_router(assets_v2_router)
app.include_router(decisions_v2_router)
app.include_router(stage_runs_v2_router)
app.include_router(dashboard_v2_router)
app.include_router(audit_router)

# WebSocket 路由
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.connect(websocket)
    try:
        await websocket_manager.receive(websocket)
    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket)

# 健康检查（顶层路径）
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# 根路径
@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端首页"""
    try:
        # 优先找 dist 版本，回退到源文件
        for candidate in [
            Path("frontend/dist/index.html"),
            Path("../frontend/dist/index.html"),
            Path("../frontend/index.html"),
            Path("frontend/index.html"),
        ]:
            if candidate.exists():
                return HTMLResponse(content=candidate.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"Frontend not found: {e}")
    
    # 返回简单的测试页面
    return HTMLResponse(content="""
    <html>
    <head>
        <title>Catown - Multi-Agent Platform</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
            .status { padding: 10px; background: #e7f3ff; border-left: 4px solid #2196F3; margin: 10px 0; }
            a { color: #2196F3; }
        </style>
    </head>
    <body>
        <h1>Catown - Multi-Agent Platform</h1>
        <p class="status">Backend service is running</p>
        <p>See <a href="/docs">/docs</a> for API documentation</p>
    </body>
    </html>
    """)

if __name__ == "__main__":
    import uvicorn
    logger.info(f"[Server] Starting on {settings.HOST}:{settings.PORT}")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
