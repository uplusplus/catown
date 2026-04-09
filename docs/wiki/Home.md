# Catown Wiki

**最后更新**: 2026-04-09

---

## 项目概览

[PRD](PRD.md) | [ADR 索引](#adr-索引) | [开发日志](#开发日志)

Catown — AI 软件工厂。输入原始需求，输出可发布产品。多个 AI Agent 协作完成软件项目开发，全流程自动化，BOSS 可实时监控和介入。

---

## 架构速览

```
用户提交需求 → Pipeline 启动
    │
    ▼
┌─────────┐  ┌──────────┐  ┌───────────┐  ┌────────┐  ┌─────────┐
│ Analyst  │→│ Architect │→│ Developer  │→│ Tester │→│ Release │
│ 需求分析 │  │ 架构设计  │  │ 编码开发   │  │ 测试    │  │ 发布     │
│ Gate:人工 │  │ Gate:自动 │  │ Gate:自动  │  │ Gate:自动│  │ Gate:人工│
└─────────┘  └──────────┘  └───────────┘  └────────┘  └─────────┘
```

**核心技术栈**: Python 3.10+ / FastAPI / SQLite / WebSocket / Vanilla JS + TailwindCSS

**测试状态**: 287 个测试（252 单元 + 35 E2E）100% 通过

---

## ADR 索引

| # | 标题 | 状态 | 日期 |
|---|------|------|------|
| [ADR-001](ADR-memory.md) | 记忆系统架构决策 — 混合方案（自研编排 + ChromaDB） | ✅ 已确认 | 2026-04-09 |
| [ADR-002](ADR-agent-soul.md) | Agent SOUL 体系 — 三层 prompt 结构 | ✅ 已确认 | 2026-04-09 |

---

## 开发日志

### [2026-04-09] PRD 增强 + 架构决策 + 代码重构

**参与者**: BOSS + AI 架构助手

#### 讨论背景

PRD v1.0 已完成核心功能（Pipeline 引擎、Agent 协作、前端 Dashboard），所有测试通过。本次讨论针对 Agent 能力的深度设计，补充 5 个新需求。

---

#### 1. PRD 新增需求

##### §4.5 工具/技能白名单机制

- Agent 仅能调用 `agents.json` 中声明的工具和 skills，运行时白名单校验
- 超出白名单时 Agent 可发起**临时授权请求**（本次允许 / 本阶段允许 / 拒绝）
- Skills 是工具的高层封装，配置在 `skills.json`

##### §4.6 三层记忆体系

- **短期记忆**（session 级）：对话上下文、中间状态，Stage 结束后摘要归档
- **项目记忆**（project 级）：关键决策、架构约定、问题方案，Markdown 文件存储
- **长期记忆**（agent 级）：工作模式、设计原则、通用经验，向量数据库 + 语义检索
- **持久化判定矩阵**：工作模式/原则/通用经验自动泛化；不确定的通过 Choice Box 交 BOSS 确认
- **睡眠整理**：触发条件从固定时间段改为**连续空闲时长**（`idle_threshold_minutes`），可中断

**讨论中的 AI 观点**：
> "三层记忆概念优雅，但短期→项目记忆的摘要提取是 LLM 密集操作，成本不低。长期记忆的'可泛化经验'边界模糊。建议睡眠触发用空闲时长而非固定时间段，因为多项目并行时 Agent 可能永远不空闲。"

##### §6.1 项目 Workspace 隔离

- **核心约束**：项目之间完全隔离，互不可见
- 隔离维度：文件系统、Git 仓库、记忆存储、Pipeline 状态、Agent 上下文、LLM Prompt 注入
- 防逃逸：路径白名单 + symlink 防护 + 绝对路径拒绝 + API 层校验

**讨论中的 AI 观点**：
> "路径校验要注意 symlink 攻击——Agent 可以创建 symlink 指向 /etc/passwd，纯路径前缀校验会被绕过。"

##### §8.3 Agent 操作可视化

- LLM 对话 / 工具调用 / 记忆操作以**折叠卡片**在聊天框展示
- LLM 卡片**不展示完整 prompt**（可能数万 token），改为展示**关键变更摘要**：
  - 新增/变更的上下文文件
  - 回答了谁的问题、提出了什么问题
  - 下一步计划
- 新增 `memory_decision` 和 `auth_request` 卡片类型（默认展开，等 BOSS 选择）

**讨论中的 AI 观点**：
> "LLM prompt 可能塞入完整 tech-spec + 代码 + 系统指令，展开后是巨大文本块。BOSS 真正关心的是'Agent 读了什么文件、改了什么、跑了什么命令'，工具调用卡片比 LLM 卡片更有价值。"

##### §9 审计机制

- 数据库记录 Agent 与 LLM 的每次交互、工具调用及结果
- `audit_logs`（摘要）+ `audit_details`（大字段分离）双表设计
- 按日志类型滚动清理（LLM 对话 7 天，工具调用 14 天，Pipeline 事件 180 天）
- **日志锁定机制**：调查中的 PipelineRun 可锁定跳过清理

**讨论中的 AI 观点**：
> "如果一个项目出了问题需要追溯但日志已被清理了怎么办？加一个锁定机制。"

---

#### 2. 架构决策

##### ADR-001: 记忆系统 — 混合方案

**候选方案对比**：

| 方案 | 控制力 | 工作量 | 部署复杂度 |
|------|--------|--------|-----------|
| A: 纯自研 | ✅ 完全 | ❌ 1-2 周 | ✅ 无依赖 |
| B: Mem0/Zep/Letta | ❌ 不适配 | ❌ 适配成本高 | ❌ 额外服务 |
| **C: 自研编排 + ChromaDB** | ✅ 可控 | ✅ 3.5 天 | ✅ 嵌入式 |

**决策**：采用方案 C。
- 短期记忆：内存 + JSON 落盘（零依赖）
- 项目记忆：Markdown 文件 + grep 检索（零依赖）
- 长期记忆：ChromaDB + sentence-transformers（轻量嵌入式）
- 睡眠整理：Python 异步任务，不引入额外框架

##### ADR-002: Agent SOUL 体系

**问题**：现有 `system_prompt` 是扁平静态文本，无个性、无价值观、无记忆注入。

**决策**：三层 prompt 结构
```
┌──────────────┐
│ SOUL（灵魂）  │  identity / values / style / quirks
├──────────────┤
│ ROLE（角色）  │  title / responsibilities
├──────────────┤
│ RULES（规则） │  硬性规则 + 工具白名单
├──────────────┤
│ MEMORY（记忆）│  项目记忆 + 长期记忆注入
└──────────────┘
```

引擎动态组装，不保留旧 `system_prompt` 字段（项目未发布，无需兼容）。

---

#### 3. 代码变更

##### agents.json 重构

- 移除旧 `system_prompt` 字段
- 每个 Agent 新增：`name`, `soul`, `role`（结构化）, `skills`, `memory`, `sleep`
- 6 个 Agent 全部配置完成

##### github_manager 扩展

- 从 14 个 action 扩展到 24 个
- 新增：`fork_repo`, `create/delete_branch`, `list_contents`, `get/create/update/delete_file`, `clone_repo`, `list_commits`, `get_commit`, `search_code`
- 文件操作支持 base64 编解码、SHA 冲突检测、大文件截断

---

#### 4. Git 提交记录

| Commit | 内容 |
|--------|------|
| `101989f` | docs(PRD): 新增工具白名单、三层记忆、Workspace隔离、操作可视化、审计机制 |
| `c6f2530` | docs(ADR): 记忆系统架构决策记录 |
| `7ca72ac` | docs(PRD): §6.1 重写为项目间隔离架构 |
| `2ef58bd` | docs: Agent SOUL 体系 + ADR |
| `4360397` | feat(config): agents.json 增加 SOUL/role/skills/memory/sleep |
| `fbdff5f` | refactor: 移除 system_prompt 兼容层 |
| `b5742f0` | feat(github_manager): 补齐 GH 常见操作（24 个 action） |

---

#### 5. 待办

- [ ] 引擎实现 `build_system_prompt()` 组装逻辑
- [ ] 实现 Choice Box 交互组件（前端 + WebSocket）
- [ ] 短期记忆实现（内存 + JSON 落盘）
- [ ] 项目记忆实现（Markdown 文件 + grep）
- [ ] ChromaDB 集成（长期记忆语义检索）
- [ ] 睡眠整理调度器
- [ ] 审计日志写入 + 双表设计
- [ ] Agent 操作可视化折叠卡片（前端）
- [ ] 工具白名单运行时校验
- [ ] 临时授权请求流程
- [ ] 项目 Workspace 隔离（路径校验 + symlink 防护）
