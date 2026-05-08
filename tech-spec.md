# Catown 当前项目架构分析

> 分析对象：当前仓库实际结构与已检查代码。  
> 分析时间：2026-05-07  
> 角色：Architect

## 1. 总体架构概览

Catown 是一个以 **多 Agent 协作为核心的软件工厂平台**。整体形态是一个单体后端 + 单体前端 + 本地/容器化运行环境的架构：

```text
用户浏览器
  │
  │ HTTP / WebSocket / SSE
  ▼
Frontend: React + Vite + TypeScript
  │
  │ REST API / WebSocket
  ▼
Backend: FastAPI
  ├─ routes: API、监控、审计、Pipeline、WebSocket
  ├─ services: 编排、会话、上下文、审批、运行账本、流式输出
  ├─ agents: Agent 配置、身份、协作、注册与运行抽象
  ├─ tools: 文件、Shell、浏览器、GitHub、搜索、技能、记忆等工具实现
  ├─ pipeline: Pipeline 模板与执行引擎
  ├─ chatrooms: 聊天室管理
  └─ models: SQLAlchemy 数据模型
  │
  ▼
SQLite / 文件系统 / 外部 LLM / GitHub / Web 搜索等外部能力
```

当前架构的核心不是传统 CRUD，而是围绕以下主线展开：

1. 用户在前端创建聊天、任务或 Pipeline。
2. 后端 FastAPI 接收请求。
3. 编排服务构造上下文、选择 Agent、驱动 LLM Turn。
4. Agent 可调用工具执行文件、Shell、浏览器、GitHub、Skill 等操作。
5. 运行过程写入数据库、运行账本、审计记录、监控事件。
6. 前端通过 HTTP / WebSocket / SSE 获取实时状态、日志、监控与结果。

这是一个 **模块化单体架构**。它没有拆成微服务，这是合理的：当前项目的复杂度主要来自编排流程和运行时状态管理，而不是独立业务域的横向扩展。

## 2. 主要目录与模块职责

### 2.1 根目录

| 路径 | 职责 |
|---|---|
| `backend/` | FastAPI 后端、Agent 编排、工具执行、数据模型、Pipeline 引擎 |
| `frontend/` | React/Vite 前端，包含主应用、监控页面、API Client、组件 |
| `docs/` | ADR 架构决策记录，包含知识图谱、聊天输入、Omni、UI/UX Skill 等设计记录 |
| `tests/` | 端到端、前端渲染、集成测试 |
| `configs/` | 配置目录，目前仓库内为空，运行时配置可能落在 Catown data root |
| `docker-compose.yml` | 单服务容器编排，暴露 8000 端口 |
| `Dockerfile` | 应用镜像构建 |
| `README.md` | 产品定位、核心能力与运行说明 |

### 2.2 Backend

#### `backend/main.py`

后端入口。主要职责：

- 初始化 FastAPI 应用。
- 注册路由。
- 配置 CORS、静态资源、生命周期事件等运行时能力。
- 承接前端与外部调用。

#### `backend/routes/`

API 层。已看到的主要路由文件：

| 文件 | 主要职责 |
|---|---|
| `api.py` | 核心业务 API，文件较大，是当前主要耦合点之一 |
| `pipeline.py` | Pipeline 创建、查询、启动、暂停、恢复、审批、拒绝、人工指令、消息、产物、阶段查询 |
| `monitor.py` | 监控日志、网络事件、SSE 流、运行卡片、用量、任务运行、审批队列、概览 |
| `audit.py` | LLM 调用、工具调用、事件、Token 汇总、时间线审计查询 |
| `websocket.py` | WebSocket 连接管理、聊天室订阅、Topic 订阅与广播 |
| `file_watcher.py` | 文件变更相关能力 |

API 层目前承担了较多协调逻辑，尤其 `api.py` 体量较大，后续应持续下沉到 service 层。

#### `backend/services/`

服务层是当前后端最重要的架构核心，负责把复杂运行逻辑从 routes 中拆出。主要模块群：

| 模块群 | 代表文件 | 职责 |
|---|---|---|
| 会话运行 | `session_service.py`, `single_agent_session_orchestrator.py`, `single_agent_session_runner.py`, `single_agent_stream_session.py` | 单 Agent 会话、同步/流式运行、终止与回调 |
| 编排运行 | `orchestration_*` | 多 Agent 编排、阶段运行、收件箱、调度、恢复、handoff、stream runner |
| 上下文构建 | `context_builder.py`, `chat_prompt_builder.py` | 构造 LLM 输入上下文、提示词、项目/聊天状态 |
| 运行状态 | `task_state.py`, `turn_state.py`, `orchestration_step_state.py` | Turn、任务、编排步骤状态管理 |
| 工具治理 | `tool_governance.py`, `approval_queue.py`, `approval_replay.py` | 高风险工具审批、审批队列、审批后重放 |
| 监控与审计辅助 | `monitor_projection.py`, `runtime_event_helpers.py`, `run_ledger.py` | 运行事件投影、运行账本、监控展示数据 |
| 流式传输 | `stream_transport.py`, `stream_turn_executor.py`, `nonstream_turn_executor.py`, `stream_runtime_persistence.py` | LLM 流式帧、非流式 Turn 执行、持久化 |
| 子 Agent 生命周期 | `subagent_lifecycle.py` | 子 Agent 创建、加入、退出或生命周期处理 |
| Workflow/Pipeline 契约 | `workflow_spec_contracts.py`, `artifact_contracts.py`, `action_request_contracts.py` | Pydantic 契约定义 |

服务层已经具备比较清晰的领域拆分，是当前项目可演进性的关键。

#### `backend/agents/`

Agent 抽象与配置层：

| 文件 | 职责 |
|---|---|
| `core.py` | Agent 核心类，持有配置、角色、工具列表、模型信息、记忆与对话历史；实际 LLM 调用和工具执行由 routes/services 驱动 |
| `identity.py` | Agent 身份相关能力 |
| `registry.py` 等 | Agent 注册、查找与配置管理 |
| `collaboration.py` | Agent 协作相关逻辑 |
| `config_manager.py`, `config_models.py` | Agent 配置加载、校验、模型定义 |

当前设计将 Agent 本身定义为“能力与身份配置”，而把运行过程放在 service，这个边界是合理的。

#### `backend/tools/`

工具系统是 Agent 执行外部动作的边界层：

| 文件 | 能力 |
|---|---|
| `base.py` | 工具基类、统一执行接口、元数据/Schema 约定 |
| `file_operations.py` | 读写文件、列目录、搜索、删除等 |
| `run_shell.py` | Shell 命令执行，高风险工具 |
| `execute_code.py` | 沙箱代码执行 |
| `browser.py`, `screenshot.py` | 浏览器自动化与截图 |
| `github_manager.py` | GitHub 仓库、分支、PR、Issue、Release、文件操作 |
| `web_search.py`, `web_fetch.py` | Web 搜索和网页抓取 |
| `retrieve_memory.py`, `save_memory.py` | 长期记忆读写 |
| `collaboration_tools.py`, `query_agent.py` | 多 Agent 协作、委派、询问 |
| `skill_manager.py` | Catown Skill 管理 |

工具边界清晰，但因为工具具有副作用，需要依赖审批、审计、权限策略来控制风险。

#### `backend/pipeline/`

| 文件 | 职责 |
|---|---|
| `config.py` | 从 Catown data root 读取 `pipelines.json` 等 Pipeline 模板定义 |
| `engine.py` | Pipeline 执行引擎，文件体量较大，是关键复杂点 |

Pipeline 模块承担阶段化自动交付流程，和 services 中 orchestration 模块存在密切关系。

#### `backend/models/`

| 文件 | 职责 |
|---|---|
| `database.py` | SQLAlchemy 数据模型定义，体量较大，承载聊天、消息、任务、审计、运行记录等持久化结构 |
| `audit.py` | 审计相关模型或辅助结构 |

当前数据库层集中在 `database.py`，短期简单，长期会成为模型膨胀点。

#### `backend/chatrooms/`

| 文件 | 职责 |
|---|---|
| `manager.py` | 聊天室管理，包括成员、消息、广播或房间状态管理 |

### 2.3 Frontend

前端技术栈来自 `frontend/package.json`：

- React 18
- TypeScript
- Vite
- Ant Design
- React Flow
- Monaco Editor
- Axios
- Zustand
- Vitest
- Playwright

主要结构：

| 路径 | 职责 |
|---|---|
| `frontend/src/App.tsx` | 主应用入口，当前文件体量较大，是前端主要耦合点 |
| `frontend/src/api/client.ts` | 后端 API Client 与类型引用 |
| `frontend/src/components/` | UI 组件 |
| `frontend/src/monitor.*` | 监控面板相关入口与样式 |
| `frontend/src/main.tsx` | React 启动入口 |
| `frontend/dist/` | 构建产物 |

前端当前是单应用结构，适合快速迭代。但 `App.tsx` 过大，后续需要按页面/业务域拆分。

### 2.4 Tests

| 文件 | 职责 |
|---|---|
| `tests/test_frontend.py` | 前端功能或页面行为测试 |
| `tests/test_integration_e2e.py` | 集成端到端测试 |
| `tests/test_visual_rendering.py` | 视觉渲染测试 |

测试偏 E2E/可视化方向，适合验证产品主流程。但核心 service 层单元测试仍需要加强。

## 3. 关键技术栈与运行边界

### 3.1 后端

| 类别 | 技术 |
|---|---|
| Web 框架 | FastAPI |
| ASGI Server | Uvicorn |
| 数据模型/API Schema | Pydantic / pydantic-settings |
| ORM | SQLAlchemy |
| 数据库 | SQLite/aiosqlite 线索明显，具体运行由配置决定 |
| 实时通信 | WebSocket、SSE |
| LLM SDK | OpenAI SDK >= 1.40 |
| 文件/环境配置 | `backend/config.py` 统一 runtime path/data root |
| 数据迁移 | Alembic |

### 3.2 前端

| 类别 | 技术 |
|---|---|
| 框架 | React 18 |
| 构建 | Vite |
| 语言 | TypeScript |
| UI | Ant Design |
| 图编排/流程图 | React Flow |
| 编辑器 | Monaco Editor |
| HTTP | Axios |
| 状态 | Zustand |
| 测试 | Vitest、Playwright |

### 3.3 部署

当前部署模式以单容器为主：

- `docker-compose.yml` 定义 `catown` 服务。
- 暴露 `${PORT:-8000}:8000`。
- 运行时环境通过环境变量控制，如 `HOST`, `PORT` 等。
- 前后端大概率在一个后端服务内统一托管或由容器构建阶段整合。

这是务实选择：对当前阶段而言，单容器比前后端/任务队列/数据库拆分更低成本。

## 4. 数据流与调用流

### 4.1 用户聊天/Agent Turn 流程

```text
用户输入
  ▼
Frontend App / API Client
  ▼
FastAPI routes/api.py 或 WebSocket
  ▼
Session / Orchestration Service
  ├─ 构造上下文: context_builder / chat_prompt_builder
  ├─ 加载 Agent 配置: agents/config_manager / registry
  ├─ 调用 LLM: stream_turn_executor 或 nonstream_turn_executor
  ├─ 处理工具调用: tools/* + tool_governance
  ├─ 写入状态: models/database.py / run_ledger
  └─ 发布事件: chat_publish / stream_transport / websocket
  ▼
Frontend 实时展示消息、工具结果、运行卡片和状态
```

### 4.2 Pipeline 运行流程

```text
创建或选择 Pipeline
  ▼
routes/pipeline.py
  ▼
pipeline/config.py 读取模板
  ▼
pipeline/engine.py 或 services/orchestration_*
  ▼
按阶段调度 Agent / 等待审批 / 执行工具
  ▼
写入消息、阶段状态、产物、运行账本
  ▼
monitor/audit API + WebSocket/SSE 对外展示
```

### 4.3 工具调用与审批流程

```text
LLM 产生 tool call
  ▼
Turn Executor 解析工具调用
  ▼
tool_governance 判断风险与权限
  ├─ 低风险：直接执行 tools/*
  └─ 高风险：进入 approval_queue
          ▼
      用户批准/拒绝
          ▼
      approval_replay 重放或终止
  ▼
工具结果写入 Turn 状态、审计、运行账本
  ▼
继续 LLM Turn 或结束
```

### 4.4 监控与审计流

```text
运行时事件 / LLM 调用 / 工具调用 / Token 用量
  ▼
run_ledger / audit models / runtime_event_helpers
  ▼
monitor_projection 汇总投影
  ▼
routes/monitor.py / routes/audit.py
  ▼
前端监控面板、时间线、运行卡片、审批队列
```

## 5. 现有架构优点

### 5.1 模块化单体，复杂度控制合理

当前项目没有过早拆成微服务。考虑到核心复杂度在 Agent 编排、上下文、工具调用和运行状态，模块化单体更容易调试和演进。

### 5.2 后端已经形成 service 层

`backend/services/` 拆分较细，说明项目已经意识到 routes 不应该承载全部业务逻辑。编排、会话、审批、监控投影、运行账本等都已独立成模块。

### 5.3 工具边界明确

工具统一放在 `backend/tools/`，并配合 `tool_governance.py`、`approval_queue.py` 处理风险控制。这对 Agent 系统非常重要，因为工具副作用远高于普通 API 调用。

### 5.4 有 ADR 文档沉淀

`docs/ADR-*` 表明项目已有架构决策记录习惯。对于多 Agent 平台，这比纯代码更重要，因为很多决策涉及产品形态和运行边界。

### 5.5 实时能力完整

项目同时具备 WebSocket、SSE、监控 API、审计 API，能支撑“软件工厂运行过程可观察”的产品目标。

### 5.6 测试方向贴近产品形态

E2E、前端、视觉渲染测试能覆盖用户实际体验，对这种交互复杂的平台比较有价值。

## 6. 风险、技术债与耦合点

### 6.1 大文件风险明显

已观察到以下文件体量较大：

- `backend/routes/api.py` 约 183KB
- `backend/pipeline/engine.py` 约 101KB
- `backend/models/database.py` 约 39KB
- `backend/services/context_builder.py` 约 42KB
- `backend/services/run_ledger.py` 约 34KB
- `frontend/src/App.tsx` 约 132KB

这些文件短期可以接受，但长期会带来：

- 修改影响面不清晰。
- Review 成本高。
- 单元测试困难。
- 新人理解成本高。
- 容易出现隐式耦合。

### 6.2 routes 层仍可能偏重

`api.py` 过大说明 API 层可能混有业务编排、数据转换、权限判断、运行时处理。建议继续把逻辑下沉到 service，routes 只保留参数解析、鉴权、调用 service、返回响应。

### 6.3 Pipeline Engine 和 Orchestration Services 边界需要持续澄清

当前同时存在：

- `backend/pipeline/engine.py`
- `backend/services/orchestration_*`

二者都与流程执行有关。需要明确：

- Pipeline 是否只是“模板/阶段定义 + 用户可见交付流程”？
- Orchestration 是否是“运行时调度内核”？
- Engine 是否应该逐步变薄，转为调用 orchestration runtime？

如果边界不清，后续会产生两套状态机。

### 6.4 数据模型集中，领域边界弱

`models/database.py` 集中定义大量表模型，短期简单，但长期会导致领域边界不明显。尤其聊天、审计、Pipeline、工具调用、运行账本如果全部混在一个文件，演进成本会增加。

### 6.5 前端主应用过大

`App.tsx` 约 132KB，说明页面状态、路由、组件组合、业务逻辑可能耦合在一起。后续新增监控、审批、Pipeline、设置、Agent 管理等功能时会更难维护。

### 6.6 高风险工具的安全边界需持续强化

当前已有工具治理和审批机制，但 Agent 工具包括：

- Shell
- 文件删除/写入
- GitHub 写操作
- 浏览器自动化
- Skill 安装

这些能力都可能产生真实副作用。必须持续保证：

- 审批不可绕过。
- 工具调用有完整审计。
- 工作区边界不能逃逸。
- 高风险工具默认最小权限。
- 审批后的 replay 行为可追踪、可中断。

### 6.7 SQLite/单进程运行的扩展边界

当前架构适合本地和单用户/小团队场景。若未来要支持多租户、大量并发 Pipeline、长时间后台任务，需要重新评估：

- SQLite 锁竞争。
- 长任务与 API 进程耦合。
- WebSocket/SSE 横向扩展。
- 任务恢复与幂等。
- 多实例下的运行账本一致性。

目前不建议马上引入复杂分布式架构，但要明确扩展边界。

## 7. 后续改进建议

### 7.1 优先级 P0：保持当前架构稳定

1. **不要拆微服务**  
   当前阶段微服务会增加部署、调试、事务和观测成本，不解决主要问题。

2. **保持模块化单体**  
   继续以 FastAPI 单体为核心，通过 service 层清理边界。

3. **明确运行时状态机**  
   Pipeline、Orchestration、Session 三者之间要有清晰职责说明，建议补一份 ADR。

### 7.2 优先级 P1：拆解高风险大文件

建议按以下顺序渐进拆分：

#### 后端

- `backend/routes/api.py`
  - 拆为 `routes/chats.py`
  - `routes/agents.py`
  - `routes/projects.py`
  - `routes/messages.py`
  - `routes/configs.py`

- `backend/pipeline/engine.py`
  - 拆出状态机定义
  - 拆出阶段执行器
  - 拆出审批/人工介入处理
  - 拆出产物收集

- `backend/models/database.py`
  - 拆为 `models/chat.py`
  - `models/agent.py`
  - `models/pipeline.py`
  - `models/audit.py`
  - `models/runtime.py`

#### 前端

- `frontend/src/App.tsx`
  - 拆页面：Chat、Monitor、Pipeline、Settings、Agents
  - 拆 hooks：useChatSession、useMonitorStream、usePipelineRun
  - 拆 service：API 调用和 UI 状态解耦

### 7.3 优先级 P1：补充架构边界文档

建议新增：

- `docs/ADR-xxx-runtime-state-machine.md`
- `docs/ADR-xxx-tool-governance.md`
- `docs/ADR-xxx-pipeline-orchestration-boundary.md`

重点解释：

- 什么是 Chat Session。
- 什么是 Pipeline Run。
- 什么是 Orchestration Step。
- Tool Call 的生命周期。
- Approval 的状态转移。

### 7.4 优先级 P2：加强 service 层单元测试

现有测试偏 E2E 和渲染。建议补充：

- `tool_governance` 审批判定测试。
- `approval_replay` 重放幂等测试。
- `context_builder` 上下文裁剪测试。
- `orchestration_scheduler` 调度规则测试。
- `run_ledger` 事件顺序和聚合测试。
- `pipeline engine` 状态流转测试。

### 7.5 优先级 P2：统一 API 响应与错误格式

建议逐步统一：

```json
{
  "success": false,
  "error": {
    "code": "TOOL_APPROVAL_REQUIRED",
    "message": "Tool call requires approval",
    "details": {}
  },
  "request_id": "..."
}
```

RESTful 路径建议保持版本化：

```text
/api/v1/chats
/api/v1/agents
/api/v1/pipelines
/api/v1/audit
/api/v1/monitor
```

如果现有接口已经广泛使用，不建议一次性破坏式改名，应通过兼容层渐进迁移。

### 7.6 优先级 P3：为未来扩展预留后台任务边界

当出现以下信号时，再考虑引入后台任务系统：

- Pipeline 经常运行数十分钟以上。
- 多个用户并发启动长任务。
- API 响应被长任务拖慢。
- 需要任务跨进程恢复。
- 需要水平扩展多个 worker。

可选演进路径：

```text
当前：FastAPI 单进程内运行
  ▼
下一步：进程内 task manager + 持久化 run state
  ▼
再下一步：独立 worker 进程
  ▼
必要时：Redis/RQ/Celery/Arq 等队列
```

不建议现在直接上复杂队列，除非已经有明确并发压力。

## 8. 架构结论

当前 Catown 的架构方向是合理的：

- 产品复杂度集中在 Agent 编排、工具调用、审批和运行可观察性。
- 采用 FastAPI + React 的模块化单体，可以快速迭代。
- 后端 service 层已经承担核心复杂度，没有完全堆在 API 层。
- 工具系统和审批机制是正确的架构重点。

当前最主要的问题不是技术选型，而是 **复杂度正在向少数大文件集中**。后续应避免引入不必要的新基础设施，优先通过拆分模块、明确边界、补充状态机文档和单元测试来提升可维护性。

推荐的短期架构策略：

```text
保持单体部署
  + 强化 service 层边界
  + 拆分大文件
  + 明确 Pipeline/Orchestration/Session 状态机
  + 加强工具治理和审计测试
```

这比过早引入微服务、消息队列、复杂权限平台更符合当前项目阶段。
