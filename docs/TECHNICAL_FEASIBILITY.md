# Catown 项目技术可行性文档

**作者：** Timi（架构师）
**日期：** 2026-04-03
**版本：** v1.0

---

## 1. 当前技术状态

### 1.1 整体架构

Catown 是一个多 Agent 协作平台，采用前后端分离架构：

| 层级 | 技术栈 | 版本/状态 |
|------|--------|-----------|
| 后端 | Python 3.12 + FastAPI | ✅ 运行正常 |
| LLM 集成 | openai 库（AsyncOpenAI 客户端） | ⚠️ 依赖版本待验证 |
| 数据库 | SQLite + SQLAlchemy 2.0 | ✅ 可用 |
| 前端 | React 18 + TypeScript + Vite + TailwindCSS | ✅ 可运行 |
| 实时通信 | FastAPI WebSocket（原生） | ✅ 可用 |
| 前端 HTTP | axios | ✅ 可用 |

> ⚠️ 注意：`package.json` 中引入了 `socket.io-client`，但后端使用的是 FastAPI 原生 WebSocket（`websockets` 库），**两者不兼容**。前端实际通过 axios REST API 通信，WebSocket 连接建立后未与 React 组件对接。

### 1.2 后端模块一览

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| 入口 | `main.py` | FastAPI 应用、CORS、路由挂载 | ✅ |
| Agent 核心 | `agents/core.py` | Agent 类、三层记忆系统、消息处理 | ⚠️ 工具调用逻辑简陋 |
| Agent 注册 | `agents/registry.py` | 内置 4 个 Agent（assistant/coder/reviewer/researcher） | ✅ |
| 配置模型 | `agents/config_models.py` | AgentConfigV2、多模型支持 | ✅ |
| 配置管理 | `agents/config_manager.py` | JSON/YAML 配置加载 | ✅ |
| LLM 客户端 | `llm/client.py` | AsyncOpenAI 封装、chat/chat_with_tools | ✅ |
| 数据库模型 | `models/database.py` | Agent/Project/Chatroom/Message/Memory | ✅ |
| 聊天室管理 | `chatrooms/manager.py` | 聊天室 CRUD、消息存储 | ✅ |
| API 路由 | `routes/api.py` | REST API（Agents/Projects/Chat/Config/Tools/Collaboration） | ✅ |
| WebSocket | `routes/websocket.py` | WebSocket 连接管理、房间广播 | ⚠️ 仅有连接层 |
| 工具系统 | `tools/*.py` | 12 个工具（搜索/代码/文件/记忆/协作） | ✅ 注册，⚠️ Agent 未集成 |

### 1.3 工具系统（已注册 12 个工具）

| 工具 | 说明 | 状态 |
|------|------|------|
| `web_search` | DuckDuckGo 即时搜索（无 API Key） | ✅ 已改为 DuckDuckGo |
| `execute_code` | Python 代码沙箱执行 | ✅ 已修复 Windows 兼容 |
| `retrieve_memory` | 从数据库检索历史消息 | ✅ 已接入数据库 |
| `read_file` | 读取文件（含 workspace 安全校验） | ✅ |
| `write_file` | 写入文件（含安全校验） | ✅ |
| `list_files` | 列出目录 | ✅ |
| `delete_file` | 删除文件/空目录 | ✅ |
| `search_files` | 文件内容搜索 | ✅ |
| `delegate_task` | 委托任务给其他 Agent | ✅ |
| `broadcast_message` | 广播消息 | ✅ |
| `check_task_status` | 检查任务状态 | ✅ |
| `list_collaborators` | 列出协作 Agent | ✅ |
| `send_direct_message` | Agent 间私聊 | ✅ |

### 1.4 前端模块

| 页面 | 文件 | 状态 |
|------|------|------|
| 主应用框架 | `MainApp.tsx` | ✅ 侧边栏 + 路由 |
| 仪表盘 | `Dashboard.tsx` | ✅ 项目概览 |
| 聊天室 | `ChatRoom.tsx` | ⚠️ 使用模拟响应，未对接真实 Agent |
| Agent 管理 | `Agents.tsx` | ✅ |
| 项目详情 | `ProjectDetail.tsx` | ✅ |
| 状态监控 | `Status.tsx` | ✅ |
| 设置 | `Settings.tsx` | ✅ |

### 1.5 数据流现状

```
用户 → 前端 (axios POST) → /api/chatrooms/{id}/messages
                           ↓
                    chatroom_manager.save_message()
                           ↓
                    trigger_agent_response()
                           ↓
                    构建消息上下文 + 获取工具 schemas
                           ↓
                    llm_client.chat_with_tools()
                           ↓
                    如果有 tool_calls → tool_registry.execute()
                           ↓
                    保存响应 + WebSocket 广播
```

**问题：** `chat_with_tools` 返回的 `tool_calls` 是 OpenAI 格式的对象，工具执行参数通过 `json.loads(tool_call.function.arguments)` 解析，这条路径**可以通**，但 `tools/__init__.py` 中工具已注册到 `tool_registry`，而 `routes/api.py` 中确实使用了 `tool_registry`，链路是通的。但 `agents/core.py` 中的 `_execute_tools` 方法是简易版（字符串匹配），**未被 API 路由调用**。

---

## 2. 已修复问题（昨日修复记录）

### 2.1 openai 库升级（1.13 → 2.30）

- `requirements.txt` 原为 `openai==1.13.3`
- 升级到 2.x 系列后，API 兼容，`AsyncOpenAI` 接口不变
- **风险：** 需要确认 `chat.completions.create` 返回的 response 结构未变化（经代码审查，2.x 保持了相同的返回格式）
- **待验证：** 实际运行测试确认 LLM 调用正常

### 2.2 web_search 改为 DuckDuckGo

- **修改前：** 可能依赖有 API Key 的搜索引擎（如 SerpAPI/Bing）
- **修改后：** 使用 `api.duckduckgo.com` 即时搜索 API（免费，无需 Key）
- 通过 `urllib.request` 实现，不引入额外依赖
- **局限：** DuckDuckGo 即时 API 只返回摘要/相关话题，不是完整的搜索结果页面。对简单查询够用，复杂搜索能力有限

### 2.3 execute_code 修复 Windows 兼容

- **修改前：** 硬编码使用 `python3` 命令，Windows 上不存在
- **修改后：** 使用 `sys.executable` 获取当前 Python 解释器路径
- 临时文件创建和清理逻辑正确
- 10 秒超时保护已到位
- **✅ Windows/Linux 双平台可用**

### 2.4 retrieve_memory 接入数据库

- **修改前：** 仅从内存中的 Agent 对象获取记忆摘要
- **修改后：** 通过 SQLAlchemy 查询 `messages` 表，支持关键词搜索、按 Agent 过滤
- 使用数据库独立连接（`next(get_db())`），不依赖会话上下文
- 返回最近 5 条相关消息
- **✅ 持久化记忆检索**

### 2.5 前端选用 vanilla 版

- 当前前端仍是 React + TypeScript 技术栈
- 用户提到"选用 vanilla 版"——指前端技术选型确定为 **不使用 React 框架的纯 HTML/CSS/JS 方案**
- **这是一个待执行的变更**，当前代码库中仍是 React 版本

---

## 3. 剩余技术债务

### 3.1 高优先级（阻塞功能）

| # | 问题 | 影响 | 建议方案 |
|---|------|------|----------|
| T-1 | **openai 2.x 未实际验证** | `requirements.txt` 仍显示 1.13.3 | `pip install openai==2.30` 后执行 `test_llm.py` 验证 |
| T-2 | **工具调用链路断裂** | `agents/core.py` 的工具调用是简易字符串匹配，不解析 LLM 返回的 tool_calls；真实工具调用在 `routes/api.py` 的 `trigger_agent_response` 中实现，但 Agent 对象本身不能自主调用工具 | 统一工具调用入口，让 Agent 核心类正确解析 tool_calls |
| T-3 | **前后端通信不一致** | `package.json` 引入了 `socket.io-client`，但后端用 FastAPI 原生 WebSocket，协议不匹配；前端 ChatRoom 用模拟响应而非真实等待 Agent 回复 | 移除 socket.io-client，统一用 WebSocket 或纯 REST |
| T-4 | **消息 Message 模型不一致** | `models/database.py` 的 Message 表没有 `agent_name` 字段，但 `chatrooms/manager.py` 和 API 中使用了 `msg.agent_name` | 需要在数据库 Model 中添加 `agent_name` 列，或从关联的 Agent 表 JOIN 获取 |

### 3.2 中优先级（影响体验）

| # | 问题 | 影响 |
|---|------|------|
| T-5 | `agents/core.py` 中 Agent 初始化时注册的工具（`_register_default_tools`）是简易 stub，不是 `tools/` 目录下的正经工具实现 |
| T-6 | 协作系统（`collaboration.py`）有完整设计（AgentCollaborator、CollaborationCoordinator、任务委派），但没有被 `main.py` 初始化，处于"写了但没用"状态 |
| T-7 | `registry_v2.py` 和 `registry.py` 并存，新旧两套注册表，不清楚用哪套 |
| T-8 | 没有单元测试覆盖核心路径（`test_backend.py` 存在但覆盖率未知） |
| T-9 | 前端 ChatRoom 的"Agent is thinking..."是硬编码的模拟消息，不是真实 Agent 响应 |
| T-10 | CORS 配置为 `allow_origins=["*"]`，生产环境需收紧 |

### 3.3 低优先级（优化项）

| # | 问题 | 影响 |
|---|------|------|
| T-11 | 记忆系统的"重要性评分"和"长期记忆提取"只有简单关键词匹配，不够智能 |
| T-12 | `chat_with_tools` 中 tool_calls 的处理是线性执行，没有多轮 tool-call → response 循环（OpenAI 标准的 tool_use 需要多次往返） |
| T-13 | 文件操作工具的 workspace 安全校验（`_is_safe_path`）在 Windows 上 `os.path.realpath` 可能和预期有差异 |
| T-14 | 缺少请求速率限制（rate limiting） |
| T-15 | 无日志系统，所有 debug 信息用 print 输出 |

---

## 4. Phase 2 / Phase 3 技术方案

### 4.1 Phase 2：功能完整化（建议 2-3 周）

#### 目标：让系统真正能跑通"用户提问 → Agent 思考 → 工具调用 → 返回结果"的完整链路

**P2-1：修复工具调用链路（最高优先级）**

```
当前问题：
  LLM 返回 tool_calls → routes/api.py 能执行
  但 Agent 对象自己的 process_message 方法用字符串匹配检测工具调用

修复方案：
  修改 agents/core.py._generate_response()
  → 使用 llm_client.chat_with_tools() 替代直接 chat()
  → 解析 response.choices[0].message.tool_calls
  → 循环执行：tool_call → 结果回传 → 继续生成
  → 直到 LLM 不再返回 tool_calls
```

**P2-2：统一 Agent 工具注册**

- 废弃 `agents/core.py` 中的 `_register_default_tools` 简易 stub
- 改为从全局 `tool_registry` 获取工具
- Agent 初始化时根据配置中的 `tools` 列表，从 `tool_registry` 绑定对应工具实例

**P2-3：WebSocket 实时通信打通**

- 前端移除 `socket.io-client` 依赖
- 使用原生 `WebSocket` API 连接后端 `/ws`
- 在 ChatRoom 页面中：
  - 发送消息后通过 WS 监听 Agent 响应
  - 替代当前的 `setTimeout` 模拟方案

**P2-4：修复数据库模型不一致**

- 在 `Message` 模型中添加 `agent_name` 列（nullable）
- 或者改为通过 `agent_id` JOIN 获取 agent name（更规范）

**P2-5：协作系统上线**

- 在 `main.py` 启动时初始化 `collaboration_coordinator`
- 在 `trigger_agent_response` 中注册 AgentCollaborator
- 实现 Agent 间的任务委派（`delegate_task` 工具 → 协作协调器 → 目标 Agent 处理）

**P2-6：前端技术栈切换为 Vanilla JS**

> 根据用户要求，前端从 React + TypeScript 方案切换为纯 HTML/CSS/JS（vanilla 版）

- 使用 `room.html` 已有的静态页面作为基础
- 实现：
  - 项目管理（CRUD）
  - Agent 列表展示
  - 聊天室实时交互
  - 状态监控
- 移除 `frontend/` React 项目目录（或保留为备份）
- 后端 `main.py` 中的静态文件挂载指向 `room.html` 或其衍生产物

**P2-7：补充测试**

- LLM 连接测试（验证 openai 2.x 兼容）
- 工具执行测试（web_search / execute_code / retrieve_memory）
- API 集成测试（项目创建 → 消息发送 → Agent 响应）

### 4.2 Phase 3：增强与优化（建议 3-4 周）

#### 目标：提升系统质量、扩展能力、准备生产部署

**P3-1：Agent 自主学习**

- 将关键词匹配的记忆提取升级为 LLM 辅助提取
- Agent 对话结束后，调用 LLM 自动总结重要信息存入长期记忆
- 实现记忆重要性动态评分

**P3-2：多轮工具调用完整支持**

```
标准 OpenAI tool_use 循环：
  1. 用户消息 → LLM (with tools) → 可能返回 tool_calls
  2. 执行所有 tool_calls → 构建 tool response messages
  3. 再次调用 LLM (with tool results) → 可能继续 tool_calls 或给出最终回答
  4. 循环直到 LLM 不再请求工具
```

**P3-3：Agent 配置热加载**

- 支持运行时通过 API 更新 Agent 的 system_prompt、工具列表、模型配置
- 无需重启服务即可生效
- 配置文件 `agents.json` 的 watch 机制

**P3-4：前端增强**

- 打字机效果（streaming 输出）
- Markdown 渲染
- Agent 头像/状态指示
- 深色模式

**P3-5：生产部署准备**

- Docker 化（前后端分别 Dockerfile + docker-compose）
- 环境变量管理（.env.production）
- 日志系统（替换 print → logging 模块）
- 错误追踪（Sentry 或同类）
- CORS 收紧
- 数据库迁移方案（Alembic）
- 可选：PostgreSQL 替代 SQLite

**P3-6：插件系统**

- 工具插件化，支持第三方工具动态加载
- Agent 角色可通过配置文件扩展
- 预置插件市场机制

---

## 5. 前后端架构建议

### 5.1 前端：推荐 Vanilla JS 路线

**理由：**
- Catown 当前的交互模式相对简单（项目管理 + 聊天），不需要 React 的组件化复杂度
- Vanilla JS 部署零构建，直接 `room.html` + CSS + JS，降低依赖
- 团队规模小，维护成本低
- 可以快速迭代

**推荐方案：**
```
catown/
├── backend/          # FastAPI 后端（不变）
├── frontend/
│   ├── index.html    # 主页面（vanilla）
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── api.js      # REST API 封装
│       ├── websocket.js # WebSocket 管理
│       ├── app.js       # 主应用逻辑
│       └── chat.js      # 聊天模块
└── data/             # SQLite 数据库
```

- 用原生 `fetch` 替代 axios
- 用原生 `WebSocket` 替代 socket.io-client
- 可使用轻量级模板库（如 Mustache）或直接 DOM 操作
- TailwindCSS 的 CDN 版本可继续用于样式

### 5.2 后端：FastAPI 架构合理

**当前架构可保持，需改进点：**

1. **工具调用统一：** 所有工具调用走 `tool_registry`，Agent 核心类不再维护独立的工具注册
2. **协作系统激活：** 在 `main.py` 启动时连接 `collaboration_coordinator` 和 `tool_registry`
3. **日志系统：** 引入 `logging`，所有 `print` 替换为 `logger.info/debug/error`
4. **错误处理：** API 层面统一异常捕获，返回标准错误格式

### 5.3 数据库迁移

- 当前 SQLite 适合开发/测试
- Phase 3 建议提供 PostgreSQL 支持（通过 SQLAlchemy 的数据库 URL 切换）
- 引入 Alembic 做数据库版本管理

---

## 6. 风险评估

| 风险 | 级别 | 应对 |
|------|------|------|
| openai 2.x 兼容性破坏 | 低 | API 结构未变，实测即可确认 |
| vanilla 前端重构工作量 | 中 | 当前功能点有限，预计 3-5 天完成 |
| 协作系统复杂度 | 中高 | AgentCollaborator/Coordinator 设计已有，需调试集成 |
| 多轮工具调用实现 | 中 | OpenAI 标准模式，按规范实现即可 |

---

## 7. 总结

**项目可研结论：✅ 可行，需补全关键链路**

- 架构设计合理，模块划分清晰
- 核心功能骨架已具备（Agent/LLM/工具/数据库/WebSocket）
- 主要阻塞点是**工具调用链路断裂**和**前后端通信不一致**
- Phase 2 解决阻塞后，系统可以完整跑通
- Phase 3 侧重质量提升和生产化

**建议执行顺序：** P2-1 → P2-2 → P2-4 → P2-3 → P2-6 → P2-5 → P2-7

---

*文档结束。有任何问题随时问我。* 🛠️
