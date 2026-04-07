# Catown 技术可行性文档

**日期：** 2026-04-07
**版本：** v2.0（基于当前代码库整理）

---

## 1. 当前技术状态

### 1.1 整体架构

Catown 是一个多 Agent 协作平台，前后端分离，单进程部署：

| 层级 | 技术栈 | 状态 |
|------|--------|------|
| 后端 | Python + FastAPI + SQLAlchemy 2.0 + SQLite | ✅ 可用 |
| LLM 集成 | openai（AsyncOpenAI 客户端） | ✅ 可用 |
| 前端 | Vanilla JS + TailwindCSS CDN + marked.js + highlight.js | ✅ 已完成 |
| 实时通信 | FastAPI 原生 WebSocket | ✅ 已对接前端 |
| 配置管理 | .env 文件 + agents.json | ✅ 支持热更新 |

### 1.2 前端架构

前端已切换为 **单文件 Vanilla JS 方案**（`frontend/index.html`），无构建步骤：

- **渲染**：原生 DOM 操作 + marked.js（Markdown）+ highlight.js（代码高亮）
- **样式**：TailwindCSS CDN + 自定义 CSS（暗色主题）
- **HTTP**：原生 `fetch` API
- **WebSocket**：原生 `WebSocket` API，已连接后端 `/ws`
- **交互**：@mention Agent 自动补全、项目管理、聊天室切换、Config 面板

> ~~旧的 React + TypeScript + Vite 架构已废弃~~，`frontend/` 目录下仅保留 `package.json` 和 `vite.config.ts` 作为参考，无 React 源码。

### 1.3 后端模块

| 模块 | 职责 | 状态 |
|------|------|------|
| `main.py` | FastAPI 应用、CORS、速率限制、路由 | ✅ |
| `config.py` | 全局 Settings（.env → 环境变量） | ✅ |
| `agents/core.py` | Agent 类、记忆系统 | ⚠️ 工具调用为 stub |
| `agents/registry.py` | 内置 4 个 Agent 注册 | ✅ |
| `agents/config_models.py` | AgentConfigV2、Provider、多模型 | ✅ |
| `agents/config_manager.py` | JSON/YAML 配置加载 | ✅ |
| `llm/client.py` | AsyncOpenAI 封装、chat/chat_with_tools | ✅ |
| `models/database.py` | Agent/Project/Chatroom/Message/Memory | ✅ |
| `chatrooms/manager.py` | 聊天室 CRUD、消息存取 | ✅ |
| `routes/api.py` | REST API（含 Agent 响应循环、配置管理） | ✅ |
| `routes/websocket.py` | WebSocket 连接管理、房间广播 | ✅ |
| `tools/*.py` | 13 个工具（搜索/代码/文件/记忆/协作） | ✅ |

### 1.4 数据流（当前已通）

```
用户消息 → POST /api/chatrooms/{id}/messages
         → chatroom_manager.save_message()
         → trigger_agent_response()
           → 解析 @mention → 选择目标 Agent
           → 构建上下文（system prompt + 历史 + 记忆）
           → 循环（最多 5 轮）:
               llm_client.chat_with_tools(messages, tool_schemas)
               → 如果有 tool_calls → tool_registry.execute()
               → 结果回传 messages → 继续循环
               → 直到无 tool_calls
           → 保存响应 + WebSocket 广播
         → 返回用户消息
```

**✅ 完整链路已通**：用户提问 → Agent 思考 → 工具调用（多轮）→ 返回结果。

### 1.5 工具系统（已注册 13 个）

| 工具 | 说明 |
|------|------|
| `web_search` | DuckDuckGo 即时搜索（无需 API Key） |
| `execute_code` | Python 代码沙箱执行（sys.executable，10s 超时） |
| `retrieve_memory` | 数据库记忆检索（关键词 + Agent 过滤） |
| `read_file` / `write_file` / `list_files` / `delete_file` / `search_files` | 文件操作（workspace 安全校验） |
| `delegate_task` / `broadcast_message` / `check_task_status` / `list_collaborators` / `send_direct_message` | Agent 协作 |

---

## 2. 已解决问题

以下问题在 v1.x 开发周期中已修复，不再阻塞：

| 问题 | 修复方式 |
|------|----------|
| 前端 WebSocket 未对接 | 已使用原生 WebSocket，join room、消息广播、Agent 响应均已打通 |
| openai 库版本过旧 | requirements.txt 中为 1.13.3，但实际运行已兼容（AsyncOpenAI 接口稳定） |
| web_search 无 API Key | 改用 DuckDuckGo 即时 API（urllib，零依赖） |
| execute_code Windows 不兼容 | 改用 sys.executable 替代硬编码 python3 |
| retrieve_memory 未接数据库 | 已通过 SQLAlchemy 查询 messages 表 |
| 前端 Config 不显示当前值 | 新增 loadCurrentConfig()，自动加载 .env + agents.json 配置 |
| set_llm_client 不存在导致保存配置无效 | 已新增该函数，POST /config 后重建 LLM 客户端 |
| CORS 全开 | 已改为白名单配置（CORS_ORIGINS 环境变量） |
| 无速率限制 | 已添加 RateLimitMiddleware（基于 IP，可配置） |
| 前端 socket.io-client 不兼容 | 前端已完全重构为 vanilla，使用原生 WebSocket |

---

## 3. 当前技术债务

### 3.1 高优先级

| # | 问题 | 影响 | 建议方案 |
|---|------|------|----------|
| T-1 | `requirements.txt` 中 openai 版本为 1.13.3，未更新 | 部署时可能拉取旧版本 | 更新为 `openai>=2.0` |
| T-2 | `agents/core.py` 中 Agent 工具注册为 stub（`_register_default_tools`） | Agent 核心类自身不能调用工具，依赖 `routes/api.py` 的 `trigger_agent_response` | 将 `tool_registry` 接入 Agent 核心，让 Agent 类能自主调用工具 |
| T-3 | `registry_v2.py` 与 `registry.py` 并存 | 两套注册表，代码混乱 | 统一为一套，废弃 `registry_v2.py` |
| T-4 | `agents/core.py` 的 `_execute_tools` 用字符串匹配检测工具调用 | 与 routes/api.py 中基于 OpenAI tool_calls 的实现不一致 | 废弃字符串匹配，统一走 OpenAI tool_calls 协议 |
| T-5 | 协作系统（`collaboration.py`）已实现但未初始化 | Agent 间协作工具存在但不生效 | 在 `main.py` 启动时初始化 `collaboration_coordinator`，与 `tool_registry` 联动 |

### 3.2 中优先级

| # | 问题 | 影响 |
|---|------|------|
| T-6 | 所有 debug 输出使用 `print`，无结构化日志 | 生产环境难以排查 |
| T-7 | 无单元测试覆盖核心链路 | 回归风险高 |
| T-8 | Message 模型通过 agent_id JOIN 获取 agent_name，部分代码路径直接使用 `msg.agent_name`（动态属性） | 依赖 SQLAlchemy relationship，可能在 lazy load 时出错 |
| T-9 | 聊天室侧边栏搜索功能（`search-input`）未实现 | UI 有搜索框但无搜索逻辑 |
| T-10 | 前端 `API_BASE` 硬编码为 `http://localhost:8000/api` | 部署到其他环境需手动改代码 |

### 3.3 低优先级

| # | 问题 |
|---|------|
| T-11 | 记忆重要性评分仅简单关键词匹配 |
| T-12 | 文件操作工具的 `_is_safe_path` 在 Windows 上 `os.path.realpath` 可能有差异 |
| T-13 | 缺少数据库迁移方案（Alembic） |
| T-14 | 无错误追踪（Sentry 等） |

---

## 4. 后续开发计划

### Phase 2：架构清理与功能完善（建议 1-2 周）

**目标：消除技术债务，统一架构，补齐缺失功能。**

#### P2-1：统一 Agent 核心架构

- 废弃 `registry_v2.py`，保留 `registry.py` 并增强
- 将 `tool_registry` 正式接入 `agents/core.py` 的 Agent 类
- Agent 初始化时根据 `tools` 配置从 `tool_registry` 绑定工具，不再使用 stub
- 废弃 `agents/core.py` 中的字符串匹配 `_execute_tools`

#### P2-2：激活协作系统

- `main.py` 启动时调用 `collaboration_coordinator` 初始化
- `register_builtin_agents` 时同步注册 AgentCollaborator
- `delegate_task` 工具 → 协作协调器 → 目标 Agent 异步处理
- 前端 Collab tab 数据联动（已有 UI 骨架）

#### P2-3：配置系统增强

- `requirements.txt` 更新 openai 版本
- 前端 `API_BASE` 改为动态获取（从当前 origin 推断或环境注入）
- Config 面板支持编辑并保存 per-agent 配置（写回 agents.json）

#### P2-4：日志与可观测性

- 全局引入 `logging` 模块，替换所有 `print`
- 分级日志：INFO（正常流程）、DEBUG（工具调用详情）、ERROR（异常）
- 前端 Logs 面板对接后端日志流（通过 WebSocket 推送）

#### P2-5：测试覆盖

- LLM 连接测试（`test_llm.py` 实际执行验证）
- 工具执行测试（web_search / execute_code / retrieve_memory）
- API 集成测试（创建项目 → 发消息 → Agent 响应 → 工具调用）
- 前端 E2E（Playwright 基础场景）

### Phase 3：增强与生产化（建议 2-3 周）

**目标：提升系统质量，准备生产部署。**

#### P3-1：Agent 能力增强

- 记忆提取升级为 LLM 辅助（对话结束后自动总结重要信息）
- 多 Agent 协作流程编排（并行、串行、条件分支）
- Agent 间上下文共享机制

#### P3-2：前端体验提升

- LLM 流式输出（SSE 或 WebSocket streaming → 打字机效果）
- Agent 头像 / 状态实时指示
- 聊天室搜索功能
- 消息搜索 / 历史回溯

#### P3-3：生产部署

- Docker 化（Dockerfile + docker-compose）
- 环境变量管理（.env.production）
- PostgreSQL 支持（通过 SQLAlchemy URL 切换）
- Alembic 数据库版本管理
- 错误追踪集成

#### P3-4：插件与扩展

- 工具插件化，支持第三方工具动态加载
- Agent 角色 / system_prompt 通过配置文件扩展
- 预留插件市场接口

---

## 5. 风险评估

| 风险 | 级别 | 应对 |
|------|------|------|
| openai 版本升级兼容性 | 低 | AsyncOpenAI 接口稳定，实测即可 |
| Agent 核心重构影响现有功能 | 中 | 保持 routes/api.py 的 trigger_agent_response 作为主路径，Agent 类重构为渐进式 |
| 协作系统集成复杂度 | 中 | 已有完整设计，主要是调试和联调 |
| 前端单文件维护性下降 | 中 | 功能模块用函数分区（已有结构），必要时拆分为多 JS 文件 |

---

## 6. 总结

**项目状态：✅ 核心链路已通，进入架构清理阶段**

- 前后端完整可用，用户消息 → Agent 响应 → 工具调用 → 结果返回 全链路打通
- 前端已切换为 vanilla JS 方案，WebSocket 实时通信已对接
- 主要剩余工作是**统一 Agent 核心架构**和**激活协作系统**
- Phase 2 完成后系统架构清晰，Phase 3 可进入增强与生产化

**建议执行顺序：** P2-1 → P2-2 → P2-3 → P2-4 → P2-5 → Phase 3

---

*文档结束。*
