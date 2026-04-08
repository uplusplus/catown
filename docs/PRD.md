# Catown 产品需求文档 (PRD)

**版本**: v1.0
**日期**: 2026-04-07
**状态**: 已确认
**作者**: BOSS

---

## 1. 产品概述

### 1.1 产品定位

**Catown** — AI 软件工厂。输入原始需求，输出可发布的产品。全流程自动化，人可在必要时介入。

### 1.2 目标用户

公司 BOSS / 技术管理者，管理多个 AI Agent 协作完成软件项目开发。

### 1.3 核心价值

- **自动化**：从原始需求到可发布产品，端到端自动化
- **可观测**：BOSS 能实时看到 Agent 在做什么、在讨论什么
- **可介入**：任意阶段可以暂停、审批、打回、直接发指令
- **可配置**：LLM 模型、Pipeline 流程、Agent 角色全部可配置

---

## 2. 竞品分析

| 能力 | OpenClaw | AutoGen / CrewAI | **Catown** |
|------|----------|-------------------|------------|
| Agent 间实时消息 | ❌ 无 | ⚠️ 有但重 | ✅ 轻量 + 可观测 |
| 软件开发流程 | ❌ 通用 | ⚠️ 需自己搭 | ✅ 内置流水线 |
| 人工介入 | ❌ 无概念 | ⚠️ 需编程 | ✅ Web UI 原生支持 |
| 产出物管理 | ❌ | ⚠️ 弱 | ✅ 项目 workspace |
| 部署复杂度 | 低 | 高 | 低（单进程 Docker） |
| 定位 | 个人 AI 助手 | 多 Agent 框架 | **AI 软件工厂** |

**关键差异**：
- **vs OpenClaw**：OpenClaw 不支持 Agent 间实时消息，极大限制了 Agent 交互
- **vs AutoGen/CrewAI**：这些方案偏重且通用，Catown 只聚焦软件开发流程

---

## 3. 用户故事

### 3.1 主流程

> **作为 BOSS**，我提交一段原始需求（如「做一个用户管理系统，支持注册登录、权限管理」），系统自动完成需求分析 → 架构设计 → 开发 → 测试 → 发布的全流程。

### 3.2 人工介入

> **作为 BOSS**，我可以在 Pipeline 运行时看到每个 Agent 的实时输出，在需求分析完成后审批是否进入架构设计，发现问题时可以打回重做。

### 3.3 Agent 协作

> **作为 Developer Agent**，在开发过程中遇到接口定义不清晰时，我可以直接给 Architect Agent 发消息询问，而不是等用户来转达。

### 3.4 配置管理

> **作为 BOSS**，我可以为不同 Agent 配置不同的 LLM 模型（分析用便宜模型，开发用强模型），也可以自定义 Pipeline 的阶段顺序。

---

## 4. Agent 角色体系

### 4.1 角色定义

Pipeline 由 5 个专业 Agent + 1 个人角色组成：

| # | 角色名称 | 角色 | 职责 | 输入 | 输出 |
|---|---------|------|------|------|------|
| 1 | `analyst` | 需求分析师 | 理解原始需求，输出结构化 PRD | 用户原始需求文本 | PRD.md |
| 2 | `architect` | 架构师 | 技术选型、架构设计、可行性评估 | PRD.md | tech-spec.md |
| 3 | `developer` | 开发工程师 | 编写代码、单元测试 | tech-spec.md | src/ 目录 |
| 4 | `tester` | 测试工程师 | 测试执行、bug 发现、报告 | src/ + PRD.md | test_report.md |
| 5 | `release` | 发布经理 | 版本管理、changelog、发布 | test_report.md + src/ | CHANGELOG.md, Git tag |
| — | PM (人) | BOSS | 审批、介入、打回、发指令 | 任意阶段 | 审批结果 / 指令 |

### 4.2 角色配置

每个 Agent 的配置存储在 `configs/agents.json`，包括：

| 配置项 | 说明 |
|--------|------|
| `role` | 角色名称 |
| `system_prompt` | 系统提示词，定义 Agent 行为 |
| `tools` | 可用工具列表 |
| `provider` | LLM 配置（baseUrl, apiKey, models） |
| `default_model` | 默认使用的模型 |

### 4.3 工具分配

| Agent | 可用工具 |
|-------|---------|
| analyst | web_search, retrieve_memory, read_file, write_file |
| architect | web_search, retrieve_memory, read_file, write_file |
| developer | web_search, retrieve_memory, read_file, write_file, list_files, execute_code, search_files |
| tester | retrieve_memory, read_file, execute_code, list_files, search_files |
| release | retrieve_memory, read_file, write_file, list_files, execute_code |

### 4.4 可扩展性

- 可在 `agents.json` 中新增 Agent 角色（如 `security_auditor`）
- 可在 `pipelines.json` 中引用新角色
- Agent 的 system_prompt 和工具配置完全独立

---

## 5. Pipeline 工作流引擎

### 5.1 概念模型

```
用户提交原始需求
        │
        ▼
   [Pipeline 启动]
        │
        ▼
  ┌─────────────┐     ┌──────────────┐     ┌─────────────┐
  │ Stage: 分析  │────▶│ Stage: 架构   │────▶│ Stage: 开发  │
  │ Agent: analyst│    │ Agent: architect│   │ Agent: developer│
  │ Gate: 人工审批│    │ Gate: 自动通过  │    │ Gate: 测试通过│
  └─────────────┘     └──────────────┘     └─────────────┘
                                                    │
                              ┌──────────────────────┘
                              ▼
                    ┌─────────────┐     ┌─────────────┐
                    │ Stage: 测试  │────▶│ Stage: 发布  │
                    │ Agent: tester│    │ Agent: release│
                    │ Gate: 自动   │     │ Gate: 人工审批│
                    └─────────────┘     └─────────────┘
```

### 5.2 核心概念

**Pipeline**：一个工作流定义，包含多个 Stage。每个项目关联一个 Pipeline。

**Stage**：流水线阶段。每个 Stage 绑定一个 Agent，定义产出物类型。

**Gate**：阶段间门禁，控制何时进入下一阶段：

| Gate 类型 | 行为 |
|-----------|------|
| `auto` | 上一阶段完成，自动进入下一阶段 |
| `manual` | 等待 BOSS 审批（approve / reject） |
| `condition` | 基于条件自动判定（如测试通过率 > 95%） |

**Artifact**：阶段产出物。存储在项目 workspace 中，记录文件路径和摘要。

**Workspace**：每个项目独立的文件目录，Agent 通过工具读写。

### 5.3 Pipeline 配置

配置文件：`configs/pipelines.json`

```json
{
  "default": {
    "name": "标准软件开发流水线",
    "description": "需求分析 → 架构设计 → 开发 → 测试 → 发布",
    "stages": [
      {
        "name": "analysis",
        "display_name": "需求分析",
        "agent": "analyst",
        "gate": "manual",
        "timeout_minutes": 30,
        "expected_artifacts": ["PRD.md"]
      },
      {
        "name": "architecture",
        "display_name": "架构设计",
        "agent": "architect",
        "gate": "auto",
        "timeout_minutes": 30,
        "expected_artifacts": ["tech-spec.md"]
      },
      {
        "name": "development",
        "display_name": "软件开发",
        "agent": "developer",
        "gate": "auto",
        "timeout_minutes": 60,
        "expected_artifacts": ["src/"]
      },
      {
        "name": "testing",
        "display_name": "测试",
        "agent": "tester",
        "gate": "auto",
        "timeout_minutes": 30,
        "expected_artifacts": ["test_report.md"]
      },
      {
        "name": "release",
        "display_name": "版本发布",
        "agent": "release",
        "gate": "manual",
        "timeout_minutes": 15,
        "expected_artifacts": ["CHANGELOG.md"]
      }
    ]
  }
}
```

### 5.4 Pipeline 状态机

**PipelineRun 状态**：

```
                  ┌────────────────────────────────┐
                  │                                │
                  ▼                                │
            ┌──────────┐     ┌──────────┐         │
────创建────▶│ PENDING  │────▶│ RUNNING  │────完成──▶ COMPLETED
            └──────────┘     └────┬─────┘         │
                                  │               │
                             暂停 │    失败        │
                                  ▼               │
                             ┌──────────┐         │
                             │ PAUSED   │─────────┘──▶ FAILED
                             └──────────┘
```

**Stage 状态**：

```
PENDING → RUNNING → COMPLETED
                  → BLOCKED (等待人工审批)
                  → FAILED
```

### 5.5 阶段流转逻辑

```
阶段 N 完成
    │
    ├── Gate = auto ──────────▶ 自动进入阶段 N+1
    │
    ├── Gate = manual ────────▶ Pipeline 暂停，等待 BOSS 审批
    │                               │
    │                          approve ──▶ 进入阶段 N+1
    │                          reject  ──▶ 打回阶段 N 重做
    │
    └── Gate = condition ─────▶ 引擎评估条件
                                   │
                              通过 ──▶ 进入阶段 N+1
                              不通过 ──▶ 打回阶段 N 重做
```

### 5.6 错误恢复

| 级别 | 触发条件 | 处理方式 |
|------|---------|---------|
| LLM 调用失败 | API 超时 / 429 / 500 | 自动重试 3 次，指数退避（1s, 2s, 4s） |
| 工具执行失败 | execute_code 报错 | 错误信息回传 LLM，让 Agent 自行修复（最多 3 轮） |
| 阶段失败 | Agent 反复失败超过上限 | Pipeline 暂停，通知 BOSS，等人工介入 |
| 超时 | 阶段运行超过 timeout_minutes | Pipeline 暂停，通知 BOSS |

---

## 6. 产出物管理

### 6.1 项目 Workspace

每个项目独立目录结构：

```
projects/
└── {project_id}/
    ├── .git/                    # Git 版本管理
    ├── PRD.md                   # Analyst 产出
    ├── tech-spec.md             # Architect 产出
    ├── src/                     # Developer 产出
    │   ├── main.py
    │   └── ...
    ├── tests/                   # Tester 产出
    │   ├── test_report.md
    │   └── ...
    ├── CHANGELOG.md             # Release 产出
    └── .catown/
        ├── pipeline.json        # Pipeline 运行状态
        └── stage_context/       # 各阶段上下文快照
```

### 6.2 阶段间上下文传递

当前一个 Agent 完成后，向下一个 Agent 传递：

```
[Stage output from: analyst]
Stage: analysis
Status: completed
Files created: PRD.md
Workspace: /path/to/projects/{id}/

Summary:
完成了用户管理模块的需求分析，包含 5 个用户故事和 12 条验收标准。
核心功能：用户注册/登录、角色权限管理、操作审计日志。

PRD.md 内容（截断）:
# 用户管理系统 PRD
## 1. 概述
...
```

Agent 通过 `read_file` 工具可以读取完整文件。

### 6.3 版本管理

- 每个 Stage 完成自动 Git commit（message: `[pipeline] stage: {stage_name} completed`）
- Release 阶段自动打 Git tag（`v1.0.0`）
- BOSS 可以查看各阶段的 diff

---

## 7. Agent 间实时消息

### 7.1 通信架构

```
Pipeline 运行时
    │
    ├── Developer 遇到接口问题
    │       │
    │       ├── 发消息给 Architect（Agent → Agent 消息）
    │       │       │
    │       │       └── Architect 回复澄清
    │       │
    │       └── WebSocket 广播到前端（BOSS 实时可见）
    │
    └── 继续开发
```

### 7.2 消息类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `STAGE_OUTPUT` | 阶段完成时的产出摘要 | Analyst → Pipeline Engine |
| `AGENT_QUESTION` | Agent 向另一 Agent 提问 | Developer → Architect |
| `AGENT_REPLY` | Agent 回答另一个 Agent | Architect → Developer |
| `HUMAN指令` | BOSS 发给 Agent 的指令 | PM → Developer |
| `STATUS_UPDATE` | Agent 状态更新 | Developer → Pipeline Engine |

### 7.3 持久化

Agent 间协作消息持久化到 `pipeline_messages` 表，BOSS 可事后回顾。

---

## 8. 监控与人工介入

### 8.1 Pipeline Dashboard

```
┌─────────────────────────────────────────────────────────────┐
│  Catown Pipeline Dashboard                                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  📋 项目: 用户管理系统                                       │
│  状态: ██████████░░░░ 开发中 (Stage 3/5)                     │
│  已用时间: 2h 15m                                            │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ ✅ 分析   │──│ ✅ 架构   │──│ 🔄 开发   │──│ ⏳ 测试   │    │
│  │ 25min    │  │ 18min    │  │ 进行中... │  │ 等待中   │    │
│  │ PRD.md   │  │ tech-spec│  │ src/     │  │          │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                              │
│  💬 Agent 实时通信:                                          │
│  ┌────────────────────────────────────────────────────┐     │
│  │ [developer] 接口 /api/users 的认证方式需要确认       │     │
│  │ [architect] 用 JWT，schema 在 tech-spec.md 第3节    │     │
│  │ [developer] 收到，继续实现                           │     │
│  └────────────────────────────────────────────────────┘     │
│                                                              │
│  📦 产出物:                                                  │
│  ├── PRD.md (12KB, analyst, 2h15m前)                        │
│  ├── tech-spec.md (8KB, architect, 1h57m前)                 │
│  ├── src/main.py (3KB, developer, 12m前)                    │
│  └── src/models.py (2KB, developer, 8m前)                   │
│                                                              │
│  ⚡ 操作:                                                    │
│  [⏸ 暂停] [⏪ 打回上一阶段] [💬 发指令给 Agent] [▶️ 继续]    │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 人工介入操作

| 操作 | 说明 | API |
|------|------|-----|
| 观察 | 实时看 Agent 对话、看产出物、看进度 | WebSocket + GET |
| 暂停 | 暂停整个 Pipeline | `POST /api/pipelines/{id}/pause` |
| 继续 | 恢复暂停的 Pipeline | `POST /api/pipelines/{id}/resume` |
| 审批 | Gate=manual 时 approve/reject | `POST /api/pipelines/{id}/approve` |
| 打回 | 打回某个阶段重做 | `POST /api/pipelines/{id}/rollback` |
| 发指令 | 直接给指定 Agent 发消息 | `POST /api/pipelines/{id}/instruct` |
| 修改产出物 | 直接编辑 Agent 生成的文件 | 文件编辑器 |

---

## 9. LLM 配置

### 9.1 配置能力

✅ **已实现**。唯一配置源：`configs/agents.json`，两级配置架构。

**配置优先级**：Agent 自身 provider → global_llm provider → 环境变量

| 配置项 | 级别 | 说明 |
|--------|------|------|
| `global_llm.provider.baseUrl` | 全局 | 所有未配置 Agent 的默认 LLM 服务 |
| `global_llm.provider.apiKey` | 全局 | 默认 API Key |
| `global_llm.default_model` | 全局 | 默认模型 |
| `agent.provider.baseUrl` | per-Agent | 该 Agent 专用 LLM 服务（覆盖全局） |
| `agent.provider.apiKey` | per-Agent | 独立 API Key（覆盖全局） |
| `agent.default_model` | per-Agent | 指定默认模型（覆盖全局） |

### 9.2 分级策略建议

| Agent | 建议模型等级 | 原因 |
|-------|------------|------|
| analyst | 中等 | 理解需求 + 结构化输出 |
| architect | 强 | 需要深度推理和权衡 |
| developer | 强 | 代码质量直接影响产出 |
| tester | 中等 | 执行测试 + 生成报告 |
| release | 弱 | 主要是机械性操作 |

> 以上仅为建议，实际由 `agents.json` 决定，随时可调。

### 9.3 运行时热加载

修改 `agents.json` 后调用 `POST /api/config/reload`，无需重启服务。

---

## 10. 数据模型

### 10.1 新增表

**pipelines** — Pipeline 定义

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| project_id | INTEGER FK | 关联项目 |
| pipeline_name | TEXT | 使用的 pipeline 模板名 |
| status | TEXT | pending / running / paused / completed / failed |
| current_stage_index | INTEGER | 当前阶段索引 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 最后更新时间 |

**pipeline_runs** — Pipeline 运行实例

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| pipeline_id | INTEGER FK | 关联 Pipeline |
| run_number | INTEGER | 第几次运行（支持重跑） |
| status | TEXT | pending / running / paused / completed / failed |
| input_requirement | TEXT | 用户原始需求 |
| started_at | DATETIME | 开始时间 |
| completed_at | DATETIME | 完成时间 |

**pipeline_stages** — 阶段记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| run_id | INTEGER FK | 关联 PipelineRun |
| stage_name | TEXT | 阶段名称 |
| stage_order | INTEGER | 阶段顺序 |
| agent_name | TEXT | 执行 Agent |
| status | TEXT | pending / running / blocked / completed / failed |
| gate_type | TEXT | auto / manual / condition |
| input_context | TEXT | 传入的上下文（JSON） |
| output_summary | TEXT | 产出摘要 |
| started_at | DATETIME | 开始时间 |
| completed_at | DATETIME | 完成时间 |
| error_message | TEXT | 错误信息 |

**stage_artifacts** — 产出物记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| stage_id | INTEGER FK | 关联 PipelineStage |
| artifact_type | TEXT | file / directory |
| file_path | TEXT | workspace 中的相对路径 |
| summary | TEXT | 内容摘要 |
| created_at | DATETIME | 创建时间 |

**pipeline_messages** — Agent 间协作消息

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| run_id | INTEGER FK | 关联 PipelineRun |
| stage_id | INTEGER FK | 关联当前阶段 |
| message_type | TEXT | STAGE_OUTPUT / AGENT_QUESTION / AGENT_REPLY / HUMAN_INSTRUCT |
| from_agent | TEXT | 发送方 Agent 名称 |
| to_agent | TEXT | 接收方 Agent 名称（NULL=广播） |
| content | TEXT | 消息内容 |
| created_at | DATETIME | 创建时间 |

### 10.2 保留现有表

`agents`, `projects`, `chatrooms`, `messages`, `memories`, `agent_assignments` 保持不变。

---

## 11. API 设计

### 11.1 Pipeline 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/pipelines` | 创建 Pipeline（关联项目 + 选择模板） |
| GET | `/api/pipelines` | 列出所有 Pipeline |
| GET | `/api/pipelines/{id}` | 获取 Pipeline 详情 |
| POST | `/api/pipelines/{id}/start` | 启动 Pipeline（传入原始需求） |
| POST | `/api/pipelines/{id}/pause` | 暂停 Pipeline |
| POST | `/api/pipelines/{id}/resume` | 恢复 Pipeline |
| POST | `/api/pipelines/{id}/approve` | 审批通过当前 Gate |
| POST | `/api/pipelines/{id}/reject` | 拒绝当前 Gate（打回重做） |
| POST | `/api/pipelines/{id}/rollback` | 打回到指定阶段 |
| POST | `/api/pipelines/{id}/instruct` | 给指定 Agent 发指令 |
| GET | `/api/pipelines/{id}/messages` | 获取 Agent 协作消息 |
| GET | `/api/pipelines/{id}/artifacts` | 获取产出物列表 |

### 11.2 配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取配置（agents.json） |
| POST | `/api/config/reload` | 热加载 agents.json |
| POST | `/api/config/test` | 测试指定 Agent 的 LLM 连接 |

---

## 12. 技术架构

### 12.1 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 后端 | Python 3.10+ / FastAPI | 异步 Web 框架 |
| LLM | OpenAI 兼容接口 | agents.json per-agent 配置 |
| 数据库 | SQLite（可扩展 PostgreSQL） | SQLAlchemy ORM |
| 前端 | Vanilla JS 单文件 + TailwindCSS | 不做 React 重写 |
| 实时通信 | WebSocket + SSE | Agent 消息 + 流式输出 |
| 部署 | Docker + docker-compose | 单进程 |

### 12.2 新增模块

```
backend/
├── pipeline/
│   ├── __init__.py        ✅
│   ├── config.py          ✅  Pipeline 配置加载
│   ├── engine.py          ⏳  Pipeline 引擎（核心）
│   └── models.py          ⏳  Pipeline 数据模型（如需要独立）
├── routes/
│   └── pipeline.py        ⏳  Pipeline API 路由
├── configs/
│   ├── agents.json        ✅  5 个角色，独立 LLM 配置
│   └── pipelines.json     ✅  默认 5 阶段流水线模板
└── models/
    └── database.py        ✅  新增 5 张 Pipeline 表
```

### 12.3 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Pipeline 配置 | 可配置（pipelines.json） | 不同项目需要不同流程 |
| 产出物传递 | Workspace + 文本摘要 | Agent 读写文件，引擎注入摘要 |
| 并发 | 单项目串行 | 第一版不处理并发，够用 |
| 错误恢复 | 重试 → 暂停等人工 | 简单可靠 |
| 代码沙箱 | 基础隔离（超时+禁import） | 第一版不上 Docker-in-Docker |
| 前端 | 继续 Vanilla JS | 不做 React 重写，Pipeline Dashboard 直接加 |

---

## 13. 实施计划

### 项目进度

| 阶段 | 状态 | 完成日期 |
|------|------|---------|
| P0 — 数据模型与配置 | ✅ 已完成 | 2026-04-07 |
| P1 — 引擎与 API | ✅ 已完成 | 2026-04-07 |
| P1 — 前端 Dashboard | ✅ 已完成 | 2026-04-07 |
| P2 — 增强 | ✅ 已完成 | 2026-04-07 |
| P3 — 扩展 | ✅ 已完成 | 2026-04-07 |
| Bug Fix — 测试修复 | ✅ 已完成 | 2026-04-08 |
| 补全 — 协作+搜索 | ✅ 已完成 | 2026-04-08 |
| 两级 LLM 配置 | ✅ 已完成 | 2026-04-08 |
| 测试修复 (agent 名称) | ✅ 已完成 | 2026-04-08 |
| Bug Fix — 配置路径修复 | ✅ 已完成 | 2026-04-08 |

### Phase 1: P0 — 数据模型与配置 ✅ 已完成

| # | 任务 | 交付物 | 状态 | 提交 |
|---|------|--------|------|------|
| 1 | Pipeline 数据模型 | `models/database.py` 新增 5 张表 | ✅ | a7d394b |
| 2 | Pipeline 配置 + 加载器 | `configs/pipelines.json` + `pipeline/config.py` | ✅ | a7d394b |
| 3 | Agent 角色重写（独立 LLM） | `configs/agents.json` 5 个角色 | ✅ | a7d394b |

**交付物详情：**

- `pipelines` 表 — Pipeline 定义，关联项目
- `pipeline_runs` 表 — 运行实例，支持重跑
- `pipeline_stages` 表 — 阶段记录（状态、Agent、Gate、上下文、重试次数）
- `stage_artifacts` 表 — 产出物（文件路径、摘要）
- `pipeline_messages` 表 — Agent 间协作消息
- `configs/pipelines.json` — 默认 5 阶段流水线模板（含打回配置）
- `pipeline/config.py` — 配置加载器（阶段查询、打回目标查询）
- `configs/agents.json` — 5 个 Pipeline 角色，每个独立 provider + models 配置

### Phase 2: P1 — 引擎与 API

| # | 任务 | 交付物 | 状态 |
|---|------|--------|------|
| 4 | Pipeline 引擎 | `pipeline/engine.py` 阶段流转 + Gate + 错误恢复 | ✅ |
| 5 | Pipeline API | `routes/pipeline.py` REST 接口 | ✅ |
| 6 | Agent 间消息 | 消息持久化 + WebSocket 广播 | ✅ |

### Phase 3: P1 — 前端

| # | 任务 | 交付物 | 状态 |
|---|------|--------|------|
| 7 | Pipeline Dashboard | `frontend/index.html` 新增 Pipeline 页面 | ✅ |

### Phase 4: P2 — 增强

| # | 任务 | 交付物 | 状态 |
|---|------|--------|------|
| 8 | Git 集成 | 阶段完成自动 commit | ✅ |
| 9 | 产出物查看器 | Web UI 查看/编辑产出文件 | ✅ |

### Phase 5: P3 — 扩展

| # | 任务 | 交付物 | 状态 |
|---|------|--------|------|
| 10 | 多项目并行 | Pipeline 并发执行 | ✅ |
| 11 | 测试覆盖 | Pipeline + 协作 + Dashboard 测试 | ✅ |

### Phase 6: Bug Fix — 测试修复 ✅ 已完成

**问题**: LLM 配置重构为 per-agent 模式后，28 个测试用例因 API 变更而失败。

| # | 问题 | 修复 | 状态 | 提交 |
|---|------|------|------|------|
| 1 | `settings` 未导入 | `routes/api.py` 添加 `from config import settings` | ✅ | 1e02901 |
| 2 | `LLMConfigModel` 缺失 | 新增 Pydantic 验证模型（api_key/url/temperature/max_tokens） | ✅ | 1e02901 |
| 3 | `LLMClient()` 无参构造失败 | 支持环境变量回退（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL） | ✅ | 1e02901 |
| 4 | `set_llm_client()` 缺失 | 新增全局客户端注入函数（测试兼容） | ✅ | 1e02901 |
| 5 | `GET /api/config` 缺少 `llm` 字段 | 响应增加 LLM 配置摘要 | ✅ | 1e02901 |
| 6 | `POST /api/config` 端点缺失 | 新增配置验证端点 | ✅ | 1e02901 |

**测试结果**: 216/216 PASSED (从 188 passed / 28 failed 提升)

### Phase 7: 补全 — Agent 协作 + Web 搜索 ✅ 已完成

**问题**: 代码中存在 3 处未实现的功能（TODO / placeholder）。

| # | 问题 | 修复 | 状态 | 提交 |
|---|------|------|------|------|
| 1 | `chatrooms/manager.py` process_user_message 无协作逻辑 | 实现完整 Agent 路由：@mention 解析、多 Agent 协作、LLM 调用 | ✅ | |
| 2 | `pipeline/engine.py` web_search placeholder | 接入 DuckDuckGo Instant Answer API | ✅ | |
| 3 | `pipeline/engine.py` send_message placeholder | 注释澄清（实际已有 _handle_send_message 实现） | ✅ | |

**测试结果**: 216/216 PASSED

### Phase 8: 两级 LLM 配置 ✅ 已完成

**需求**: Agent 级 LLM 配置 + 全局 fallback，两级都要有 Web UI 配置界面。

| # | 改动 | 文件 | 状态 |
|---|------|------|------|
| 1 | agents.json 新增 `global_llm` 段 | `configs/agents.json` | ✅ |
| 2 | LLM client 两级查找：Agent → global_llm → 环境变量 | `llm/client.py` | ✅ |
| 3 | API: `PUT /config/global` + `PUT /config/agent/{name}` | `routes/api.py` | ✅ |
| 4 | API: `GET /config` 返回 `source: agent|global` | `routes/api.py` | ✅ |
| 5 | 前端: 全局配置编辑 + 保存 + 测试连接 | `frontend/index.html` | ✅ |
| 6 | 前端: 每个 Agent 独立编辑 + "Use Global" 一键清除 | `frontend/index.html` | ✅ |
| 7 | 前端: 运行时生效摘要（标注来源 agent/global） | `frontend/index.html` | ✅ |
| 8 | 单元测试: LLM 两级 fallback 逻辑 (8 cases) | `tests/test_llm_two_level_config.py` | ✅ |
| 9 | API 测试: config 端点 CRUD + roundtrip (7 cases) | `tests/test_llm_two_level_config.py` | ✅ |
| 10 | E2E 测试: Config UI 交互 (5 cases) | `tests/test_e2e_playwright.py` | ✅ |

**配置优先级**: Agent 自身 provider → global_llm provider → 环境变量

**测试结果**: 233/233 PASSED (新增 17 个用例)

### Phase 9: 测试修复 (agent 名称映射) ✅ 已完成

**问题**: agents.json 重构为 Pipeline 角色（analyst/architect/developer/tester/release）后，`test_api_routes.py` 中 10 个测试仍引用旧角色名（assistant/coder/reviewer/researcher），导致全部失败。

| # | 问题 | 修复 | 状态 | 提交 |
|---|------|------|------|------|
| 1 | test_list_agents 断言旧角色名 | 更新为 analyst/architect/developer/tester | ✅ | 7abdb5a |
| 2 | 项目创建测试引用旧 agent_names | 全部替换为 Pipeline 角色名 | ✅ | 7abdb5a |

**测试结果**: 233/233 PASSED

### Phase 10: Bug Fix — Agent 配置路径修复 ✅ 已完成

**问题**: `registry.py` 中 `get_builtin_agent_configs()` 使用相对路径 `"configs/agents.json"` 查找配置文件，当 CWD 不是 backend 目录时（如从项目根目录运行 pytest），找不到配置文件，回退到硬编码的旧默认 agent 名（assistant/coder/reviewer/researcher），导致 10 个测试失败。

| # | 问题 | 修复 | 状态 | 提交 |
|---|------|------|------|------|
| 1 | `configs/agents.json` 相对路径解析失败 | 改为基于 `__file__` 解析 backend 目录绝对路径 | ✅ | — |

**测试结果**: 233/233 PASSED (修复后从 223 passed / 10 failed 恢复)

---

## 14. 验收标准

### 14.1 功能验收

- [x] 提交原始需求后，Pipeline 自动执行 5 个阶段
- [x] 每个阶段的 Agent 使用正确的 system_prompt 和工具
- [x] 阶段产出物保存到 workspace，下一阶段能读取
- [x] Gate=manual 的阶段暂停等待人工审批
- [x] BOSS 可以通过 Web UI 暂停/继续/打回/审批
- [x] Agent 间可以互相发消息，BOSS 能实时看到
- [x] 每个 Agent 的 LLM 模型独立配置，来源 agents.json
- [x] 错误自动重试，超过阈值暂停等人工

### 14.2 技术验收

- [x] 所有配置来源 agents.json（无 .env LLM 依赖）
- [x] Pipeline 状态持久化到数据库
- [x] Agent 协作消息持久化到数据库
- [x] WebSocket 实时推送 Pipeline 状态变更
- [ ] Docker 部署正常

---

## 附录

### A. 与现有代码的关系

| 现有模块 | Pipeline 中的用途 | 改动量 | 状态 |
|---------|-------------------|--------|------|
| `config.py` | 已去掉 LLM 配置，仅保留基础设施 | — | ✅ 完成 |
| `llm/client.py` | 已改为 per-agent 客户端工厂 | — | ✅ 完成 |
| `models/database.py` | 新增 5 张 Pipeline 表 | 中 | ✅ 完成 |
| `configs/agents.json` | 改为 5 个 Pipeline 角色 | — | ✅ 完成 |
| `configs/pipelines.json` | 新增，默认 5 阶段模板 | — | ✅ 完成 |
| `pipeline/config.py` | 新增，配置加载器 | — | ✅ 完成 |
| `pipeline/engine.py` | 新增，Pipeline 引擎核心 | 大 | ✅ 完成 |
| `routes/pipeline.py` | 新增，Pipeline API 路由 | 中 | ✅ 完成 |
| `agents/collaboration.py` | Agent 间消息路由，接入 Pipeline | 中 | ✅ 完成 |
| `agents/registry.py` | 注册新角色（已由 agents.json 自动加载） | 小 | ✅ 完成 |
| `agents/core.py` | Agent 基类不变 | 无 | — |
| `routes/api.py` | 保留现有 API，新增 pipeline 路由 | 小 | ✅ 完成 |
| `frontend/index.html` | 新增 Pipeline Dashboard section | 中 | ✅ 完成 |

### B. 参考文档

- [PRD_ANALYSIS.md](./PRD_ANALYSIS.md) — 详细分析与讨论记录
- [TECHNICAL_FEASIBILITY.md](./TECHNICAL_FEASIBILITY.md) — 技术可行性分析
- [PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md) — 现有功能总结
