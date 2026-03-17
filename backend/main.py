# -*- coding: utf-8 -*-
"""
Catown - Multi-Agent Collaboration Platform
后端主入口
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import os
from pathlib import Path

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# 导入路由
from routes.api import router as api_router
from routes.websocket import websocket_manager

# 导入初始化模块
from models.database import init_database
from agents.registry import register_builtin_agents

# 创建 FastAPI 应用
app = FastAPI(
    title="Catown API",
    description="Multi-Agent Collaboration Platform",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据库
init_database()

# 注册内置 Agent
register_builtin_agents()

# 包含 API 路由
app.include_router(api_router, prefix="/api")

# WebSocket 路由
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.connect(websocket)
    try:
        await websocket_manager.receive(websocket)
    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket)

# 根路径
@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端首页"""
    try:
        frontend_index = Path("frontend/dist/index.html")
        if frontend_index.exists():
            return HTMLResponse(content=frontend_index.read_text())
    except:
        pass
    
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
        <h1>🐱 Catown</h1>
        <p class="status">✅ 后端服务运行正常</p>
        <p>访问 <a href="/docs">/docs</a> 查看 API 文档</p>
        <p>访问 <a href="http://localhost:3000">http://localhost:3000</a> 使用 Web 界面</p>
    </body>
    </html>
    """)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
