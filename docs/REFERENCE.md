# 🐱 Catown - 快速参考卡

## 📌 一句话介绍
Catown 是一个多 Agent 协作平台，支持多个 AI Agent 通过聊天频道通信，协作完成复杂任务。

## 🚀 30 秒启动

```bash
# 1. 安装依赖
cd backend && pip install -r requirements.txt
cd ../frontend && npm install

# 2. 配置 LLM
cd backend && cp .env.example .env
# 编辑 .env，设置 LLM_API_KEY

# 3. 启动服务
cd .. && ./start.sh  # 或 start.bat (Windows)
# 选择选项 3

# 4. 访问
# Web: http://localhost:3000
# API: http://localhost:8000/docs
```

## 📁 核心文件

| 文件 | 说明 |
|------|------|
| `backend/main.py` | 后端入口 |
| `backend/agents/core.py` | Agent 核心逻辑 |
| `backend/routes/api.py` | API 端点 |
| `frontend/src/App.tsx` | 前端入口 |
| `frontend/src/pages/` | 页面组件 |
| `README.md` | 完整文档 |
| `QUICKSTART.md` | 快速开始 |

## 🎯 核心功能

### 1. 创建项目
```bash
POST /api/projects
{
  "name": "My Project",
  "description": "Test",
  "agent_names": ["assistant", "coder"]
}
```

### 2. 发送消息
```bash
POST /api/chatrooms/{id}/messages
{
  "content": "Hello!"
}
```

### 3. 查看状态
```bash
GET /api/status
```

## 🤖 内置 Agent

| Agent | 角色 | 工具 |
|-------|------|------|
| assistant | 通用助手 | web_search, retrieve_memory |
| coder | 代码专家 | web_search, execute_code, retrieve_memory |
| reviewer | 审核专家 | web_search, retrieve_memory |
| researcher | 研究专家 | web_search, retrieve_memory |

## 🧠 记忆系统

- **短期记忆**: 最近对话（最多 20 条）
- **长期记忆**: 重要信息（持久化）
- **程序性记忆**: 技能和经验

## 🛠️ 内置工具

1. `web_search` - 网络搜索
2. `execute_code` - 代码执行（仅 coder）
3. `retrieve_memory` - 记忆检索

## 🌐 API 端点速查

### Agents
- `GET /api/agents` - 列表
- `GET /api/agents/{id}` - 详情
- `GET /api/agents/{id}/memory` - 记忆

### Projects
- `GET /api/projects` - 列表
- `POST /api/projects` - 创建
- `GET /api/projects/{id}` - 详情
- `DELETE /api/projects/{id}` - 删除

### Chat
- `GET /api/chatrooms/{id}/messages` - 获取消息
- `POST /api/chatrooms/{id}/messages` - 发送消息

### Status
- `GET /api/status` - 系统状态
- `GET /api/health` - 健康检查

## 🎨 前端页面

| 页面 | 路由 | 功能 |
|------|------|------|
| Dashboard | `/` | 项目管理 |
| ChatRoom | `/chat/:id` | 实时聊天 |
| ProjectDetail | `/projects/:id` | 项目详情 |
| Agents | `/agents` | Agent 管理 |
| Status | `/status` | 系统状态 |

## ⚙️ 配置项

```env
# LLM 配置
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4

# 服务器配置
HOST=0.0.0.0
PORT=8000

# 数据库
DATABASE_URL=data/catown.db
```

## 🔧 常用命令

```bash
# 启动后端
cd backend && uvicorn main:app --reload

# 启动前端
cd frontend && npm run dev

# 运行测试
cd backend && pytest tests/ -v

# 运行示例
cd backend/examples && python demo.py
```

## 📊 技术栈

**后端**: Python, FastAPI, SQLAlchemy, Pydantic, WebSocket
**前端**: React, TypeScript, TailwindCSS, Vite
**数据库**: SQLite (可升级到 PostgreSQL)
**LLM**: OpenAI 兼容接口

## 🎯 验收要求 ✅

1. ✅ Web 页面用于同 Agent 交互
2. ✅ 可使用与 OpenAI 兼容的 LLM
3. ✅ Agent 具备调用 tool/skills 能力
4. ✅ Agent 具备记忆能力

## 📚 文档链接

- 完整说明：README.md
- 快速开始：QUICKSTART.md
- 项目结构：PROJECT_STRUCTURE.md
- 完成总结：PROJECT_SUMMARY.md
- API 文档：http://localhost:8000/docs

## 💡 提示

- 修改代码后后端自动重载（开发模式）
- 前端支持热更新
- 数据库文件：`backend/data/catown.db`
- 查看日志：`backend/logs/`

## 🆘 遇到问题？

1. 检查后端是否运行：http://localhost:8000
2. 查看浏览器控制台错误
3. 检查 .env 配置是否正确
4. 查看后端日志
5. 运行测试：`pytest tests/ -v`

---

**🎉 开始使用 Catown 吧！**
