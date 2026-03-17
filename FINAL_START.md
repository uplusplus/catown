# 🎯 Catown 最终启动指南

## ✅ 后端已准备就绪

所有测试通过：
- ✅ 模块导入成功
- ✅ API 路由正确（14个端点）
- ✅ 配置文件有效（4个 Agent）
- ✅ 数据库初始化成功
- ✅ `/api/config` 端点已添加

## 🚀 启动后端服务

### Windows 用户

**方法 1: 使用启动脚本（推荐）**
```bash
cd catown
run-backend.bat
```

**方法 2: 手动启动**
```bash
cd catown/backend
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Linux/Mac 用户

**方法 1: 使用启动脚本（推荐）**
```bash
cd catown
chmod +x run-backend.sh
./run-backend.sh
```

**方法 2: 手动启动**
```bash
cd catown/backend
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## ✅ 验证启动成功

启动成功后，你会看到：

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
✅ Database initialized successfully
✅ Registered 4 built-in agents
```

## 🧪 测试 API 端点

### 1. 在浏览器中测试

访问以下 URL：

- **API 文档**: http://localhost:8000/docs
- **配置信息**: http://localhost:8000/api/config
- **系统状态**: http://localhost:8000/api/status
- **Agent 列表**: http://localhost:8000/api/agents

### 2. 使用 curl 测试

```bash
# 测试配置端点（解决前端 404 错误）
curl http://localhost:8000/api/config

# 测试状态端点
curl http://localhost:8000/api/status

# 测试 Agent 列表
curl http://localhost:8000/api/agents

# 创建测试项目
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Project","agent_names":["assistant"]}'
```

## 🌐 前端配置

前端现在应该能正常工作了，因为 `/api/config` 端点已经可用。

### 启动前端（可选）

```bash
cd catown/frontend
npm install  # 首次运行
npm run dev
```

访问: http://localhost:3000

## 📋 API 端点列表

### 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/config` | GET | 获取配置信息 ⭐ |
| `/api/status` | GET | 系统状态 |
| `/api/health` | GET | 健康检查 |

### Agent 管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agents` | GET | Agent 列表 |
| `/api/agents/{id}` | GET | Agent 详情 |
| `/api/agents/{id}/memory` | GET | Agent 记忆 |

### 项目管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/projects` | GET | 项目列表 |
| `/api/projects` | POST | 创建项目 |
| `/api/projects/{id}` | GET | 项目详情 |
| `/api/projects/{id}` | DELETE | 删除项目 |

### 聊天功能

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chatrooms/{id}/messages` | GET | 获取消息 |
| `/api/chatrooms/{id}/messages` | POST | 发送消息 |

## 🔧 配置 LLM

编辑 `backend/.env` 文件：

```env
# 使用环境变量配置
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=http://localhost:3008/opencode/assistant-agent-s/opencode/v1
LLM_MODEL=GLM-V5-128K
```

或使用 `backend/configs/agents.json` 配置文件（推荐，支持多模型）

## 🐛 故障排除

### 问题 1: 端口 8000 被占用

**解决方案**: 使用其他端口
```bash
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

### 问题 2: 前端仍然显示 404

**检查步骤**:
1. 确认后端已启动
2. 访问 http://localhost:8000/api/config 验证
3. 检查前端代理配置（vite.config.ts）

### 问题 3: Agent 无法响应

**检查步骤**:
1. 确认 LLM_API_KEY 已设置
2. 确认 LLM_BASE_URL 正确
3. 检查网络连接

## 📚 完整文档

- `START_HERE.md` - 快速开始
- `PROJECT_STATUS.md` - 项目状态
- `AGENT_CONFIG.md` - 配置说明
- `README.md` - 完整文档

## 🎉 开始使用

**现在就启动后端服务：**

```bash
# Windows
run-backend.bat

# Linux/Mac
./run-backend.sh
```

**然后在浏览器中访问：**
- API 文档: http://localhost:8000/docs
- Web 界面: http://localhost:3000（需启动前端）

---

**🚀 一切准备就绪，开始你的 Catown 之旅吧！**
