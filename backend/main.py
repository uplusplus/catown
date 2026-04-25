# -*- coding: utf-8 -*-
"""
Catown - Multi-Agent Collaboration Platform
后端主入口
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import logging
from logging.handlers import RotatingFileHandler
import time
import os
from pathlib import Path
from functools import lru_cache
import re
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict

# 加载 .env 文件（仅基础设施配置：HOST, PORT, DATABASE_URL, LOG_LEVEL）
# LLM 配置已迁移至 agents.json
from dotenv import load_dotenv


def _default_catown_home() -> Path:
    configured = os.getenv("CATOWN_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".catown").resolve()


load_dotenv(_default_catown_home() / ".env")

from config import settings
from monitoring import monitor_network_buffer

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
FRONTEND_DIST_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
DOCS_STATIC_DIR = BACKEND_DIR / "static" / "docs"

# 结构化日志配置
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    logs_dir = settings.STATE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    app_file_handler = RotatingFileHandler(
        logs_dir / "catown.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    app_file_handler.setLevel(level)
    app_file_handler.setFormatter(formatter)
    root_logger.addHandler(app_file_handler)

    error_file_handler = RotatingFileHandler(
        logs_dir / "catown-error.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    root_logger.addHandler(error_file_handler)

    for logger_name in ("uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        if app_file_handler not in logger.handlers:
            logger.addHandler(app_file_handler)
        if logger_name == "uvicorn.error" and error_file_handler not in logger.handlers:
            logger.addHandler(error_file_handler)


_configure_logging()
logger = logging.getLogger("catown")
logger.info(
    "[Paths] CATOWN_HOME=%s CONFIG_DIR=%s STATE_DIR=%s PROJECTS_ROOT=%s WORKSPACES_DIR=%s",
    settings.CATOWN_HOME,
    settings.CONFIG_DIR,
    settings.STATE_DIR,
    settings.PROJECTS_ROOT,
    settings.WORKSPACES_DIR,
)

# 导入路由
from routes.api import router as api_router
from routes.pipeline import router as pipeline_router
from routes.audit import router as audit_router
from routes.monitor import router as monitor_router
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
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

if FRONTEND_DIST_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST_ASSETS_DIR), name="frontend-assets")
if DOCS_STATIC_DIR.exists():
    app.mount("/_docs_static", StaticFiles(directory=DOCS_STATIC_DIR), name="docs-assets")

# CORS 配置（白名单，不再允许所有来源）
allowed_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001")
ALLOWED_ORIGINS = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]
logger.info(f"[Config] CORS allowed origins: {ALLOWED_ORIGINS}")
ALLOWED_HEADERS = ["Content-Type", "Authorization", "X-Catown-Client"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=ALLOWED_HEADERS,
)

# 速率限制
app.add_middleware(RateLimitMiddleware)
logger.info(f"[Config] Rate limiter: {rate_limiter.max_requests} req / {rate_limiter.window_seconds}s per IP")


# ==================== 请求日志 & 错误追踪中间件 ====================

import traceback as _traceback
import re as _re

_CLIENT_SOURCE_RE = _re.compile(r"^[a-z0-9_-]{1,32}$")
_UI_VERSION_RE = _re.compile(r"^[a-z0-9._-]{1,32}$")
_UI_VERSION_FILE = FRONTEND_DIR / "src" / "uiVersion.ts"
_UI_VERSION_VALUE_RE = re.compile(r'UI_VERSION\s*=\s*"([^"]+)"')
_HTML_ASSET_RE = re.compile(r"/assets/([^\"']+)")

_NO_STORE_PATHS = ("/", "/monitor", "/docs", "/redoc")

def _request_client_source(request: Request) -> str:
    explicit_source = request.headers.get("x-catown-client", "").strip().lower()
    if _CLIENT_SOURCE_RE.fullmatch(explicit_source):
        return explicit_source

    referer = request.headers.get("referer", "").lower()
    if "/monitor" in referer:
        return "monitor"
    if referer:
        return "home"
    return "unknown"

def _request_ui_version(request: Request) -> str:
    ui_version = request.headers.get("x-catown-ui-version", "").strip().lower()
    if _UI_VERSION_RE.fullmatch(ui_version):
        return ui_version
    return "unknown"

def _frontend_page_from_request(request: Request) -> str:
    path = request.url.path
    if path.startswith("/monitor"):
        return "monitor"
    if _request_client_source(request) == "monitor":
        return "monitor"
    return "home"

def _file_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0

@lru_cache(maxsize=8)
def _cached_frontend_meta(page: str, ui_version_mtime_ns: int, html_mtime_ns: int) -> dict[str, str]:
    ui_version = "unknown"
    try:
        raw = _UI_VERSION_FILE.read_text(encoding="utf-8")
        match = _UI_VERSION_VALUE_RE.search(raw)
        if match:
            ui_version = match.group(1)
    except Exception:
        pass

    html_name = "monitor.html" if page == "monitor" else "index.html"
    html_path = FRONTEND_DIST_DIR / html_name
    build_assets: list[str] = []
    try:
        html = html_path.read_text(encoding="utf-8")
        build_assets = sorted(set(_HTML_ASSET_RE.findall(html)))
    except Exception:
        build_assets = []

    build_id = "|".join(build_assets) or f"{html_name}:{html_mtime_ns}"
    return {
        "page": page,
        "ui_version": ui_version,
        "build_id": build_id,
    }

def _frontend_meta(page: str) -> dict[str, str]:
    html_name = "monitor.html" if page == "monitor" else "index.html"
    html_path = FRONTEND_DIST_DIR / html_name
    return _cached_frontend_meta(
        page,
        _file_mtime_ns(_UI_VERSION_FILE),
        _file_mtime_ns(html_path),
    )

def _should_disable_cache(request: Request) -> bool:
    if request.method.upper() != "GET":
        return False
    path = request.url.path
    if path.startswith("/api/"):
        return True
    return path in _NO_STORE_PATHS


def _response_content_length(response) -> int:
    header_value = response.headers.get("content-length", "").strip()
    try:
        return max(int(header_value), 0)
    except (TypeError, ValueError):
        return 0


def _safe_text(value: bytes | str | None, limit: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    return text if limit is None else text[:limit]


def _sanitize_headers(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in dict(headers).items():
        normalized = str(key).lower()
        if normalized in {"authorization", "cookie", "set-cookie", "x-openai-api-key"}:
            continue
        result[str(key)] = str(value)
    return result


def _record_frontend_backend_event(
    request: Request,
    *,
    status_code: int,
    duration_ms: float,
    response_bytes: int,
    request_body: bytes | str | None = None,
    response_body: bytes | str | None = None,
    response_headers: dict[str, str] | None = None,
    error: str = "",
) -> None:
    if request.url.path == "/api/monitor/network/ingest":
        return

    request_bytes = 0
    content_length = request.headers.get("content-length", "").strip()
    try:
        request_bytes = max(int(content_length), 0)
    except (TypeError, ValueError):
        request_bytes = 0

    protocol = request.scope.get("scheme", "http").upper()
    http_version = str(request.scope.get("http_version") or "").strip()
    if http_version:
        protocol = f"{protocol}/{http_version}"

    client_source = _request_client_source(request)
    monitor_network_buffer.append(
        {
            "category": "frontend_backend",
            "source": "backend",
            "protocol": protocol,
            "from_entity": f"Frontend ({client_source})",
            "to_entity": "Backend API",
            "request_direction": f"Frontend ({client_source}) -> Backend API",
            "response_direction": f"Backend API -> Frontend ({client_source})",
            "method": request.method,
            "url": str(request.url),
            "host": request.url.hostname or "",
            "path": request.url.path,
            "status_code": status_code,
            "success": status_code < 400 and not error,
            "request_bytes": request_bytes,
            "response_bytes": response_bytes,
            "duration_ms": int(duration_ms),
            "content_type": request.headers.get("content-type", ""),
            "preview": f"{request.method} {request.url.path}",
            "error": error,
            "client_source": client_source,
            "raw_request": _safe_text(request_body),
            "raw_response": _safe_text(response_body),
            "request_headers": _sanitize_headers(request.headers),
            "response_headers": response_headers or {},
            "metadata": {
                "query": request.url.query,
                "ui_version": _request_ui_version(request),
                "http_version": request.scope.get("http_version"),
            },
        }
    )

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录请求耗时和错误"""

    async def dispatch(self, request: Request, call_next):
        import time as _time
        start = _time.time()
        request_body = await request.body()
        path = (
            f"source={_request_client_source(request)} "
            f"ui={_request_ui_version(request)} "
            f"{request.url.path}"
        )
        method = request.method

        try:
            response = await call_next(request)
            frontend_meta = _frontend_meta(_frontend_page_from_request(request))
            response.headers["X-Catown-Server-UI-Version"] = frontend_meta["ui_version"]
            response.headers["X-Catown-Server-Build-Id"] = frontend_meta["build_id"]
            if _should_disable_cache(request):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            duration_ms = (_time.time() - start) * 1000
            logger.info(f"[Access] {method} {path} -> {response.status_code} ({duration_ms:.0f}ms)")

            if response.status_code >= 400:
                logger.warning(f"[HTTP] {method} {path} → {response.status_code} ({duration_ms:.0f}ms)")
            elif duration_ms > 2000:
                logger.info(f"[HTTP] {method} {path} → {response.status_code} ({duration_ms:.0f}ms) SLOW")
            else:
                logger.debug(f"[HTTP] {method} {path} → {response.status_code} ({duration_ms:.0f}ms)")

            _record_frontend_backend_event(
                request,
                status_code=response.status_code,
                duration_ms=duration_ms,
                response_bytes=_response_content_length(response),
                request_body=request_body,
                response_body=getattr(response, "body", b""),
                response_headers=_sanitize_headers(response.headers),
            )
            return response

        except Exception as e:
            duration_ms = (_time.time() - start) * 1000
            logger.error(
                f"[HTTP] {method} {path} FAILED ({duration_ms:.0f}ms)\n"
                f"  Error: {e}\n"
                f"{_traceback.format_exc()}"
            )
            _record_frontend_backend_event(
                request,
                status_code=500,
                duration_ms=duration_ms,
                response_bytes=0,
                request_body=request_body,
                response_body="",
                response_headers={},
                error=str(e),
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

# 将 Pipeline 事件总线转发到通用 WebSocket（聊天窗口可实时接收）
from pipeline.engine import event_bus

async def _forward_pipeline_events_to_ws(event_type: str, data: dict):
    """将 engine 事件转发到所有通用 /ws 连接"""
    try:
        msg = {"type": f"pipeline_{event_type}", **data}
        await websocket_manager.broadcast(msg)
    except Exception:
        pass

event_bus.on(_forward_pipeline_events_to_ws)
logger.info("[Events] Pipeline event bus connected to general WebSocket")

@app.on_event("startup")
async def _start_file_watcher():
    loop = _asyncio.get_event_loop()
    file_watcher.start(loop)
    try:
        from routes.api import recover_interrupted_task_runs

        recovery_summary = await recover_interrupted_task_runs(limit=20)
        if recovery_summary["detected"]:
            logger.info(
                "[Recovery] Task-run recovery scanned %s interrupted run(s): %s recovered / %s skipped / %s failed",
                recovery_summary["detected"],
                recovery_summary["recovered"],
                recovery_summary.get("skipped", 0),
                recovery_summary["failed"],
            )
    except Exception as exc:
        logger.warning(f"[Recovery] Startup task-run recovery failed: {exc}")

@app.on_event("shutdown")
async def _stop_file_watcher():
    file_watcher.stop()

# 包含 API 路由
app.include_router(api_router, prefix="/api")
app.include_router(pipeline_router)
app.include_router(audit_router)
app.include_router(monitor_router)

# WebSocket 路由
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.connect(websocket)
    try:
        await websocket_manager.receive(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        await websocket_manager.disconnect(websocket)

# 健康检查（顶层路径）
@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/frontend-meta", include_in_schema=False)
async def frontend_meta(request: Request):
    return _frontend_meta(_frontend_page_from_request(request))


@app.get("/docs", include_in_schema=False)
async def swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/_docs_static/swagger-ui-bundle.js",
        swagger_css_url="/_docs_static/swagger-ui.css",
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_ui():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - ReDoc",
        redoc_js_url="/_docs_static/redoc.standalone.js",
    )


@app.get("/monitor", response_class=HTMLResponse, include_in_schema=False)
@app.get("/monitor/", response_class=HTMLResponse, include_in_schema=False)
async def monitor_root():
    """Return the standalone monitor frontend."""
    try:
        if FRONTEND_DIST_DIR.exists():
            candidate = FRONTEND_DIST_DIR / "monitor.html"
            if candidate.exists():
                return HTMLResponse(content=candidate.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"Monitor frontend not found: {e}")

    return HTMLResponse(
        content="""
        <html>
        <head><title>Catown Monitor</title></head>
        <body style="font-family: Arial, sans-serif; max-width: 720px; margin: 48px auto; padding: 24px;">
            <h1>Catown Monitor</h1>
            <p>The standalone monitor build is not available yet.</p>
            <p>Run the frontend build and reopen <code>/monitor</code>.</p>
            <p><a href="/">Back to Catown</a></p>
        </body>
        </html>
        """
    )

# 根路径
@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端首页"""
    try:
        if FRONTEND_DIST_DIR.exists():
            candidate = FRONTEND_DIST_DIR / "index.html"
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
        <p>Frontend build not found. Keep using <a href="http://localhost:8000">http://localhost:8000</a> after running a frontend build or starting the app with <code>./run.sh</code>.</p>
        <p>See <a href="/docs">/docs</a> for API documentation</p>
    </body>
    </html>
    """)

if __name__ == "__main__":
    import uvicorn
    logger.info(f"[Server] Starting on {settings.HOST}:{settings.PORT}")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
