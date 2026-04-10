#!/bin/bash
# Catown 交互式启动脚本
# q = 停止, r = 重载（重启 uvicorn --reload 进程）

set -e

DIR="$(cd "$(dirname "$0")/backend" && pwd)"
PID=""

cleanup() {
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "⏹  停止服务 (PID $PID)..."
        kill "$PID" 2>/dev/null
        wait "$PID" 2>/dev/null || true
    fi
    echo "✅ 已退出。"
    exit 0
}

start_server() {
    echo "🚀 启动 Catown..."
    echo "   Frontend:  http://localhost:8000"
    echo "   API Docs:  http://localhost:8000/docs"
    echo ""
    cd "$DIR"
    python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 &
    PID=$!
    echo "   PID: $PID"
    echo ""
}

# 依赖检查
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] 找不到 python3，请先安装 Python 3.10+"
    exit 1
fi

if ! python3 -c "import fastapi" &> /dev/null; then
    echo "📦 安装依赖..."
    cd "$DIR" && pip3 install -r requirements.txt
fi

# .env
if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    echo "✅ 已创建 backend/.env — 请编辑填入 LLM_API_KEY"
fi

trap cleanup EXIT INT TERM

start_server

echo "──────────────────────────────────────"
echo "  输入 q + 回车 = 停止"
echo "  输入 r + 回车 = 重载（重启）"
echo "  Ctrl+C = 停止并退出"
echo "──────────────────────────────────────"
echo ""

while true; do
    read -r cmd
    case "$cmd" in
        q|Q)
            cleanup
            ;;
        r|R)
            echo "🔄 重载中..."
            if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
                kill "$PID" 2>/dev/null
                wait "$PID" 2>/dev/null || true
            fi
            start_server
            echo "✅ 重载完成。"
            echo ""
            ;;
        "")
            ;;
        *)
            echo "❓ 未知命令: $cmd (输入 q 停止, r 重载)"
            ;;
    esac
done
