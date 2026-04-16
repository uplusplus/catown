# ADR-024: 架构差距分析 — 当前实现 vs 设计文档

**日期**: 2026-04-16
**状态**: 待确认
**决策人**: BOSS
**相关**: PRD §4-§10, ADR-004 ~ ADR-011

---

## 1. 执行摘要

PRD + 11 个 ADR 定义了一个非常完整的愿景。当前 `feature/business-architecture-refactor` 分支落地了约 **40-50%**，主要集中在骨架层。设计方向全部正确（无方向性错误），差距在连接管道未打通。

**一句话总结**：设计文档是 A 级蓝图，当前实现是 C+ 级骨架。骨架能跑，但离设计愿景的差距不在架构方向上，而在"记忆不持久、Skill 不注入、审计不记录、token 不追踪"。

---

## 2. 总览矩阵

| 模块 | 设计文档 | 当前实现 | 完成度 | 优先级 |
|------|---------|---------|--------|--------|
| SOUL 三层 Prompt 体系 | ✅ | ✅ 数据模型 + 组装逻辑 | 80% | — |
| Skills 渐进式披露 (ADR-008) | ✅ | ⚠️ 框架字段到位，注入管道缺失 | 25% | 🔴 P0 |
| 上下文压缩 (ADR-009) | ✅ | ❌ 完全未实现 | 0% | 🔴 P0 |
| 监控审计 (ADR-010) | ✅ | ❌ 完全未实现 | 0% | 🔴 P0 |
| 三层记忆体系 (PRD §4.6) | ✅ | ⚠️ 纯内存 list，无持久化 | 15% | 🔴 P0 |
| 知识图谱 (ADR-004) | ✅ | ❌ 未实现 | 0% | 🟡 P1 |
| Choice Box 交互 | ✅ | ❌ 未实现 | 0% | 🟡 P1 |
| 睡眠整理机制 | ✅ | ❌ SleepConfig 已定义，无调度器 | 5% | 🟡 P1 |
| Pipeline 引擎 | ✅ | ✅ engine.py 54KB | 85% | — |
| Agent 间协作 | ✅ | ✅ collaboration.py 17KB | 80% | — |
| 安全隔离 (基础) | ✅ | ✅ 白名单 + 路径校验 | 70% | — |
| 工具临时授权 | ✅ | ❌ 未实现 | 0% | 🟡 P1 |

---

## 3. 逐模块详细对比

### 3.1 SOUL 三层 Prompt 体系 — 已落地，有偏差

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| `SoulConfig` / `RoleConfig` | identity + values + style + quirks | ✅ 完整实现 | 无 |
| `build_system_prompt()` | 5 层组装：灵魂→角色→规则→项目记忆→长期记忆 | ✅ `AgentConfigV2` 中实现 | — |
| 项目记忆注入 | `project_memory` 参数 | ❌ 参数存在但无调用方传入 | 缺数据源 |
| 长期记忆注入 | `long_term_memory` 参数 | ❌ Agent 有 memory list 但不持久化 | 纯内存，重启丢失 |
| Agent 个性辨识度 | PRD 示例生动（命名强迫症等） | ⚠️ 默认配置偏精简 | 可更生动 |

**评价**：SOUL 的数据模型和组装逻辑忠实于设计，架构是对的。但记忆注入管道没打通——`build_system_prompt()` 的参数存在但没有实际调用者提供 `project_memory` 和 `long_term_memory`。

### 3.2 Skills 渐进式披露 (ADR-008) — 框架到位，核心缺失

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| `skills.json` 三级定义 | hint / guide / full 三层 | ❌ 文件不存在 | 最大缺口 |
| `AgentConfigV2.skills` 字段 | agent 声明拥有的 skills | ✅ 字段已定义 | — |
| `build_system_prompt()` 注入 hint | 拼接 hint 到 system prompt | ❌ 无 skill 注入逻辑 | 缺实现 |
| `active_skills` / `hint_only_skills` | pipelines.json stage 配置 | ❌ 未实现 | — |
| Full 层写入 `.catown/skills/` | Pipeline 启动时写入 | ❌ 未实现 | — |
| 工具与 Skill 联动校验 | `required_tools` 一致性检查 | ❌ 未实现 | — |

**评价**：ADR-008 的三级渐进式披露是全项目最有价值的设计创新——用 hint 保底（30 token）、guide 按需（150 token）、full 文件系统按需读取，比全量注入节省 36% token。当前只有数据模型字段，注入管道完全未接通。最需要优先实现。

### 3.3 上下文压缩 (ADR-009) — 完全未实现

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| Layer 1: 输出级过滤 (RTK 策略) | `output_filter.py` + 12 种过滤器 | ❌ 不存在 | — |
| Layer 2: 跨阶段摘要 | 结构化 JSON 摘要注入下游 | ❌ 不存在 | — |
| Layer 3: LLM 辅助摘要 | 条件触发的轻量模型摘要 | ❌ 不存在 | — |
| Token 追踪 | SQLite 记录每次调用的 token 消耗 | ❌ 不存在 | — |

**评价**：三层压缩架构很扎实，对 RTK 的分析和"不集成二进制、翻译其策略"的决策非常务实。P0 的三个过滤器（test/git/build）预估 3 天能完成 70% token 节省。ROI 最高。

### 3.4 监控审计 (ADR-010) — 完全未实现

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| `llm_calls` / `tool_calls` / `events` 三表 | 审计数据模型 | ❌ 不存在 | — |
| LLM Client 返回 usage | `response.usage` 透传 | ❌ 未读取 | — |
| 审计 API (`/api/audit/*`) | 6 个查询端点 | ❌ 不存在 | — |
| 7 种聊天卡片 | LLM 调用/工具/消息/Gate/Skill 等 | ❌ 不存在 | — |
| SSE 事件扩展 | `llm_call` / `stage_start` / `gate_blocked` 等 | ❌ 未扩展 | — |
| Token 面板 | 前端按 agent/stage 统计消耗 | ❌ 不存在 | — |

**评价**：BOSS 的核心诉求是"可观测 + 可介入"，当前连 LLM 调了什么、用了多少 token 都看不到。与 ADR-009 并列为最高优先级。

### 3.5 三层记忆体系 (PRD §4.6) — 部分落地

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| 短期记忆 (session-level) | 内存 + Stage 结束后归档 | ⚠️ `Agent.short_term_memory` 列表，裁剪到 20 条 | 无持久化、无归档 |
| 项目记忆 (project-level) | `projects/{id}/.catown/memory/` | ❌ 不存在 | — |
| 长期记忆 (agent-level) | `configs/agents/{name}/memory/` + ChromaDB | ❌ 不存在 | — |
| 睡眠整理 | 空闲触发 → 短期→项目→长期→压缩 | ❌ `SleepConfig` 已定义，无调度器 | — |
| Choice Box | Agent 提交不确定决策给 BOSS | ❌ 不存在 | — |
| 记忆持久化判定矩阵 | 泛化 vs 项目特定 vs 丢弃 | ❌ 不存在 | — |

### 3.6 知识图谱 (ADR-004) — 未实现

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| `knowledge-graph` Skill 定义 | 在 skills.json 中 | ❌ skills.json 不存在 | — |
| 建图需 BOSS 审批 | Agent 请求 → 弹出 Choice Box | ❌ 未实现 | — |
| 查询自主 | `graphify query` 纯本地计算 | ❌ 未实现 | — |

### 3.7 Pipeline 引擎 — 核心已实现

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| Stage 串行执行 | analyst → architect → developer → tester → release | ✅ engine.py 54KB | — |
| Gate 类型 (auto/manual/condition) | 人工审批 + 自动通过 | ⚠️ 有基础实现，condition gate 未见 | 需验证 |
| Agent 间实时消息 | send/query/delegate/broadcast | ✅ collaboration.py 17KB | — |
| Workspace 隔离 | 每个项目独立目录 | ✅ 路径校验 | — |

### 3.8 安全隔离 — 基础已落地

| 维度 | 设计 | 实现 | 差距 |
|------|------|------|------|
| 工具白名单 | agents.json tools 字段 | ✅ registry.py `get_tools_for_agent()` | — |
| 路径校验 | `_validate_path()` + symlink + `.catown/` 保护 | ✅ | — |
| 工具临时授权 | Agent 请求 → BOSS 审批 → 本次/本阶段/拒绝 | ❌ 不存在 | — |
| 审计日志 | 所有授权请求记录 | ❌ 不存在 | — |

---

## 4. 实施优先级建议

| 排名 | 缺失项 | 影响 | 预估工作量 |
|------|--------|------|-----------|
| 1 | Skills 三级注入管道 | Agent 行为质量直接决定产品价值 | 2-3 天 |
| 2 | 审计三表 + Token 追踪 | 没有可观测性，BOSS 无法信任 | 3-4 天 |
| 3 | 上下文压缩 (P0 过滤器) | token 成本决定产品能不能用得起 | 3 天 |
| 4 | 记忆持久化 | 重启丢状态是致命的 | 2 天 |
| 5 | Choice Box 交互 | 人机协作的核心交互形态 | 3-4 天 |
| 6 | 工具临时授权 | 安全与灵活性的平衡 | 1-2 天 |
| 7 | 知识图谱 Skill | Agent 理解代码结构的效率提升 | 2 天 |
| 8 | 睡眠整理调度器 | 记忆体系的自动化闭环 | 2 天 |
