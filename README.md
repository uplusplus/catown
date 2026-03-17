# Catown - Multi-Agent Collaboration Platform

一个支持多 AI Agent 协作的平台，Agent 们可以通过聊天频道通信，协作完成复杂任务。

## 🌟 特性

- 🤖 **多 Agent 协作**：支持创建不同角色的 Agent，它们可以协作完成任务
- 💬 **聊天室系统**：每个项目自动创建独立聊天室，Agent 间实时通信
- 🧠 **记忆系统**：Agent 具备长期和短期记忆能力
- 🛠️ **工具调用**：Agent 可以调用各种工具和技能
- 🌐 **OpenAI 兼容**：支持任何 OpenAI 兼容的 LLM 接口
- 🎨 **Web 界面**：友好的 Web 界面用于监控和交互

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- pip 和 npm

### 安装步骤

#### 1. 后端安装

```bash
cd backend
pip install -r requirements.txt
```

#### 2. 前端安装

```bash
cd frontend
npm install
```

#### 3. 配置

复制环境变量文件并配置：

```bash
cd backend
cp .env.example .env
```

编辑 `.env` 文件，设置你的 LLM API 密钥：

```env
# LLM 配置
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4

# 服务器配置
HOST=0.0.0.0
PORT=8000
```

### 启动服务

#### 方式一：使用启动脚本（推荐）

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

**Windows:**
```bash
start.bat
```

选择 "Both (Full stack)" 选项同时启动前后端。

#### 方式二：手动启动

**启动后端:**
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**启动前端（新终端）:**
```bash
cd frontend
npm run dev
```

### 访问应用

- 🌐 **Web 界面**: http://localhost:3000
- 📚 **API 文档**: http://localhost:8000/docs
- 🏠 **后端首页**: http://localhost:8000

## 📁 项目结构

```
catown/
├── backend/              # 后端服务
│   ├── agents/          # Agent 核心模块
│   ├── chatrooms/       # 聊天室系统
│   ├── llm/             # LLM 集成
│   ├── models/          # 数据模型
│   ├── routes/          # API 路由
│   ├── tools/           # 工具集合
│   └── main.py         # 应用入口
├── frontend/            # 前端应用
│   ├── src/
│   │   ├── components/  # React 组件
│   │   ├── pages/       # 页面
│   │   └── hooks/       # 自定义 Hooks
│   └── public/
├── data/               # 数据存储
└── logs/              # 日志目录
```

## 🎯 使用示例

### 创建项目

通过 Web 界面或 API 创建新项目：

```bash
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "我的第一个项目",
    "description": "测试项目",
    "agents": ["assistant", "coder", "reviewer"]
  }'
```

### 与 Agent 交互

在 Web 界面中选择项目，发送消息到聊天室，Agent 们会自动协作回复。

## 🤖 内置 Agent 角色

- **Assistant**: 通用助手，处理日常任务
- **Coder**: 代码专家，编写和审查代码
- **Reviewer**: 审核专家，提供反馈和建议
- **Researcher**: 研究专家，信息收集和分析

## 📝 API 文档

启动服务后访问 http://localhost:8000/docs 查看完整的 API 文档

### 主要 API 端点

#### Agents
- `GET /api/agents` - 获取所有可用 Agent
- `GET /api/agents/{id}` - 获取 Agent 详情
- `GET /api/agents/{id}/memory` - 获取 Agent 记忆

#### Projects
- `GET /api/projects` - 获取所有项目
- `POST /api/projects` - 创建新项目
- `GET /api/projects/{id}` - 获取项目详情
- `DELETE /api/projects/{id}` - 删除项目

#### Chat
- `GET /api/chatrooms/{id}/messages` - 获取聊天消息
- `POST /api/chatrooms/{id}/messages` - 发送消息

#### Status
- `GET /api/status` - 获取系统状态
- `GET /api/health` - 健康检查

#### WebSocket
- `WS /ws` - WebSocket 实时通信

## 🔧 配置自定义 Agent

在 `backend/agents/registry.py` 中添加新的 Agent 配置：

```python
AgentConfig(
    name="my_agent",
    role="我的专家角色",
    system_prompt="""You are a specialized agent...
    
    Your capabilities:
    - Capability 1
    - Capability 2
    
    Guidelines:
    - Always be helpful
    - Provide clear explanations
    """,
    tools=["web_search", "custom_tool"]
)
```

然后重启后端服务即可。

## 🧪 运行测试

```bash
cd backend
pip install -r requirements-test.txt
pytest tests/ -v
```

## 📖 示例代码

查看 `backend/examples/demo.py` 了解如何使用 API：

```bash
cd backend/examples
python demo.py
```

## 🎯 验收要求完成情况

✅ **1. Web 页面用于同 Agent 交互**
   - Dashboard 页面：项目管理、Agent 列表、项目列表
   - Chat 页面：实时聊天界面
   - Agents 页面：Agent 详细信息和记忆查看
   - Status 页面：系统状态监控

✅ **2. 使用与 OpenAI 兼容的 LLM**
   - 支持任何 OpenAI 兼容的 API
   - 可配置的 base_url 和 model
   - 统一的 LLM 客户端封装

✅ **3. Agent 具备调用 tool/skills 能力**
   - 工具注册机制
   - 工具调用和结果处理
   - 内置工具：web_search, execute_code, retrieve_memory

✅ **4. Agent 具备记忆能力**
   - 短期记忆（最近对话）
   - 长期记忆（重要信息）
   - 程序性记忆（技能和经验）
   - 记忆重要性评分系统

## 🛣️ 路线图

- [ ] Agent 间自动协作机制
- [ ] 更多内置工具
- [ ] Agent 学习和适应能力
- [ ] 分布式部署支持
- [ ] 插件系统
- [ ] 可视化工作流编排

## 🤝 贡献

欢迎贡献代码！请遵循以下步骤：

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

## 📄 License

MIT License

## 👥 团队

这个项目由 Catown 团队开发。感谢所有贡献者！
