# Catown - AI 软件工厂

Catown 是一个面向软件交付的多 Agent 工作台：用户用自然语言描述需求，系统把需求分析、架构设计、开发、测试、发布和人工审批组织成可追踪的运行时流程。它的核心不是单次聊天，而是把聊天、项目、工作区、Agent 协作、工具调用、审批、审计和监控放进同一个可恢复、可观察的产品运行时。

这份 README 是给人和 Prompt 共用的一级入口。短上下文只读本文件；需要深入时，再按下面的文档地图渐进式加载 `docs/` 中的 PRD、ADR、Schema、Spec 和参考资料。

## 项目目标

- 用对话启动项目，并把需求沉淀为可执行的项目上下文、工作区文件和阶段产物。
- 让 `analyst`、`architect`、`developer`、`tester`、`release`、`valet` 等 Agent 在同一运行时中协作，而不是各自孤立地生成文本。
- 把语义判断留给 Prompt 和 Agent，把安全、审批、状态流转、产物契约、上下文压缩、监控审计等稳定规则落实到后端运行时。
- 让 BOSS/用户可以实时看到任务进展、Agent 消息、工具调用、审批队列、网络/LLM 调用和失败原因，并能在关键节点介入。
- 支持本地优先的开发体验：FastAPI 后端、React/Vite 前端、SQLite 本地状态、OpenAI 兼容 LLM、JSON 驱动配置和可扩展 Skills。

## 架构概览

Catown 当前是一个模块化单体，前端负责交互和运行时可视化，后端负责 Agent 编排、工具执行、状态持久化和运行时策略。

```text
User / BOSS
  -> Frontend (React + TypeScript + Vite)
  -> Backend API (FastAPI routes)
  -> Services (chat, project, runtime, audit, policy, memory)
  -> Runtime Kernel (pipeline, agents, tools, LLM client)
  -> Persistence (business DB + telemetry DB + workspace files)
  -> Monitor / Chat projections (SSE, WebSocket, polling, read models)
```

主要边界：

- `frontend/`：聊天主界面、项目浏览、配置界面、`/monitor` 监控面板和运行时状态展示。
- `backend/routes/`：HTTP/SSE/WebSocket API 边界，承接前端请求并调用 service 层。
- `backend/services/`：业务和运行时策略层，包括项目、聊天、审批、审计、上下文、监控、文件写入和工具执行策略。
- `backend/pipeline/`：Pipeline 执行引擎，负责多阶段软件交付流程的调度。
- `backend/agents/`：Agent 注册、角色、SOUL/prompt 装配、协作和运行上下文。
- `backend/tools/`：文件、Shell、代码执行、浏览器、搜索、协作、记忆等工具能力。
- `backend/models/`：SQLAlchemy 模型。业务状态和高频观测数据按当前 ADR 拆分为主状态库与 telemetry 写入路径。
- `backend/configs/` 与 `${CATOWN_HOME:-~/.catown}/config/`：Agent、Pipeline、Skills、授权和 UI 等运行时配置。
- `${CATOWN_HOME:-~/.catown}/projects` 与 `${CATOWN_HOME:-~/.catown}/workspaces`：项目产物与 Agent 工作区。

## Prompt 渐进式披露

当本仓库被作为 Prompt 上下文引用时，建议按任务风险逐层加载：

1. 项目方向和边界：只加载本 `README.md`。
2. 产品意图：再加载 [docs/02_PRD/PRD.md](docs/02_PRD/PRD.md)。
3. 当前架构：再加载 [docs/04_Spec/tech-spec.md](docs/04_Spec/tech-spec.md) 和 [docs/01_ADR/Architecture.md](docs/01_ADR/Architecture.md)。
4. 具体设计决策：按问题加载 [docs/01_ADR/](docs/01_ADR/) 中的对应 ADR。
5. 数据/协议契约：按接口加载 [docs/03_Schema/](docs/03_Schema/) 中的 Schema 文档。
6. 外部协议背景：需要 OpenAI 兼容协议时加载 [docs/05_Ref/OpenAi.md](docs/05_Ref/OpenAi.md)。

完整文档索引见 [docs/README.md](docs/README.md)。

## 设计文档地图

| 目录 | 作用 | 优先阅读 |
|------|------|----------|
| [docs/01_ADR/](docs/01_ADR/) | 架构决策、运行时边界、状态机、审批、安全、监控、上下文和长期演进记录 | [Architecture.md](docs/01_ADR/Architecture.md), [Business-Flow.md](docs/01_ADR/Business-Flow.md), [Session-Project-Flow.md](docs/01_ADR/Session-Project-Flow.md) |
| [docs/02_PRD/](docs/02_PRD/) | 产品需求、核心用户故事、Agent/Skills 体系和具体功能 PRD | [PRD.md](docs/02_PRD/PRD.md), [skills-prd.md](docs/02_PRD/skills-prd.md) |
| [docs/03_Schema/](docs/03_Schema/) | Action、Artifact、Workflow、Policy、Evaluation 等稳定协议对象 | [Schema-Workflow-Spec-v1.md](docs/03_Schema/Schema-Workflow-Spec-v1.md), [Schema-Action-Request-v1.md](docs/03_Schema/Schema-Action-Request-v1.md) |
| [docs/04_Spec/](docs/04_Spec/) | 当前代码架构分析、模块职责、数据流、风险和改进建议 | [tech-spec.md](docs/04_Spec/tech-spec.md) |
| [docs/05_Ref/](docs/05_Ref/) | 外部协议和背景材料 | [OpenAi.md](docs/05_Ref/OpenAi.md) |

高频 ADR 入口：

- [ADR-008: Skills 渐进式披露机制](docs/01_ADR/ADR-008-skills-progressive-disclosure.md)
- [ADR-015: Codex 风格运行时内核演进](docs/01_ADR/ADR-015-codex-style-runtime-evolution.md)
- [ADR-020: Prompt Guidance vs Runtime Contracts](docs/01_ADR/ADR-020-prompt-vs-runtime-contracts.md)
- [ADR-024: Configurable Tool Authorization Policy](docs/01_ADR/ADR-024-configurable-tool-authorization-policy.md)
- [ADR-030: Event-Sourced TaskRun State](docs/01_ADR/ADR-030-event-sourced-taskrun-state.md)
- [ADR-031: Telemetry Split DB and Serial Write Adapter](docs/01_ADR/ADR-031-telemetry-storage-and-serial-write-adapter.md)
- [ADR-032: Stable Public Identifiers and Recovery-Safe Identity Boundaries](docs/01_ADR/ADR-032-stable-public-identifiers-and-recovery-safe-identity.md)
- [ADR-033: Approval Notification Single Source of Truth](docs/01_ADR/ADR-033-approval-single-source.md)
- [ADR-034: Monitor/Network 页面性能问题根因分析与改进方案](docs/01_ADR/ADR-034-monitor-performance.md)

## 运行方式

环境要求：

- Python 3.10+，推荐 Python 3.12。
- Git。
- 一个 OpenAI 兼容的 LLM API，例如 OpenAI、DeepSeek、Ollama、vLLM 或兼容网关。

一键启动：

```bash
git clone https://github.com/uplusplus/catown.git
cd catown
./run.sh        # Linux / macOS / WSL
run.bat         # Windows
```

启动后：

- Web 主界面：http://localhost:8000
- Monitor 面板：http://localhost:8000/monitor
- FastAPI 文档：http://localhost:8000/docs

运行时配置通常位于 `${CATOWN_HOME:-~/.catown}`：

```text
~/.catown/
  .env
  config/
    agents.json
    pipelines.json
    skills.json
    skill_marketplaces.json
  state/
  projects/
  workspaces/
```

常用 LLM 环境变量：

```bash
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
```

## 开发与验证

后端测试：

```bash
cd backend
python -m pytest
```

前端构建：

```bash
cd frontend
npm run build
```

文档和架构类改动至少应确认相关 Markdown 链接仍能解析，并检查 `docs/README.md` 是否继续反映最新目录结构。
