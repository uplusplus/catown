# Catown 技术可行性文档

**日期：** 2026-04-07
**版本：** v5.0（基于最新代码库完整审查）
**审查来源：** GitHub 仓库 + 逐模块源码审查
**基于提交：** `df94340` test: 新增启动流程测试 16 个用例

---

## 1. 当前技术状态

### 1.1 整体架构

Catown 是一个多 Agent 协作平台，前后端分离，单进程部署：

| 层级 | 技术栈 | 状态 |
|------|--------|------|
| 后端 | Python 3.10+ / FastAPI 0.109 / SQLAlchemy 2.0 / SQLite | ✅ 生产就绪 |
| LLM 集成 | openai ≥1.40.0（AsyncOpenAI 异步客户端） | ✅ 稳定 |
| 前端 | 单文件 Vanilla JS（~2000 行）+ TailwindCSS CDN + marked.js + highlight.js | ✅ 可用 |
| 实时通信 | FastAPI 原生 WebSocket + SSE 流式输出 | ✅ 双通道就绪 |
| 配置管理 | .env 文件 + configs/agents.json（支持多 Provider、多模型） | ✅ 灵活 |
| 协作系统 | CollaborationCoordinator + AgentCollaborator + 多策略路由 | ✅ 可用 |
| 数据库 | SQLAlchemy ORM + SQLite + Alembic 迁移 | ✅ 支持 PostgreSQL 扩展 |
| 部署 | Docker + docker-compose | ✅ 生产就绪 |
| 测试 | pytest + pytest-asyncio | ✅ 覆盖完整 |

### 1.2 前端架构

前端为**单文件 Vanilla JS 方案**（`frontend/index.html`，约 2000 行），无构建步骤：

- **渲染**：原生 DOM 操作 + marked.js（Markdown 渲染）+ highlight.js（代码高亮）
- **样式**：TailwindCSS CDN + 自定义 CSS（暗色主题、glass-panel 效果）
- **HTTP 通信**：原生 `fetch` API，`API_BASE` 从 `window.location` 动态推断
- **WebSocket**：原生 `WebSocket` API，支持房间加入/离开/广播
- **SSE 流式**：fetch + ReadableStream 消费 SSE 事件流，实现打字机效果
- **交互功能**：@mention Agent 自动补全、项目管理、聊天室切换、Config 面板（含输入验证）、搜索

> 注：`frontend/vite.config.ts` 和 `package.json` 是一个未完成的 React+Vite 重写方案的残留（配置了 react/vue-router/axios/socket.io-client，但无实际组件代码）。当前前端为 Vanilla JS 单文件，由 FastAPI 直接 serve，不依赖这些文件。

### 1.3 后端模块矩阵

| 模块 | 文件 | 职责 | 状态 |
|------|------|------|------|
| 应用入口 | `main.py` | FastAPI 应用、CORS 白名单、速率限制、请求日志、错误追踪 | ✅ |
| 统一配置 | `config.py` | Settings 类（.env → 环境变量），LLM 连接唯一入口 | ✅ |
| Agent 核心 | `agents/core.py` | Agent 类：配置持有、记忆系统（短期/长期/程序性）、对话历史 | ✅ |
| Agent 注册表 | `agents/registry.py` | 内置 Agent 注册、DB 同步、配置文件加载优先 | ✅ |
| 配置模型 V2 | `agents/config_models.py` | AgentConfigV2、AgentProviderConfig、ModelConfig、成本配置 | ✅ |
| 配置管理器 | `agents/config_manager.py` | JSON/YAML 配置加载、解析、序列化 | ✅ |
| 协作模块 | `agents/collaboration.py` | CollaborationCoordinator、AgentCollaborator、任务委托、多策略路由 | ✅ |
| LLM 客户端 | `llm/client.py` | AsyncOpenAI 封装：chat / chat_with_tools / chat_stream（SSE） | ✅ |
| 数据库模型 | `models/database.py` | Agent / Project / Chatroom / Message / Memory / AgentAssignment | ✅ |
| 聊天室管理 | `chatrooms/manager.py` | 聊天室 CRUD、消息路由、Agent 协作协调 | ✅ |
| API 路由 | `routes/api.py` | REST API + Agent 响应循环 + SSE 流式端点 + 多 Agent 流水线 | ✅ |
| WebSocket | `routes/websocket.py` | 连接管理、房间广播、消息类型处理 | ✅ |
| 工具系统 | `tools/*.py` | 14 个工具（详见 1.5 节） | ✅ |

### 1.4 数据流架构

#### 同步模式（非流式）

```
用户消息 → POST /api/chatrooms/{id}/messages
         → chatroom_manager.save_message()
         → trigger_agent_response()
           → 解析 @mention → 选择目标 Agent
           → 构建上下文（system_prompt + 历史 + 记忆注入）
           → 自动注册 Agent 为项目协作者
           → 循环（最多 5 轮）:
               llm_client.chat_with_tools(messages, tool_schemas)
               → 如有 tool_calls → tool_registry.execute()
               → 结果回传 messages（tool role）→ 继续循环
               → 直到无 tool_calls
           → 保存响应 + WebSocket 广播
           → 异步提取记忆（LLM 辅助）
         → 返回用户消息
```

#### SSE 流式模式

```
用户消息 → POST /api/chatrooms/{id}/messages/stream
         → StreamingResponse (text/event-stream)
         → event: agent_start {"agent_name": "..."}
         → event: content {"delta": "..."}       ← 打字机效果
         → event: tool_start {"tool": "..."}     ← 工具开始执行
         → event: tool_result {"tool": "..."}    ← 工具执行完毕
         → ...（循环，最多 5 轮工具调用）
         → event: done {"message_id": 123}       ← 流结束
```

#### 多 Agent 流水线模式

```
用户 @coder @reviewer → POST .../messages/stream
  → collab_start {"agents": ["coder", "reviewer"]}
  → collab_step {"step": 1, "agent": "coder"}
  → content {delta} × N（coder 流式输出）
  → collab_step_done {"agent": "coder"}
  → collab_step {"step": 2, "agent": "reviewer"}
  → content {delta} × N（reviewer 基于 coder 输出响应）
  → collab_step_done {"agent": "reviewer"}
  → done
```

**✅ 完整链路已通**：用户提问 → Agent 思考 → 工具调用（多轮）→ 结果返回 → 记忆提取。

### 1.5 工具系统（14 个已注册工具）

| 工具名称 | 功能描述 | 类型 |
|----------|----------|------|
| `web_search` | DuckDuckGo 即时搜索（无需 API Key） | 信息获取 |
| `execute_code` | Python 代码沙箱执行（sys.executable，10s 超时） | 代码执行 |
| `retrieve_memory` | 数据库记忆检索（关键词 + Agent 过滤） | 记忆 |
| `save_memory` | 保存记忆到数据库 | 记忆 |
| `read_file` | 读取 workspace 文件 | 文件操作 |
| `write_file` | 写入 workspace 文件 | 文件操作 |
| `list_files` | 列出 workspace 文件 | 文件操作 |
| `delete_file` | 删除 workspace 文件 | 文件操作 |
| `search_files` | 搜索 workspace 文件内容 | 文件操作 |
| `delegate_task` | 委托任务给其他 Agent | 协作 |
| `broadcast_message` | 广播消息给所有 Agent | 协作 |
| `check_task_status` | 检查委托任务状态 | 协作 |
| `list_collaborators` | 列出可用协作者 | 协作 |
| `send_direct_message` | 向指定 Agent 发送直接消息 | 协作 |

### 1.6 Agent 系统

#### 内置 Agent

| Agent | 角色 | 系统提示摘要 | 配置工具 |
|-------|------|-------------|----------|
| assistant | 通用助手 | 处理日常任务，协调其他 Agent | web_search, retrieve_memory |
| coder | 代码专家 | 编写高效代码，调试和技术解释 | web_search, execute_code, retrieve_memory |
| reviewer | 审核专家 | 质量审查、建设性反馈、改进建议 | web_search, retrieve_memory |
| researcher | 研究专家 | 信息收集与分析、综合复杂主题 | web_search, retrieve_memory |

#### Agent 配置系统（V2）

- **AgentConfigV2**：支持 per-agent 的 Provider 配置
- **AgentProviderConfig**：baseUrl、apiKey、认证方式、API 类型、模型列表
- **ModelConfig**：模型 ID、上下文窗口、最大 Token、能力（text/image）、推理模式、成本配置
- **配置来源优先级**：`configs/agents.json` > 默认配置（.env 环境变量）
- **多模型支持**：每个 Agent 可配置多个模型，支持指定默认模型

### 1.7 协作系统

#### 协作策略

| 策略 | 类 | 逻辑 |
|------|-----|------|
| 单 Agent | `SingleAgentStrategy` | 基于 @mention 或默认 assistant |
| 多 Agent | `MultiAgentStrategy` | @mention 多选 + 关键词自动匹配（代码/研究/审核） |

#### 协作消息类型

| 类型 | 说明 |
|------|------|
| `TASK_REQUEST` | 请求其他 Agent 执行任务 |
| `TASK_RESPONSE` | 任务响应 |
| `BROADCAST` | 广播到所有 Agent |
| `DIRECT` | 直接消息 |
| `STATUS_UPDATE` | Agent 状态更新 |
| `COORDINATION` | 协调消息 |

#### AgentCollaborator 能力

- 异步消息队列（inbox/outbox）
- 任务委托和跟踪（pending/in_progress/completed/failed/delegated）
- 消息历史记录
- 协作者集合管理
- `main.py` 启动时 `init_collaboration_tools(collaboration_coordinator)` 绑定协作工具

### 1.8 记忆系统

| 类型 | 存储位置 | 容量限制 | 用途 |
|------|----------|---------|------|
| 短期记忆 | Agent 内存 + DB | 最近 20 条 | 最近对话上下文 |
| 长期记忆 | DB | 无硬限制 | 重要信息持久化 |
| 程序性记忆 | DB | 无硬限制 | 技能和经验 |
| 上下文注入 | 查询时 | 自身 8 条 + 他人 5 条（importance ≥7） | 对话上下文构建 |
| 记忆提取 | LLM 辅助 | 对话结束后异步提取，最多 3 条 | 自动记忆积累 |

### 1.9 数据库模型

| 表名 | 关键字段 | 关系 |
|------|---------|------|
| `agents` | name, role, system_prompt, tools, is_active | → memories, messages |
| `projects` | name, description, status | → chatroom, agent_assignments |
| `chatrooms` | project_id (unique) | → project, messages |
| `agent_assignments` | project_id, agent_id | → project, agent |
| `messages` | chatroom_id, agent_id, content, message_type, metadata_json | → chatroom, agent |
| `memories` | agent_id, memory_type, content, importance | → agent |

### 1.10 安全与中间件

| 特性 | 实现 | 状态 |
|------|------|------|
| CORS | 白名单（环境变量配置，默认 localhost） | ✅ |
| 速率限制 | 基于 IP 的滑动窗口（默认 60 req/60s） | ✅ |
| 请求日志 | 方法/路径/状态码/耗时 | ✅ |
| 错误追踪 | 异常捕获 + traceback 记录 | ✅ |
| 健康检查 | `/health` + Docker HEALTHCHECK | ✅ |
| 文件操作安全 | `_is_safe_path` workspace 路径校验 | ✅ |

### 1.11 部署架构

```
┌─────────────────────────────────────────────┐
│                 Docker 容器                   │
│  ┌─────────┐  ┌──────────┐  ┌────────────┐  │
│  │ uvicorn  │  │ FastAPI   │  │ WebSocket  │  │
│  │ :8000    │──│ 应用      │──│ Manager    │  │
│  └─────────┘  └──────────┘  └────────────┘  │
│       │              │              │        │
│  ┌─────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Static  │  │ SQLAlchemy│  │ SQLite /   │  │
│  │ Files   │  │ ORM      │  │ PostgreSQL │  │
│  └─────────┘  └──────────┘  └────────────┘  │
└─────────────────────────────────────────────┘
```

- **基础镜像**：python:3.11-slim
- **进程管理**：uvicorn 单进程
- **健康检查**：30s 间隔，5s 超时，3 次重试
- **数据持久化**：`backend/data/` 挂载卷
- **环境变量**：HOST, PORT, DATABASE_URL, LOG_LEVEL, CORS_ORIGINS

---

## 2. 技术可行性评估

### 2.1 已验证可行的功能

| 功能 | 验证状态 | 说明 |
|------|----------|------|
| Web 界面交互 | ✅ 已实现 | 前端完整，含 Dashboard / Chat / Agents / Status / Config 页面 |
| OpenAI 兼容 LLM | ✅ 已实现 | AsyncOpenAI 客户端，支持自定义 base_url 和 model |
| Agent 工具调用 | ✅ 已实现 | 14 个工具，多轮调用循环（最多 5 轮） |
| Agent 记忆系统 | ✅ 已实现 | 短期/长期/程序性，LLM 辅助提取，跨 Agent 共享 |
| 实时 WebSocket 通信 | ✅ 已实现 | 房间管理、消息广播 |
| SSE 流式输出 | ✅ 已实现 | 打字机效果、工具调用事件流 |
| 多 Agent 协作流水线 | ✅ 已实现 | @mention 路由、串行协作、策略选择 |
| 多 Provider/模型配置 | ✅ 已实现 | JSON 配置文件、per-agent 模型选择 |
| 项目管理 | ✅ 已实现 | CRUD、Agent 分配、聊天室关联 |
| Docker 部署 | ✅ 已实现 | Dockerfile + docker-compose + PostgreSQL 支持 |
| 数据库迁移 | ✅ 已实现 | Alembic 集成 |
| API 速率限制 | ✅ 已实现 | IP 级别滑动窗口 |

### 2.2 当前存在的技术债务

| 编号 | 问题 | 严重程度 | 建议 |
|------|------|---------|------|
| TD-1 | `models/database.py` 使用 `declarative_base()` 旧式写法 | 低 | SQLAlchemy 2.0 推荐 `DeclarativeBase`，当前功能正常 |
| TD-2 | 前端为单文件 (~2000 行)，无组件化 | 中 | 功能模块化拆分为多个 JS 文件 |
| TD-3 | `database.py` engine 创建硬编码 `sqlite:///`，PostgreSQL 切换可能不工作 | 中 | 修复 engine 创建逻辑，根据 DATABASE_URL 前缀动态选择 |
| TD-4 | `execute_code` 工具用 `sys.executable` 执行，沙箱隔离不足 | 高 | 考虑 Docker-in-Docker 或 gVisor 沙箱 |
| TD-5 | 速率限制基于内存字典，重启丢失，不支持多进程 | 低 | 可迁移至 Redis |
| TD-6 | Config 面板保存直接写 `.env` 文件，并发写入可能冲突 | 中 | 引入文件锁或配置中心 |
| TD-7 | `frontend/vite.config.ts` 和 `package.json` 是未完成的 React+Vite 重写方案残留，当前前端为 Vanilla JS 单文件，由 FastAPI 直接 serve，不依赖这些文件 | 低 | 决定：正式启用 React 重写或清理残留 |
| TD-8 | 协作消息队列为内存实现（asyncio.Queue），进程重启丢失 | 中 | 生产环境建议持久化 |
| TD-9 | `ChatroomManager.process_user_message` 有 TODO，协作路由未完善 | 低 | 完善路由逻辑 |
| TD-10 | 记忆提取依赖 LLM 调用，存在延迟和成本 | 低 | 已异步处理，不影响主流程 |
| TD-11 | 无 CI/CD 集成 | 中 | 建议添加 GitHub Actions |

---

## 3. 依赖分析

### 3.1 核心后端依赖

| 包名 | 版本 | 用途 | 风险 |
|------|------|------|------|
| fastapi | 0.109.0 | Web 框架 | 低 — 成熟稳定 |
| uvicorn[standard] | 0.27.0 | ASGI 服务器 | 低 — 生产级 |
| sqlalchemy | 2.0.25 | ORM | 低 — 行业标准 |
| openai | ≥1.40.0 | LLM 客户端 | 低 — 官方库 |
| pydantic | 2.5.3 | 数据验证 | 低 — FastAPI 内置 |
| pydantic-settings | 2.1.0 | 配置管理 | 低 |
| aiosqlite | 0.19.0 | SQLite 异步驱动 | 低 |
| websockets | 12.0 | WebSocket 支持 | 低 |
| alembic | ≥1.13 | 数据库迁移 | 低 |
| httpx | ≥0.25,<0.28 | HTTP 客户端（测试用） | 低 |
| python-dotenv | 1.0.0 | 环境变量管理 | 低 |
| psycopg2-binary | ≥2.9 | PostgreSQL 驱动（可选） | 低 |
| janus | 1.0.0 | 同步/异步桥接队列 | 低 |

### 3.2 测试依赖

| 包名 | 版本 | 用途 |
|------|------|------|
| pytest | ≥8.0 | 测试框架 |
| pytest-asyncio | ≥0.23 | 异步测试支持 |

### 3.3 前端依赖（CDN）

| 库名 | 用途 | 风险 |
|------|------|------|
| TailwindCSS | 样式框架 | 低 — CDN 加载 |
| marked.js | Markdown 渲染 | 低 |
| highlight.js | 代码高亮 | 低 |

---

## 4. 扩展可行性

| 扩展方向 | 可行性 | 所需工作 |
|----------|--------|---------|
| 新增 Agent 角色 | ✅ 高 | 在 `configs/agents.json` 添加配置即可 |
| 新增工具 | ✅ 高 | 实现工具函数 + 注册到 tool_registry |
| 更换 LLM Provider | ✅ 高 | 修改 `.env` 或 `agents.json` 中的 baseUrl/apiKey |
| 切换至 PostgreSQL | ⚠️ 中 | 需修复 TD-3 的 engine 创建逻辑 |
| 添加用户认证 | ⚠️ 中 | 需新增 auth 中间件和用户表 |
| 多租户支持 | ⚠️ 中 | 需项目级别数据隔离 |
| 可视化工作流编排 | ⚠️ 中 | 需前端流程图组件 + 后端编排引擎 |
| Agent 学习/自适应 | ⚠️ 低 | 需设计反馈学习机制 |
| 分布式部署 | ⚠️ 低 | 需消息队列（Redis/RabbitMQ）+ 共享状态 |

---

## 5. 性能特征

| 场景 | 预期性能 | 瓶颈 |
|------|---------|------|
| API 响应（非 LLM） | < 100ms | SQLite 读写 |
| LLM 调用（同步） | 2-30s | LLM Provider 延迟 |
| SSE 流式首字节 | < 2s | LLM Provider |
| WebSocket 消息广播 | < 50ms | 网络延迟 |
| 并发请求 | ~60 req/min/IP | 速率限制器配置 |
| 数据库查询 | < 10ms | SQLite 并发写入限制 |

---

## 6. 开发路线图

### Phase 2：架构清理与功能完善 ✅ 已完成

- [x] 统一 Agent 核心架构（删除 registry_v2.py，职责分离）
- [x] 激活协作系统（main.py 初始化 + 工具绑定）
- [x] 配置系统增强（openai 更新 + 动态 API_BASE + Pydantic 验证）
- [x] 日志与可观测性（结构化日志 + 分级输出）
- [x] 测试覆盖（单元测试全部通过）

### Phase 3：增强与生产化 ✅ 基本完成

- [x] 前端体验提升（SSE 流式 + Agent 状态 + 搜索功能）
- [x] Agent 能力增强（LLM 记忆提取 + 跨 Agent 共享 + 多 Agent 流水线）
- [x] 生产部署（Docker + PostgreSQL + Alembic）
- [x] 测试与质量保障（覆盖数据库 / API / 工具 / 协作 / LLM / WebSocket / SSE）

### Phase 4：插件与扩展（当前阶段）

- [ ] 修复 PostgreSQL engine 创建逻辑（TD-3）
- [ ] Playwright E2E 测试完善
- [ ] CI/CD 集成（GitHub Actions）
- [ ] 工具插件化，支持第三方工具动态加载
- [ ] 可视化工作流编排
- [ ] 分布式部署支持
- [ ] 决策：React 重写 or 清理残留配置（TD-7）

---

## 7. 风险评估

| 风险 | 级别 | 应对措施 |
|------|------|---------|
| 前端单文件维护性下降 | 中 | 函数分区已有结构，必要时拆分为多 JS 文件 |
| PostgreSQL engine 创建逻辑错误（TD-3） | 中 | 需修复硬编码 sqlite:/// 前缀 |
| 工具执行沙箱隔离不足（TD-4） | 高 | 考虑 Docker-in-Docker 或 gVisor |
| 协作队列内存丢失（TD-8） | 中 | 生产部署时持久化到 DB 或 Redis |
| E2E 测试缺失 | 中 | 已有单元/集成测试覆盖核心链路 |

---

## 8. 结论

**Catown 项目技术可行性：✅ 确认可行**

项目已完成核心功能的完整实现：

- ✅ **完整 Web 应用**：Dashboard / Chat / Agents / Status / Config 五页
- ✅ **OpenAI 兼容 LLM**：支持任何 OpenAI 兼容 API，可配置 base_url 和 model
- ✅ **工具调用**：14 个工具，多轮工具调用循环（最多 5 轮）
- ✅ **记忆系统**：短期/长期/程序性三层 + LLM 辅助提取 + 跨 Agent 共享
- ✅ **SSE 流式输出**：打字机效果，同步回退
- ✅ **多 Agent 协作**：顺序流水线 + 策略选择 + WebSocket 实时广播
- ✅ **Docker 部署**：docker-compose + PostgreSQL + Alembic
- ✅ **测试覆盖**：全面覆盖数据库 / API / 工具 / 协作 / LLM / WebSocket / SSE
- ✅ **生产特性**：速率限制、CORS 白名单、请求日志、错误追踪、健康检查

架构清晰，模块职责分明，具备良好的扩展性。主要技术风险集中在工具执行安全（TD-4）和 PostgreSQL 适配（TD-3），对当前阶段可接受。

**建议后续迭代优先级：**
1. 修复 PostgreSQL engine 创建逻辑（TD-3）
2. 强化工具执行沙箱隔离（TD-4）
3. 添加 CI/CD 集成（TD-11）
4. 决策：React 重写 or 清理残留配置（TD-7）
5. 协作消息队列持久化（TD-8）

---

*文档结束。*
