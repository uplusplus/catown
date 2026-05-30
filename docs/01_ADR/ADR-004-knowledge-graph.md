# ADR-004: 知识图谱集成方案 — Skills 模式 + 人控建图

**日期**: 2026-04-10
**状态**: 已确认
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 的多 Agent 工作流中，Agent 需要理解项目代码结构才能高效协作。当前方式是通过 `read_file` 逐个读取文件，存在以下问题：

1. **效率低**：大型项目文件多，Agent 逐个读文件浪费 token 和时间
2. **缺乏全局视角**：Agent 看到的是局部文件，难以把握模块间依赖和架构全貌
3. **重复劳动**：每个 Agent 上手都要重新理解一遍代码库
4. **新 Agent 入驻慢**：BOSS 邀请新 Agent 加入项目，它需要从零开始理解代码

需要一种机制，让 Agent 快速获取项目代码的结构化知识，而不是每次从头读文件。

## 候选方案

### 方案 A：Skills 集成（推荐）

将 graphify 包装为 `knowledge-graph` Skill，Agent 按需调用。

```
Agent 需要理解代码
    │
    ├─ 检查 graphify-out/graph.json 是否存在
    │       ├─ 不存在 → 请求 BOSS 批准建图（LLM API 调用，有成本）
    │       └─ 已存在 → 直接查询（纯本地计算，无成本）
    │
    └─ 查询结果注入 Agent 上下文
```

### 方案 B：直接集成到 Pipeline

将 graphify 建图作为 Pipeline 的固定阶段，在 analysis 之后自动执行。

```
analysis → graphify → architecture → development → ...
                         │
                         └─ 每次 pipeline 都跑，不管项目大小
```

### 方案 C：纯手动

不集成，Agent 需要时手动执行 graphify 命令。

## 决策

**采用方案 A：Skills 集成，但建图阶段需 BOSS 审批。**

核心原则：**建图由人决策，查询由 Agent 自主。**

| 操作 | 决策方 | 理由 |
|------|--------|------|
| 建图（`graphify . --no-viz`） | BOSS 审批 | 需要 LLM API 调用，有时间和费用成本；不是所有项目都需要知识图谱 |
| 查询（`graphify query`） | Agent 自主 | 纯本地计算，毫秒级响应，无成本 |
| 增量更新（`graphify . --update`） | BOSS 审批 | 同样涉及 LLM API 调用 |

## 决策理由

### 为什么不选方案 B（Pipeline 集成）

1. **不是所有项目都需要**：简单脚本项目跑 graphify 是浪费
2. **BOSS 失去控制权**：每次 pipeline 都自动跑，BOSS 无法跳过
3. **版本耦合**：graphify 在快速迭代（v0.3.27），锁定在 pipeline 里维护成本高

### 为什么不选方案 C（纯手动）

1. **Agent 无法自主触发**：需要 BOSS 手动执行，打断工作流
2. **无法融入 Agent 上下文**：graphify 产出物不会自动注入 system prompt

### 为什么方案 A 最优

1. **按需激活**：Agent 在真正需要时才请求建图，避免无谓开销
2. **成本可控**：建图需 BOSS 审批，BOSS 知道何时会产生 LLM API 费用
3. **查询零成本**：graph.json 存在后，Agent 查询是纯本地操作
4. **与 Skills 体系天然契合**：graphify 本身就是"按需发现知识结构"的工具
5. **独立升级**：graphify 包更新只需更新 pip 包，不改 Catown 代码

## 关于"建图需人决策"的关键讨论

### DeepSeek 的观点

DeepSeek 建议纯 Skills 集成，认为 graphify 天然适合按需调用，无需额外控制。

### 我方的补充

DeepSeek 的分析忽略了建图和查询两个阶段的成本差异：

| 阶段 | 成本 | 频率 | 是否需要人控 |
|------|------|------|-------------|
| 建图 | 高（LLM API 调用，分钟级） | 低（项目开始一次） | ✅ 是 |
| 查询 | 极低（本地计算，毫秒级） | 高（随时） | ❌ 否 |

如果全靠 Skills 自主决策，Agent 可能在不合适的时候触发建图（如编码中途突然要建图等 5 分钟），影响 BOSS 体验。

### 结论

**建图是 BOSS 的决策，查询是 Agent 的决策。** 两阶段分开处理，各取所需。

## Skill 定义

```json
{
  "knowledge-graph": {
    "name": "知识图谱",
    "description": "基于 graphify 构建和查询项目代码知识图谱",
    "required_tools": ["execute_code", "read_file", "write_file"],
    "prompt_fragment": "## 知识图谱\n- 处理代码相关任务前，检查项目中是否存在 graphify-out/graph.json\n- 不存在 → 向 BOSS 请求建图许可\n- 已存在 → 可直接读取 GRAPH_REPORT.md 获取项目结构概览\n- 具体查询 → 自主执行 graphify query（无需审批）\n- 项目文件变更后 → 向 BOSS 请求增量更新许可",
    "category": "analysis"
  }
}
```

## 适用 Agent

| Agent | 是否配置 | 理由 |
|-------|---------|------|
| developer | ✅ | 核心使用者，编码时频繁需要理解代码结构 |
| architect | ✅ | 设计架构时需要了解现有代码依赖 |
| analyst | 可选 | 需求分析阶段可能代码尚未存在 |
| tester | 可选 | 测试时可能需要理解代码结构 |
| release | ❌ | 发布阶段不涉及代码理解 |

## 与现有模块的关系

| 模块 | 关系 | 改动 |
|------|------|------|
| `configs/skills.json` | 新增 knowledge-graph skill 定义 | 新增条目 |
| `configs/agents.json` | developer/architect 的 skills 列表加入 knowledge-graph | 新增字段 |
| `tools/execute_code.py` | 用于执行 graphify 命令 | 无改动 |
| `agents/core.py` | system prompt 组装时注入 skill prompt_fragment | 无改动（已有 Skills 机制） |
