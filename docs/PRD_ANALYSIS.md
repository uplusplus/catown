# Catown PRD 分析与产品重定位

**日期**: 2026-04-07
**版本**: v1.0
**状态**: 讨论稿

---

## 1. 项目现状总结

### 1.1 已实现功能

| 功能模块 | 描述 | 完成度 |
|---------|------|--------|
| 多 Agent 系统 | 4 个内置角色（assistant / coder / reviewer / researcher），支持自定义 | ✅ |
| 聊天室 | 项目级聊天室，用户发消息，Agent 自动协作回复 | ✅ |
| 工具调用 | 14 个工具，多轮调用循环（最多 5 轮） | ✅ |
| 记忆系统 | 短期 / 长期 / 程序性三层记忆，LLM 辅助提取 | ✅ |
| 流式输出 | SSE 打字机效果 + WebSocket 实时通信 | ✅ |
| Web 界面 | Dashboard / Chat / Agents / Status / Config 五页 | ✅ |
| 多 Provider | 支持任何 OpenAI 兼容 API，per-agent 模型配置 | ✅ |
| Docker 部署 | docker-compose + PostgreSQL 扩展 | ✅ |

### 1.2 现有协作机制

- **CollaborationCoordinator**：Agent 间消息路由中心
- **AgentCollaborator**：每个 Agent 的消息收发 + 任务委托
- **策略模式**：SingleAgentStrategy / MultiAgentStrategy
- **消息类型**：TASK_REQUEST / TASK_RESPONSE / BROADCAST / DIRECT / STATUS_UPDATE / COORDINATION

### 1.3 技术债务

| 编号 | 问题 | 严重程度 |
|------|------|---------|
| TD-1 | `declarative_base()` 旧式写法 | 低 |
| TD-2 | 前端单文件 ~2000 行，无组件化 | 中 |
| TD-3 | PostgreSQL engine 创建硬编码 `sqlite:///` | 中 |
| TD-4 | `execute_code` 沙箱隔离不足 | 高 |
| TD-5 | 速率限制基于内存，重启丢失 | 低 |
| TD-6 | Config 面板并发写 .env 可能冲突 | 中 |
| TD-7 | Vite/React 重写残留未清理 | 低 |
| TD-8 | 协作消息队列内存实现，重启丢失 | 中 |
| TD-9 | `process_user_message` 协作路由未完善 | 低 |
| TD-10 | 记忆提取依赖 LLM，有延迟和成本 | 低 |
| TD-11 | 无 CI/CD 集成 | 中 |

---

## 2. 产品目标重新定义

### 2.1 核心愿景

> **一个 AI 软件工厂：输入原始需求，输出可发布的产品。全流程自动化，人可在必要时介入。**

### 2.2 目标用户

公司 BOSS / 技术管理者，需要管理多个 AI Agent 协作完成软件项目开发。

### 2.3 核心需求

1. **多 Agent 管理**：作为 BOSS 管理多个专精 Agent，用于软件项目开发
2. **全流程自动化**：原始需求 → 需求分析 → 技术可行性 & 架构设计 → 软件开发 → 测试 → 版本发布
3. **Agent 间实时通信**：Agent 之间可以直接对话协作，而非仅通过用户中转
4. **人在回路**：自动化主导，但人可以在必要时介入、审批、观察系统运行状态

### 2.4 竞品分析

| 能力 | OpenClaw | AutoGen / CrewAI | **Catown (目标)** |
|------|----------|-------------------|-------------------|
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

## 3. 产品重定位：Agent 角色体系

不再是通用的 assistant/coder/reviewer，而是**软件流水线工位**：

### 3.1 流水线角色

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐
│  需求分析师   │───▶│  架构师/可行性 │───▶│   开发工程师   │───▶│  测试工程师   │───▶│  发布经理  │
│  Analyst     │    │  Architect   │    │  Developer   │    │  Tester     │    │  Release  │
└─────────────┘    └──────────────┘    └─────────────┘    └─────────────┘    └──────────┘
       ▲                  ▲                   ▲                  ▲                 ▲
       │                  │                   │                  │                 │
       └──────── 人可以在任意节点介入、审批、修改 ─────────────────────────────────┘
```

### 3.2 角色职责定义

| Agent | 输入 | 输出 | 工具 |
|-------|------|------|------|
| **Analyst** | 原始需求文本 | 结构化 PRD（用户故事、验收标准、优先级） | web_search, retrieve_memory |
| **Architect** | PRD | 技术方案（架构图、技术选型、接口设计、可行性评估） | web_search, read_file, retrieve_memory |
| **Developer** | 技术方案 | 代码 + 单元测试 | execute_code, read_file, write_file, list_files |
| **Tester** | 代码 + PRD | 测试报告 + bug 列表 | execute_code, read_file, write_file |
| **Release** | 测试通过的代码 | 版本 tag + changelog + 部署产物 | execute_code, write_file, list_files |
| **PM (人)** | 任意阶段 | 审批、修改、回退 | 通过 Web UI 操作 |

---

## 4. Pipeline 工作流引擎（核心新增）

### 4.1 概念模型

```
用户提交原始需求
        │
        ▼
   [Pipeline 启动]
        │
        ▼
  ┌─────────────┐     ┌──────────────┐     ┌─────────────┐
  │ Stage: 分析  │────▶│ Stage: 架构   │────▶│ Stage: 开发  │
  │ Agent: Analyst│    │ Agent: Architect│   │ Agent: Developer│
  │ Gate: 人工审批│    │ Gate: 自动通过  │    │ Gate: 测试通过│
  └─────────────┘     └──────────────┘     └─────────────┘
                                                    │
                              ┌──────────────────────┘
                              ▼
                    ┌─────────────┐     ┌─────────────┐
                    │ Stage: 测试  │────▶│ Stage: 发布  │
                    │ Agent: Tester│    │ Agent: Release│
                    │ Gate: 自动   │     │ Gate: 人工审批│
                    └─────────────┘     └─────────────┘
```

### 4.2 关键设计要素

- **Stage**：流水线阶段，每个阶段绑定一个 Agent
- **Gate**：阶段间的人工/自动门禁
  - `auto`：上一阶段完成自动进入
  - `manual`：等待 Boss 审批
  - `condition`：基于条件自动判定（如测试通过率 > 95%）
- **Artifact**：每个阶段的产出物（文档、代码、测试报告），存入项目 workspace
- **Context 传递**：上一阶段的产出作为下一阶段的输入上下文
- **回退**：任意阶段可以打回上一阶段重做

### 4.3 Pipeline 状态机

```
          ┌──────────────────────────────────────┐
          │                                      │
          ▼                                      │
    ┌──────────┐    ┌──────────┐    ┌──────────┐│   ┌──────────┐
───▶│ PENDING  │───▶│ RUNNING  │───▶│COMPLETED │├──▶│  FAILED  │
    └──────────┘    └────┬─────┘    └──────────┘│   └──────────┘
                         │                      │
                         ▼                      │
                    ┌──────────┐                │
                    │ PAUSED   │────────────────┘
                    │ (人工介入)│
                    └──────────┘
```

Stage 状态：
- `pending` → `running` → `completed` / `failed` / `blocked`（等待人工审批）

---

## 5. Agent 间实时消息系统

### 5.1 当前问题

- 消息队列是内存的（`asyncio.Queue`），重启丢失
- 协作只在聊天室场景触发，Pipeline 场景没接入
- Agent 间的消息前端不可见

### 5.2 改进方案

```
Pipeline 运行时
    │
    ├── Developer 遇到接口问题
    │       │
    │       ├── 发消息给 Architect（Agent → Agent 实时消息）
    │       │       │
    │       │       └── Architect 回复澄清
    │       │
    │       └── WebSocket 广播到前端（Boss 能看到 Agent 讨论）
    │
    └── 继续开发
```

### 5.3 实现路径

1. Pipeline 阶段内的 Agent 间消息走 `CollaborationCoordinator.route_message()`
2. 所有 Agent 消息通过 WebSocket 广播到前端「流水线监控」页面
3. 消息持久化到 DB（当前只存 chatroom 消息，Agent 间协作消息需新增表）
4. 支持跨阶段 Agent 通信（如 Developer 向 Analyst 确认需求细节）

### 5.4 数据模型扩展

新增 `pipeline_messages` 表：
- id, pipeline_id, stage_id, from_agent, to_agent, content, message_type, created_at

---

## 6. 监控与人工介入

### 6.1 Pipeline Dashboard（Boss 视角）

```
┌─────────────────────────────────────────────────────────────┐
│  Catown Pipeline Dashboard                                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  📋 项目: 用户管理系统 v2.0                                   │
│  状态: ██████████░░░░ 开发中 (Stage 3/5)                     │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ ✅ 分析   │──│ ✅ 架构   │──│ 🔄 开发   │──│ ⏳ 测试   │    │
│  │ 2h前完成  │  │ 1h前完成  │  │ 进行中... │  │ 等待中   │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                              │
│  💬 Agent 实时通信:                                          │
│  ┌────────────────────────────────────────────────────┐     │
│  │ [Developer] 接口 /api/users 的认证方式需要确认       │     │
│  │ [Architect] 用 JWT，schema 在 tech-spec.md 第3节    │     │
│  │ [Developer] 收到，继续实现                           │     │
│  └────────────────────────────────────────────────────┘     │
│                                                              │
│  📦 产出物:                                                  │
│  ├── PRD.md (需求分析师)                                     │
│  ├── tech-spec.md (架构师)                                   │
│  ├── src/ (开发中)                                           │
│                                                              │
│  [⏸ 暂停] [⏪ 打回上一阶段] [💬 插入指令] [▶️ 继续]          │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 人工介入能力

| 操作 | 说明 | 触发方式 |
|------|------|---------|
| 观察 | 看 Agent 实时对话、看产出物、看进度 | Pipeline Dashboard |
| 暂停 | 暂停整个 Pipeline | 按钮 / API |
| 打回 | 打回某个阶段重做 | 按钮 / API |
| 插入指令 | 直接发消息给任意 Agent | 聊天输入框 |
| 审批 | 在 Gate 节点 approve / reject | 按钮 / API |
| 修改产出物 | 直接编辑 Agent 生成的文件 | 文件编辑器 |

---

## 7. 产出物管理

### 7.1 项目 Workspace

每个项目有一个独立的 workspace 目录：

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
    │   └── test_report.md
    ├── CHANGELOG.md             # Release 产出
    └── .catown/
        ├── pipeline.json        # Pipeline 状态
        └── stage_outputs/       # 各阶段中间产物
```

### 7.2 版本管理

- 每个 Stage 完成自动 commit（带 stage tag）
- Release 阶段自动打 Git tag
- 支持 diff 查看各阶段变更

---

## 8. LLM 策略

### 8.1 成本考量

一次完整 Pipeline 预估：

| 阶段 | 预估 LLM 调用次数 | 说明 |
|------|------------------|------|
| 分析 | 3-5 次 | 需求理解 + PRD 生成 |
| 架构 | 5-8 次 | 技术调研 + 方案设计 |
| 开发 | 10-30 次 | 编码 + 调试 + 代码审查 |
| 测试 | 5-10 次 | 测试用例生成 + 执行 + 报告 |
| 发布 | 2-3 次 | changelog + 版本管理 |
| **总计** | **25-56 次** | 按 GPT-4 约 $2-10/次完整流程 |

### 8.2 模型分级策略

Catown 已支持 per-agent 模型配置，建议：

| Agent | 推荐模型 | 原因 |
|-------|---------|------|
| Analyst | 中等模型 | 理解需求 + 结构化输出 |
| Architect | 强模型 | 需要深度推理和权衡 |
| Developer | 强模型 | 代码质量直接影响产出 |
| Tester | 中等模型 | 执行测试 + 生成报告 |
| Release | 弱模型 | 主要是机械性操作 |

---

## 9. 实施优先级

| 优先级 | 任务 | 工作量 | 依赖 |
|--------|------|--------|------|
| **P0** | 设计 Pipeline 数据模型（Stage/Gate/Artifact） | 中 | 无 |
| **P0** | 重写 Agent 角色（Analyst/Architect/Developer/Tester/Release） | 中 | 无 |
| **P1** | 实现 Pipeline 引擎（阶段流转、Gate 判定、上下文传递） | 大 | P0 |
| **P1** | Agent 间消息接入 Pipeline + 持久化 | 中 | P0 |
| **P1** | Pipeline Dashboard 前端页面 | 大 | P0 |
| **P2** | 产出物版本管理（Git 集成） | 中 | P1 |
| **P2** | 人工介入机制（暂停/打回/指令） | 中 | P1 |
| **P3** | 多项目并行 Pipeline | 小 | P1 |
| **P3** | 测试覆盖（Pipeline + 协作 + Dashboard） | 中 | P1 |

---

## 10. 开放问题

1. **LLM 选型**：使用什么模型？是否按阶段分级？
2. **Pipeline 可配置性**：是否支持用户自定义 Pipeline（自定义阶段顺序和 Agent 组合）？
3. **并发能力**：多个项目同时跑 Pipeline，资源如何隔离？
4. **错误恢复**：Agent 调用失败 / LLM 超时如何处理？
5. **安全性**：Developer Agent 的代码执行沙箱如何加强（TD-4）？
6. **前端技术栈决策**：继续 Vanilla JS 还是启用 React 重写（TD-7）？

---

## 附录

### A. 与现有代码的关系

Catown 当前的代码结构基本可以复用：

| 现有模块 | 新用途 | 改动量 |
|---------|--------|--------|
| `agents/core.py` | Agent 基类不变，重写 system_prompt 和工具 | 小 |
| `agents/registry.py` | 注册新角色 | 小 |
| `agents/collaboration.py` | 接入 Pipeline，消息持久化 | 中 |
| `chatrooms/manager.py` | 可保留作为独立聊天功能 | 无 |
| `routes/api.py` | 新增 Pipeline API | 中 |
| `frontend/index.html` | 新增 Pipeline Dashboard 页面 | 大 |
| `models/database.py` | 新增 Pipeline 相关表 | 中 |

### B. 参考文档

- [PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md) - 项目完成总结
- [TECHNICAL_FEASIBILITY.md](./TECHNICAL_FEASIBILITY.md) - 技术可行性分析
- [PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md) - 项目结构说明
- [AGENT_CONFIG.md](./AGENT_CONFIG.md) - Agent 配置说明
