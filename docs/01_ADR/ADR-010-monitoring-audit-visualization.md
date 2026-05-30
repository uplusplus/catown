# ADR-010: 监控审计与交互可视化

**状态**: 已确认
**日期**: 2026-04-11
**决策人**: BOSS
**相关**: PRD §8 (监控与人工介入)、PRD §9 (审计机制)、PRD §21 (上下文压缩)

---

## 1. 背景

Catown 当前的可观测性存在严重缺口：

- **LLM 对话不可见**：`engine.py` 的 `_run_agent_stage()` 中 `messages` 列表是局部变量，函数结束后丢失。BOSS 无法看到 Agent 的 system_prompt 实际组装结果、每轮 LLM 收发内容、工具调用的完整回传过程。
- **工具调用无审计**：`_execute_tool()` 的入参和返回值仅通过 WebSocket 广播，不落盘。
- **阶段流转不透明**：`PipelineStage.input_context` 只存 `{"context_length": N}`，不存实际内容。重试、打回、超时等事件只打 logger。
- **无 Token 计量**：`LLMClient.chat_with_tools()` 未读取 `response.usage`。
- **聊天室 LLM 会话无记录**：`send_message_stream` SSE 流式输出不落盘。

PRD §8 已规划了 Pipeline Dashboard、Agent 操作可视化、折叠卡片等交互设计，PRD §9 已规划了 `audit_logs` + `audit_details` 双表模型，但均未实现。本 ADR 定义数据层的落地方案和前端可视化方案。

## 2. 决策

### 2.1 定位

不做传统开发者调试工具（断点、堆栈、变量查看），而是面向 BOSS 的**运营监控面板 + 审计追溯记录**：

- **监控**：实时看到 Agent 在干什么、LLM 对话内容、工具调用、阶段进度
- **审计**：事后回溯——查某次 Pipeline 的完整执行记录、Token 消耗、谁做了什么

### 2.2 数据管道：三层采集

在现有调用链上增加旁路审计写入点，不修改 Agent 决策逻辑和执行流程。

```
Agent 执行引擎
    │
    ├── ① LLM 调用层 ──▶ llm_calls 表
    │      (system_prompt, messages, response, tool_calls, tokens, 耗时)
    │
    ├── ② 工具执行层 ──▶ tool_calls 表
    │      (工具名, 入参, 返回摘要, 耗时, 成功/失败)
    │
    └── ③ 事件层 ──────▶ events 表
           (event_type, 关联方, 摘要, payload)
```

三层通过 `run_id` + `stage_id` 关联，支撑两种查询模式：
- **实时监控**：WebSocket 推送 + SSE 扩展事件
- **事后审计**：REST API 按 run/agent/时间范围查询

### 2.3 数据模型

#### llm_calls — LLM 对话全记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| run_id | INTEGER FK | Pipeline run（聊天室场景 nullable） |
| stage_id | INTEGER FK | 阶段（nullable） |
| agent_name | TEXT | Agent 名 |
| turn_index | INTEGER | 该 Agent 在本轮阶段中的第几轮对话 |
| model | TEXT | 实际调用的模型 |
| system_prompt | TEXT | 完整 system prompt |
| messages | TEXT | 本次传入的完整 messages JSON |
| response_content | TEXT | LLM 返回的文本 |
| response_tool_calls | TEXT | LLM 返回的工具调用 JSON |
| token_input | INTEGER | prompt_tokens |
| token_output | INTEGER | completion_tokens |
| duration_ms | INTEGER | 调用耗时 |
| error | TEXT | 错误信息 |
| created_at | DATETIME | |

#### tool_calls — 工具执行记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| llm_call_id | INTEGER FK | 关联哪次 LLM 调用触发的 |
| run_id | INTEGER FK | 归属 |
| stage_id | INTEGER FK | |
| agent_name | TEXT | 谁调的 |
| tool_name | TEXT | 工具名 |
| arguments | TEXT | 入参 JSON |
| result_summary | TEXT | 返回值摘要（前 500 字） |
| result_length | INTEGER | 完整返回值长度 |
| success | BOOLEAN | 是否成功 |
| duration_ms | INTEGER | 耗时 |
| created_at | DATETIME | |

#### events — 事件流

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| run_id | INTEGER FK | 归属 |
| event_type | TEXT | `stage_start` / `stage_end` / `stage_retry` / `gate_blocked` / `gate_approved` / `gate_rejected` / `rollback` / `agent_message` / `boss_instruction` / `error` / `timeout` |
| agent_name | TEXT | 相关 Agent |
| stage_name | TEXT | 相关阶段 |
| summary | TEXT | 一句话摘要 |
| payload | TEXT | 完整详情 JSON |
| created_at | DATETIME | |

### 2.4 LLM Client 改造

`LLMClient.chat_with_tools()` 返回值增加 `usage` 字段：

```python
return {
    "content": choice.message.content,
    "tool_calls": ...,
    "usage": {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    } if response.usage else None
}
```

### 2.5 审计 API

| 端点 | 功能 |
|------|------|
| `GET /api/audit/llm?run_id=X` | 某次 pipeline 的所有 LLM 调用 |
| `GET /api/audit/llm/{id}` | 单条完整 prompt + response |
| `GET /api/audit/tools?run_id=X` | 工具调用记录 |
| `GET /api/audit/events?run_id=X` | 事件流 |
| `GET /api/audit/tokens/summary?run_id=X` | Token 汇总 + 成本估算 |
| `GET /api/audit/timeline?run_id=X` | 聚合时间线（LLM + 工具 + 事件混合排序） |

### 2.6 前端：聊天窗口卡片可视化

将所有交互事件以卡片形式渲染在聊天流中，按时间顺序排列，形成执行叙事线。

#### 7 种卡片类型

| 卡片 | 图标 | 默认状态 | 触发事件 |
|------|------|----------|----------|
| 🧠 LLM 调用 | 🧠 | 折叠（显示 Agent + 模型 + token + 关键句） | 每次 LLM 请求/响应 |
| 🔧 工具调用 | 🔧/📖/✏️/▶️/🔍 | 折叠（显示工具名 + 结果摘要） | 每次工具执行 |
| 💬 Agent 间消息 | 💬 | 展开 | Agent 间 ask/reply |
| ▶️ 阶段事件 | ▶️/✅ | 展开 | 阶段开始/完成 |
| 🚧 Gate 卡片 | 🚧 | 展开 + 操作按钮 | manual gate 阻塞 |
| ⚡ Skill 注入 | ⚡ | 折叠 | 阶段开始时 |
| 👤 BOSS 指令 | 👤 | 展开 | BOSS 发出指令 |

#### 卡片渲染规则

- **默认折叠**：LLM 卡片、工具卡片、Skill 卡片——避免刷屏
- **默认展开**：Agent 间消息（协作叙事线索）、阶段事件（进度标记）、Gate 卡片（需要操作）
- **自动展开**：工具调用失败（红色边框）、LLM 调用出错
- **合并连续同类卡片**：连续 3 个 `read_file` 合为一个卡片显示
- **LLM 卡片展开内容**：system_prompt（标注来源片段）→ messages 历史 → response（含工具调用）
- **工具卡片按类型区分 icon**：`read_file` 📖 / `write_file` ✏️ / `execute_code` ▶️ / `web_search` 🔍

#### SSE 事件扩展

当前 SSE 推送 `content`、`tool_start`、`tool_result`，需扩展为：

```javascript
// 新增事件类型
{ type: "llm_call", agent, model, turn, tokens, content_preview }
{ type: "stage_start", stage, agent, skills_active }
{ type: "stage_end", stage, duration, artifacts }
{ type: "agent_message", from, to, content, message_type }
{ type: "gate_blocked", stage, artifact_summary }
{ type: "skill_inject", agent, skills }
```

### 2.7 与 PRD 章节的关系

| PRD 章节 | 关系 |
|----------|------|
| §8 监控与人工介入 | 本 ADR 是 §8 的数据层实现方案。§8 定义了"看什么"，本 ADR 定义"怎么存、怎么查、怎么渲染" |
| §9 审计机制 | 本 ADR 的三表模型（llm_calls / tool_calls /events）是 §9 `audit_logs` + `audit_details` 的具体落地。§9 的滚动清理策略（保留期、锁定机制）直接适用 |
| §8.3 Agent 操作可视化 | 本 ADR 的 7 种卡片类型是 §8.3 折叠卡片规范的具体实现定义 |
| §8.4 聊天框输入体验 | 本 ADR 的卡片系统与 §8.4 的指令系统、输入联想独立不冲突 |
| §21 上下文压缩 | tool_calls 的 `result_summary`（前 500 字）与 §21 CC-008 Tee 机制互补：摘要存 DB，完整输出存 `.catown/tee/` |

### 2.8 关键约束

1. **旁路审计**：数据采集不修改 Agent 决策逻辑和执行流程
2. **LLM 审计表不存大文件**：`read_file` 返回值只存摘要，完整内容通过 Tee 机制追溯
3. **`input_context` 改为存储实际内容**：~10KB/阶段，总计 ~50KB/pipeline
4. **调试 API 加权限控制**：`/api/audit/` 通过环境变量 `AUDIT_API_ENABLED` 控制
5. **不影响实时性能**：审计写入异步执行，不阻塞 Agent 执行循环
6. **前端单文件兼容**：所有卡片渲染函数内嵌在 `index.html`，不拆分构建

## 3. 实施计划

| 优先级 | 模块 | 内容 | 工作量 |
|--------|------|------|--------|
| P0-a | 数据管道 | `LLMClient` 返回 usage + `engine.py`/`api.py` 写 llm_calls + tool_calls + events 三表 | 2 天 |
| P0-b | 审计 API | `GET /api/audit/*` 查询端点（按 run/agent/时间/类型过滤 + 汇总统计 + 时间线聚合） | 1 天 |
| P0-c | SSE 扩展 | 新增 llm_call / stage_start / stage_end / agent_message / gate_blocked / skill_inject 事件类型 | 1 天 |
| P1-a | 卡片前端 | 7 种卡片渲染函数 + renderMessages() 混合卡片流 + 折叠/展开/合并 | 2 天 |
| P1-b | Token 面板 | 按 agent/stage/模型分组的消耗统计 | 1 天 |
| P2-a | 滚动清理 | PRD §9.3 保留期策略 + 锁定机制 | 半天 |

## 4. 备选方案（已否决）

### 方案 B：独立调试页面

将监控/审计做成独立页面，不嵌入聊天窗。

**否决原因**：割裂了上下文。BOSS 在聊天窗看到 Agent 回复了一句话，想知道"为什么这么说"，需要切到另一个页面查——操作成本高，叙事断裂。

### 方案 C：只做后端 API，不做前端

先落地数据管道和查询 API，前端后续再做。

**否决原因**：数据管道的价值只有通过可视化才能体现。没有前端，三张表只是日志——BOSS 不会直接查 SQL。数据管道 + 卡片前端必须同步交付。

### 方案 D：复用 PRD §9 的 audit_logs 双表

不新建三张表，扩展 audit_logs / audit_details。

**否决原因**：§9 的双表设计面向通用审计，字段粒度不适合 LLM 对话这种高频、大 payload 的场景。llm_calls 需要 system_prompt / messages 等专用字段，塞进 audit_details 会导致查询效率低、索引困难。三张表各司其职更清晰。
