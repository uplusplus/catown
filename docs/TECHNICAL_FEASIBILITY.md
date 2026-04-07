# Catown 技术可行性文档

**日期：** 2026-04-07
**版本：** v3.0（基于代码库实际审查更新）
**最后更新提交：** `0326933` refactor: P2-1/P2-2/P2-4 统一Agent架构 + 激活协作系统 + 结构化日志

---

## 1. 当前技术状态

### 1.1 整体架构

Catown 是一个多 Agent 协作平台，前后端分离，单进程部署：

| 层级 | 技术栈 | 状态 |
|------|--------|------|
| 后端 | Python + FastAPI + SQLAlchemy 2.0 + SQLite | ✅ 可用 |
| LLM 集成 | openai 1.13.3（AsyncOpenAI 客户端） | ✅ 可用（见 T-1） |
| 前端 | 单文件 Vanilla JS + TailwindCSS CDN + marked.js + highlight.js | ✅ 已完成 |
| 实时通信 | FastAPI 原生 WebSocket | ✅ 已对接前端 |
| 配置管理 | .env 文件 + agents.json（支持多 Provider） | ✅ 支持热更新 |
| 协作系统 | CollaborationCoordinator + AgentCollaborator | ✅ 已初始化并联通 |
| 结构化日志 | logging 模块 + 分级输出 | ✅ 已完成 |

### 1.2 前端架构

前端为**单文件 Vanilla JS 方案**（`frontend/index.html`，1668 行），无构建步骤：

- **渲染**：原生 DOM 操作 + marked.js（Markdown）+ highlight.js（代码高亮）
- **样式**：TailwindCSS CDN + 自定义 CSS（暗色主题、glass-panel 效果）
- **HTTP**：原生 `fetch` API，`API_BASE` 指向 `http://localhost:8000/api`
- **WebSocket**：原生 `WebSocket` API，已连接后端 `/ws`
- **交互**：@mention Agent 自动补全、项目管理、聊天室切换、Config 面板

### 1.3 后端模块

| 模块 | 职责 | 状态 |
|------|------|------|
| `main.py` | FastAPI 应用、CORS 白名单、速率限制、路由、初始化协作系统 | ✅ |
| `config.py` | 全局 Settings（.env → 环境变量），唯一配置入口 | ✅ |
| `agents/core.py` | Agent 类（配置持有、记忆系统、对话历史管理） | ✅ 职责清晰 |
| `agents/registry.py` | Agent 注册表，支持从 agents.json 加载配置 | ✅ 统一 |
| `agents/config_models.py` | AgentConfigV2、Provider、多模型配置 | ✅ |
| `agents/config_manager.py` | JSON/YAML 配置加载 | ✅ |
| `agents/collaboration.py` | 协作协调器、AgentCollaborator、任务路由 | ✅ 已激活 |
| `llm/client.py` | AsyncOpenAI 封装、chat/chat_with_tools | ✅ |
| `models/database.py` | Agent/Project/Chatroom/Message/Memory + SQLite | ✅ |
| `chatrooms/manager.py` | 聊天室 CRUD、消息存取 | ✅ |
| `routes/api.py` | REST API + Agent 响应循环（含完整工具调用链路） | ✅ |
| `routes/websocket.py` | WebSocket 连接管理、房间广播 | ✅ |
| `tools/*.py` | 14 个工具（搜索/代码/文件/记忆/协作/保存记忆） | ✅ |

### 1.4 数据流（已通）

```
用户消息 → POST /api/chatrooms/{id}/messages
         → chatroom_manager.save_message()
         → trigger_agent_response()
           → 解析 @mention → 选择目标 Agent
           → 构建上下文（system prompt + 历史 + 记忆注入）
           → 自动注册 Agent 为协作者
           → 循环（最多 5 轮）:
               llm_client.chat_with_tools(messages, tool_schemas)
               → 如果有 tool_calls → tool_registry.execute()
               → 结果回传 messages（tool role）→ 继续循环
               → 直到无 tool_calls
           → 保存响应 + WebSocket 广播
         → 返回用户消息
```

**✅ 完整链路已通**：用户提问 → Agent 思考 → 工具调用（多轮）→ 结果返回。

### 1.4.1 流式数据流（SSE）

```
用户消息 → POST /api/chatrooms/{id}/messages/stream
         → StreamingResponse (text/event-stream)
         → event: agent_start {"agent_name": "..."}
         → event: content {"delta": "..."}      ← 打字机效果，逐字输出
         → event: tool_start {"tool": "..."}    ← 工具开始执行
         → event: tool_result {"tool": "..."}   ← 工具执行完毕
         → ...（循环，最多 5 轮工具调用）
         → event: done {"message_id": 123}      ← 流结束
```

**前端**：使用 `fetch` + `ReadableStream` 消费 SSE，实时更新 DOM。
**回退**：若流式失败，自动降级为同步 `POST /messages` + 轮询。

### 1.5 工具系统（已注册 14 个）

| 工具 | 说明 | 状态 |
|------|------|------|
| `web_search` | DuckDuckGo 即时搜索（无需 API Key） | ✅ |
| `execute_code` | Python 代码沙箱执行（sys.executable，10s 超时） | ✅ |
| `retrieve_memory` | 数据库记忆检索（关键词 + Agent 过滤） | ✅ |
| `save_memory` | 保存记忆到数据库 | ✅ |
| `read_file` / `write_file` / `list_files` / `delete_file` / `search_files` | 文件操作（workspace 安全校验） | ✅ |
| `delegate_task` | 委托任务给其他 Agent | ✅ 已联通协作系统 |
| `broadcast_message` | 广播消息给所有 Agent | ✅ |
| `check_task_status` | 检查委托任务状态 | ✅ |
| `list_collaborators` | 列出可用协作者 | ✅ |
| `send_direct_message` | 向指定 Agent 发送私信 | ✅ |

### 1.6 协作系统（已激活）

- `main.py` 启动时调用 `init_collaboration_tools(collaboration_coordinator)`
- `trigger_agent_response` 中自动将 Agent 注册为协作者（`AgentCollaborator`）
- `CollaborationCoordinator` 管理消息路由、任务注册、聊天室状态
- 5 个协作工具已绑定 coordinator 实例
- 支持单 Agent 和多 Agent 协作策略

### 1.7 记忆系统

- **模型层**：`Memory` 表（agent_id, memory_type, content, importance, metadata）
- **上下文注入**：`trigger_agent_response` 自动加载 Agent 最重要的 10 条记忆注入 system prompt
- **保存工具**：`save_memory` 工具允许 Agent 主动保存记忆
- **检索工具**：`retrieve_memory` 支持关键词 + Agent 过滤
- **Agent 内存**：`Agent` 类维护 short_term/long_term/procedural 三层内存（进程内）

### 1.8 日志系统

- `logging.basicConfig` 配置在 `main.py` 启动时完成
- 日志级别从 `LOG_LEVEL` 环境变量读取（默认 INFO）
- 格式：`%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- 已使用 `logger = logging.getLogger("catown")` 分模块日志

---

## 2. 已解决问题

以下问题已在 P2 阶段修复（提交 `0326933`）：

| 问题 | 修复方式 | 状态 |
|------|----------|------|
| `registry_v2.py` 与 `registry.py` 并存 | 已删除 `registry_v2.py`，统一为 `registry.py` | ✅ |
| 协作系统已实现但未初始化 | `main.py` 启动时初始化 `collaboration_coordinator` 并调用 `init_collaboration_tools` | ✅ |
| 所有 debug 输出使用 `print` | 全局引入 `logging` 模块，main.py 配置结构化日志 | ✅ |
| Agent 工具注册为 stub（旧 `_register_default_tools`） | Agent 类职责明确为配置持有 + 记忆，工具执行统一走 `routes/api.py` | ✅ |
| 前端 WebSocket 未对接 | 已使用原生 WebSocket，join room、消息广播、Agent 响应均已打通 | ✅ |
| web_search 无 API Key | 改用 DuckDuckGo 即时 API（urllib，零依赖） | ✅ |
| execute_code Windows 不兼容 | 改用 sys.executable 替代硬编码 python3 | ✅ |
| retrieve_memory 未接数据库 | 已通过 SQLAlchemy 查询 memories 表 | ✅ |
| CORS 全开 | 已改为白名单配置（CORS_ORIGINS 环境变量） | ✅ |
| 无速率限制 | 已添加 RateLimitMiddleware（基于 IP，可配置） | ✅ |
| 前端 socket.io-client 不兼容 | 前端完全重构为 vanilla JS，使用原生 WebSocket | ✅ |
| `set_llm_client` 不存在 | 已新增该函数，POST /config 后重建 LLM 客户端 | ✅ |

---

## 3. 当前技术债务

### 3.1 高优先级

| # | 问题 | 影响 | 建议方案 |
|---|------|------|----------|
| T-1 | `requirements.txt` 中 openai 版本为 1.13.3（2024 年版本） | 功能上可用（AsyncOpenAI 接口稳定），但缺少新特性（如 structured outputs、assistants v2） | 更新为 `openai>=1.40.0`，兼容性风险低 |
| T-2 | 前端 `API_BASE` 硬编码为 `http://localhost:8000/api` | 部署到其他环境需手动改代码 | 改为从当前 origin 推断或 `<script>` 注入 |
| T-3 | Agent 核心类（`agents/core.py`）与实际执行路径（`routes/api.py`）分离 | Agent 类仅作数据持有，不参与实际工具调用和 LLM 交互 | 可接受（轻量设计），但需文档说明 |

### 3.2 中优先级

| # | 问题 | 影响 |
|---|------|------|
| T-4 | 聊天室侧边栏搜索功能未实现 | UI 有搜索框但无搜索逻辑 |
| T-5 | 无单元测试覆盖核心链路（`backend/tests/` 存在但未集成到 CI） | 回归风险中等 |
| T-6 | 前端单文件 1668 行，维护性随功能增长下降 | 复杂度累积 |
| T-7 | `ChatroomManager.process_user_message` 中有 TODO 注释 | 协作路由逻辑未完善 |
| T-8 | `models/database.py` 使用 `declarative_base()` 旧式写法 | SQLAlchemy 2.0 推荐 `DeclarativeBase` |
| T-9 | Config 面板保存后直接写 `.env` 文件，无验证 | 可能写入无效配置 |

### 3.3 低优先级

| # | 问题 |
|---|------|
| T-10 | 记忆重要性评分仅简单关键词匹配 |
| T-11 | 文件操作工具的 `_is_safe_path` 在 Windows 上 `os.path.realpath` 可能有差异 |
| T-12 | 缺少数据库迁移方案（Alembic） |
| T-13 | 无错误追踪（Sentry 等） |
| ~~T-14~~ | ~~SSE 流式输出未实现~~ | ✅ 已实现 |

---

## 4. 开发路线图

### ~~Phase 2：架构清理与功能完善~~ ✅ 已完成

| 任务 | 状态 | 说明 |
|------|------|------|
| P2-1 统一 Agent 核心架构 | ✅ | 已删除 `registry_v2.py`，职责分离清晰 |
| P2-2 激活协作系统 | ✅ | `main.py` 初始化 + 工具绑定 + 自动注册 |
| P2-4 日志与可观测性 | ✅ | 结构化日志 + 分级输出 |
| P2-3 配置系统增强 | ⚠️ 部分 | openai 版本和前端 API_BASE 未更新 |
| P2-5 测试覆盖 | ⚠️ 部分 | 单元测试存在但无 CI 集成 |

### Phase 3：增强与生产化（当前阶段）

**目标：提升系统质量，准备生产部署。**

#### P3-1：依赖与配置修复（1-2 天）✅ 已完成
- [x] 更新 `requirements.txt` 中 openai 版本（`>=1.40.0`）
- [x] 前端 `API_BASE` 改为动态获取（从 `window.location` 推断）
- [x] Config 面板输入验证（Pydantic field_validator）

#### P3-2：前端体验提升（3-5 天）
- [x] LLM 流式输出（SSE 打字机效果）
  - `llm/client.py` 新增 `chat_stream()` 方法（async generator）
  - `routes/api.py` 新增 `POST /chatrooms/{id}/messages/stream`（SSE endpoint）
  - `frontend/index.html` `sendUserMessage()` 改为流式消费 + 自动回退 sync
- [x] Agent 头像 / 状态实时指示
  - Agent 状态栏支持三种状态：Idle（灰色月亮）/ Thinking（黄色旋转）/ Working（绿色齿轮）
  - SSE 事件自动驱动状态切换
  - 头像支持 pulse 动画
- [x] 聊天室搜索功能
  - 侧边栏搜索框实时过滤项目列表
  - 支持按名称和描述搜索
- [x] 消息搜索 / 历史回溯
  - Header 新增搜索按钮，展开搜索栏
  - 实时过滤当前聊天室消息，高亮匹配文本

#### P3-3：Agent 能力增强（3-5 天）
- [ ] 记忆提取升级为 LLM 辅助（对话结束后自动总结重要信息）
- [ ] 多 Agent 协作流程编排（并行、串行、条件分支）
- [ ] Agent 间上下文共享机制增强

#### P3-4：测试与质量保障（2-3 天）
- [ ] 核心链路单元测试（LLM mock + 工具执行）
- [ ] API 集成测试自动化
- [ ] Playwright E2E 测试扩展
- [ ] 测试 CI 集成

#### P3-5：生产部署（3-5 天）
- [ ] Docker 化（Dockerfile + docker-compose）
- [ ] 环境变量管理（.env.production）
- [ ] PostgreSQL 支持（通过 SQLAlchemy URL 切换）
- [ ] Alembic 数据库版本管理
- [ ] 错误追踪集成

### Phase 4：插件与扩展（远期）

- [ ] 工具插件化，支持第三方工具动态加载
- [ ] Agent 角色 / system_prompt 通过配置文件扩展
- [ ] 预留插件市场接口

---

## 5. 风险评估

| 风险 | 级别 | 应对 |
|------|------|------|
| openai 版本升级兼容性 | 低 | AsyncOpenAI 接口稳定，实测即可 |
| 前端单文件维护性下降 | 中 | 功能模块用函数分区（已有结构），必要时拆分为多 JS 文件 |
| 流式输出改造工作量 | 中 | FastAPI 原生支持 SSE，改造范围可控 |
| 数据库迁移（SQLite → PostgreSQL） | 中 | SQLAlchemy 抽象层已用，切换成本低 |
| 多 Agent 协作复杂度 | 中 | 已有完整设计和基础实现，主要是调试和联调 |

---

## 6. 总结

**项目状态：✅ Phase 2 完成，进入 Phase 3 增强与生产化阶段**

- 前后端完整可用，用户消息 → Agent 响应 → 工具调用 → 结果返回 全链路打通
- Agent 核心架构已统一（`registry_v2.py` 已删除），协作系统已激活
- 结构化日志已配置，14 个工具已注册并可用
- 记忆系统已联通数据库，上下文注入已实现
- 主要剩余工作：**流式输出**、**前端体验优化**、**生产部署**

**建议执行顺序：** P3-1 → P3-2 → P3-3 → P3-4 → P3-5 → Phase 4

---

*文档结束。*
