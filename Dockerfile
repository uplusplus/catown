# Catown - Multi-Agent Collaboration Platform
FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码到 backend/ 子目录（保持 import 路径一致）
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# 创建数据和日志目录
RUN mkdir -p backend/data backend/logs

# 环境变量
ENV HOST=0.0.0.0
ENV PORT=8000
ENV DATABASE_URL=data/catown.db
ENV LOG_LEVEL=INFO
ENV CORS_ORIGINS=*

# 工作目录切到 backend（匹配代码中的相对 import）
WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
