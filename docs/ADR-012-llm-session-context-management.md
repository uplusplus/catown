# ADR-012: LLM 会话上下文管理

**状态**: 待确认
**日期**: 2026-04-12
**决策人**: BOSS
**相关**: PRD §21 (上下文压缩系统)、PRD §4.6 (三层记忆体系)

---

## 1. 背景

### 1.1 现状

Catown 的 LLM 调用采用无状态 HTTP 模型：每次调用独立构造完整 messages 数组发送，LLM 侧不保留任何对话状态。

```
调用1: [system_prompt, user_msg_1] → response_1
调用2: [system_prompt, user_msg_1, assistant_resp_1, user_msg_2] → response_2
调用3: [system_prompt, user_msg_1, ..., user_msg_3] → response_3
         ↑ 每次完整发送，历史不断累积
```

当前 system_prompt 包含内容（聊天室 SSE 路径）：

| 组成部分 | 估算 token | 说明 |
|---------|-----------|------|
| 角色定义 + SOUL | ~200 | Agent 身份、价值观、风格（来自 agents.json） |
| 项目信息 | ~100 | 项目名、描述 |
| 工具名称列表 | ~50 | 仅名称，如 "read_file, write_file, execute_code" |
| 团队成员 | ~150 | 所有 Agent 列表 |
| 记忆注入（自身） | ~800 | 最近 8 条记忆 |
| 记忆注入（共享） | ~500 | 其他 Agent 高重要性记忆 |
| 协作上下文 | ~500 | 前一个 Agent 的输出（多 Agent 模式） |
| **system_prompt 合计** | **~2300** | 作为 messages[0] 发送 |

**但 system_prompt 不是全部开销。** API 请求中还有独立参数：

| 独立参数 | 估算 token | 说明 |
|---------|-----------|------|
| Tool schemas | ~800~2000 | 每个工具的 name/description/parameters 完整 JSON schema，作为 `tools` 参数传入，**不在 system_prompt 内** |
| 历史对话 | 变量 | 近期消息，随轮次增长 |
| 当前用户消息 | ~100~500 | 本次输入 |

**当前缺失的部分：**

| 缺失项 | 聊天室 SSE 路径 | Pipeline 路径 | 说明 |
|--------|----------------|--------------|------|
| Skills hint | ❌ 未注入 | ✅ 已注入 | Agent 可用技能提示（~30 tok/技能） |
| Skills guide | ❌ 未注入 | ✅ 已注入 | 当前阶段激活的技能指南（~150 tok/技能） |
| 上下文截断 | ❌ 无 | ❌ 无 | 两套路径都没有历史截断机制 |

加上历史对话累积，一个 5 轮对话的 token 消耗：

| 轮次 | Input tokens (估算) | 说明 |
|------|-------------------|------|
| 第 1 轮 | ~2,800 | system + user |
| 第 2 轮 | ~5,500 | + 上轮 assistant + user |
| 第 3 轮 | ~8,500 | 继续累积 |
| 第 4 轮 | ~12,000 | 工具调用返回值加入 |
| 第 5 轮 | ~16,000 | 越聊越贵 |

### 1.2 问题

1. **Token 成本线性增长**：每轮对话的 input tokens 持续累积，无截断机制
2. **system_prompt 全量注入**：每次发送所有记忆、团队列表、协作上下文，即使当前任务不需要
3. **无上下文窗口管理**：历史对话无限增长，超出模型 context window 时直接报错
4. **与 PRD §21 的关系**：PRD §21 规划了工具输出过滤（CC-001~007）和跨阶段摘要（CC-010），但未覆盖对话历史本身的管理

### 1.3 行业共识

LLM API 无状态是行业标准（OpenAI / Anthropic / Google 均如此），所有主流框架（LangChain、AutoGen、CrewAI）都在调用方维护 messages 数组。**这是正确做法，不需要改变。**

需要优化的是 messages 数组的构造策略，而非 API 调用模型。

---

## 2. 待讨论方案

### 方案 A：滑动窗口截断

保留最近 N 轮对话，丢弃更早的。

```
N=3 时：
[system_prompt, user_1, assistant_1, user_2, assistant_2, user_3, assistant_3, user_4]
                                         ↑ 丢弃 user_1/assistant_1
```

| 优点 | 缺点 |
|------|------|
| 实现简单 | 丢失早期上下文 |
| Token 消耗有上限 | Agent 可能"忘记"之前的决策 |
| 无额外 API 调用 | 硬截断可能导致对话断裂感 |

### 方案 B：摘要压缩

将早期对话压缩为一段摘要，替代原始历史。

```
[system_prompt, "（前情摘要：用户要求做用户管理系统，已完成需求分析，确定了5个核心功能）", user_3, assistant_3, user_4]
```

| 优点 | 缺点 |
|------|------|
| 保留关键信息 | 需要额外 LLM 调用来生成摘要 |
| Token 消耗可控 | 摘要可能丢失细节 |
| 对话连贯性好 | 摘要质量依赖模型能力 |

### 方案 C：分层 system_prompt

不每次全量注入，按任务阶段动态裁剪。

```
基础层（始终注入）：角色定义 + SOUL（~200 tok）
工具层（按需）：当前阶段需要的工具说明（~100 tok）
记忆层（按需）：只注入与当前任务相关的记忆（~300 tok）
协作层（按需）：只有协作场景才注入前序上下文（~500 tok）

总计：~600~1100 tok，而非固定 ~2300 tok
```

| 优点 | 缺点 |
|------|------|
| 大幅减少固定开销 | 需要定义"相关性"判断规则 |
| 与 Skills 三级注入体系兼容 | 增加 system_prompt 组装复杂度 |
| 不改变对话历史管理 | 不解决历史累积问题 |

### 方案 D：组合方案 B+C

system_prompt 分层精简 + 对话历史摘要压缩。

这是 PRD §21 上下文压缩系统的设计方向。

---

## 3. 实现约束

| 约束 | 说明 |
|------|------|
| Agent 零改动 | 优化在 LLM 调用层完成，不改 Agent prompt 和 SOUL 体系 |
| Fail-Safe | 截断/压缩失败时 fallback 到当前全量模式 |
| 可观测 | BOSS 能看到每次调用实际发送了多少 token、压缩了多少 |
| 不引入额外依赖 | 优先用现有 LLM 客户端，避免额外模型调用 |

---

## 4. 影响范围

| 模块 | 改动 |
|------|------|
| `llm/client.py` | 可选：messages 预处理、token 计量 |
| `routes/api.py` | `send_message_stream()` 中 messages 构造逻辑 + Skills 注入补齐 |
| `pipeline/engine.py` | `_run_agent_stage()` 中 messages 构造逻辑 |
| `routes/api.py` `_run_single_agent_turn()` | 同上 |

**额外发现**：聊天室 SSE 路径缺少 Skills 注入（hint/guide），与 Pipeline 路径能力不对称。如需补齐，需在 `send_message_stream()` 中读取 skills.json 并注入对应内容。这属于 ADR-011（聊天室全事件卡片统一）的延伸。

---

## 5. 状态

**当前阶段：收集意见，待 BOSS 确认方案后再实施。**

相关实施可参考 PRD §21 (CC-001~011) 的上下文压缩系统规划。
