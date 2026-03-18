#!/bin/bash
# Catown 启动脚本

echo "🐱 Catown - Multi-Agent Collaboration Platform"
echo "=============================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 is not installed. Please install Python 3.10+"
    exit 1
fi

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed. Please install Node.js 18+"
    exit 1
fi

# 选择启动模式
echo "Choose startup mode:"
echo "1) Backend only"
echo "2) Frontend only"
echo "3) Both (Full stack)"
echo ""
read -p "Enter choice (1-3): " choice

case $choice in
    1)
        echo "Starting backend server..."
        cd backend
        python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
        ;;
    2)
        echo "Starting frontend development server..."
        cd frontend
        npm run dev
        ;;
    3)
        echo "Starting full stack..."
        
        # 启动后端
        cd backend
        python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 &
        BACKEND_PID=$!
        
        echo "Backend started (PID: $BACKEND_PID)"
        echo ""
        
        # 等待后端启动
        sleep 3
        
        # 启动前端
        cd ../frontend
        npm run dev &
        FRONTEND_PID=$!
        
        echo "Frontend started (PID: $FRONTEND_PID)"
        echo ""
        echo "✅ Catown is running!"
        echo "   Frontend: http://localhost:3000"
        echo "   Backend:  http://localhost:8000"
        echo "   API Docs: http://localhost:8000/docs"
        echo ""
        echo "Press Ctrl+C to stop all servers"
        
        # 等待用户中断
        trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT
        wait
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac
