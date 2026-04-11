# ADR-011: 聊天室全事件卡片统一

**状态**: 待确认
**日期**: 2026-04-12
**决策人**: BOSS
**相关**: PRD §4.5 (Skills 三级注入)、PRD §7 (Agent 间实时消息)、PRD §8.3 (聊天窗口卡片可视化)、ADR-010 (监控审计与可视化)

---

## 1. 背景

### 1.1 核心要求

**所有 LLM / TOOL / SKILLS / AGENT 调用，不论是通过聊天室 SSE 还是 Pipeline 引擎触发，都必须在聊天室窗口中以卡片形式完整展示。**

BOSS 的唯一观察入口是聊天室。无论后端走哪条执行路径，前端看到的卡片应该完全一致。

### 1.2 现状

Catown 有两套 Agent 执行路径，可视化能力严重不对称：

| 事件类型 | 聊天室 SSE | Pipeline WebSocket |
|---------|-----------|-------------------|
| 🧠 LLM 调用 | ❌ 不发送 | ✅ system_prompt / model / tokens / response |
| 🔧 工具调用 | ⚠️ 只有 tool name | ✅ arguments / result / success / duration |
| ⚡ Skill 注入 | ❌ 不发送 | ✅ hint / guide 内容 |
| 💬 Agent 间消息 | ❌ 不发送 | ✅ from → to + content |
| ▶️ 阶段开始 | ❌ 不发送 | ✅ stage / agent / gate / skills |
| ✅ 阶段完成 | ❌ 不发送 | ✅ stage / duration / artifacts |
| 🚧 Gate 阻塞 | ❌ 不发送 | ✅ Approve/Reject 按钮 |
| 👤 BOSS 指令 | ❌ 不发送 | ✅ 指令内容 + 目标 Agent |

聊天室只展示了 Pipeline Dashboard 的冰山一角。

### 1.3 根因

两套执行路径，审计+事件只在 Pipeline 路径做了：

```
聊天室: routes/api.py
  _run_single_agent_turn()
    → agent.generate_response()       # 简单调用
    → SSE: agent_start / content / tool_start(tool名) / tool_result / done
    → 不写审计表，不发完整事件

Pipeline: pipeline/engine.py
  _run_agent_stage()
    → llm_client.chat_with_tools()    # 带审计
    → 写 llm_calls / tool_calls / events 表
    → WebSocket: llm_call / tool_call / skill_inject / stage_* / gate_* 事件
```

---

## 2. 决策

### 2.1 原则

**聊天室是唯一观察入口。后端执行路径（SSE / Pipeline）对前端透明，卡片渲染逻辑统一，但视觉上区分来源。**

- 聊天室触发的卡片：正常样式，融入聊天流
- Pipeline 触发的卡片：带 Pipeline 标识（左侧色带 / 标签），BOSS 一眼能看出是流水线自动执行

### 2.2 卡片清单（全量）

以下 8 种卡片，无论触发来源，都必须在聊天室中显示：

| # | 卡片 | 图标 | 默认状态 | SSE 触发点 | Pipeline 触发点 |
|---|------|------|---------|-----------|----------------|
| 1 | LLM 调用 | 🧠 | 折叠 | 每次 `chat_with_tools()` 返回 | 同左 |
| 2 | 工具调用 | 🔧📖✏️▶️🔍 | 折叠（失败展开） | 每次 `_execute_tool()` 返回 | 同左 |
| 3 | Skill 注入 | ⚡ | 折叠 | Agent 开始执行时 | Stage 开始时 |
| 4 | Agent 间消息 | 💬 | **展开** | Agent 发送/接收协作消息 | 同左 |
| 5 | 阶段事件 | ▶️/✅ | **展开** | — | Pipeline 阶段开始/完成 |
| 6 | Gate 事件 | 🚧 | **展开** | — | 审批阻塞/通过/拒绝 |
| 7 | BOSS 指令 | 👤 | **展开** | 聊天框发送指令 | Pipeline instruct API |
| 8 | Agent 输出 | 💬 | **展开** | Agent 文本回复 | 同左 |

### 2.3 来源视觉区分

每张卡片携带 `source` 字段：`chatroom` 或 `pipeline`。前端根据来源应用不同样式：

| 来源 | 视觉标记 | 说明 |
|------|---------|------|
| `chatroom` | 无特殊标记 | 融入聊天流，BOSS 主动触发的交互 |
| `pipeline` | 左侧蓝色竖线 + 小标签 `🔧 Pipeline` | 流水线自动执行，BOSS 可快速识别 |

```javascript
// 卡片容器样式
const borderClass = card.source === 'pipeline'
    ? 'border-l-2 border-l-blue-500'  // Pipeline: 左侧蓝色竖线
    : '';                               // Chatroom: 无标记

// 卡片标题区可选显示来源标签
const sourceTag = card.source === 'pipeline'
    ? '<span class="text-[10px] text-blue-400 bg-blue-500/10 px-1.5 py-0.5 rounded">🔧 Pipeline</span>'
    : '';
```

### 2.4 方案：SSE 事件扩展 + 前端卡片统一

在聊天室 SSE 流中补全所有缺失事件类型，前端统一走 `handlePipelineEvent()` 渲染卡片。

**后端改动**（`routes/api.py` — `_run_single_agent_turn()`）：

```python
# ① LLM 调用后
yield sse("llm_call", {
    "agent": agent.name, "turn": turn, "model": model,
    "tokens_in": prompt_tokens, "tokens_out": completion_tokens,
    "duration_ms": ms,
    "system_prompt": system_prompt[:5000],
    "response": content[:3000],
    "tool_calls": [...],
})

# ② 工具调用后
yield sse("tool_call", {
    "agent": agent.name, "tool": tool_name,
    "arguments": args, "success": ok,
    "result": result[:3000], "duration_ms": ms,
})

# ③ Skill 注入（Agent 开始执行时）
yield sse("skill_inject", {
    "agent": agent.name,
    "skills": [{"name": s, "hint": hint, "guide": guide} for s in active_skills],
})

# ④ Agent 间消息
yield sse("agent_message", {
    "from": from_agent, "to": to_agent,
    "content": msg, "message_type": type,
})

# ⑤ 阶段开始/完成（Pipeline 场景由 engine.py 发，聊天室可选）
yield sse("stage_started", {"stage": name, "agent": agent, "gate": gate})
yield sse("stage_completed", {"stage": name, "duration": ms, "artifacts": [...]})

# ⑥ Gate 事件（Pipeline 场景）
yield sse("gate_blocked", {"stage": name, "summary": summary})
yield sse("gate_approved", {"stage": name})
yield sse("gate_rejected", {"stage": name})

# ⑦ BOSS 指令确认
yield sse("boss_instruction", {"agent": agent, "content": instruction})
```

SSE 辅助函数：
```python
def sse(event_type: str, data: dict, source: str = "chatroom") -> str:
    return f"data: {json.dumps({'type': event_type, 'source': source, **data}, ensure_ascii=False)}\n\n"
```

Pipeline 的 WebSocket 事件也统一加 `source: "pipeline"` 字段，前端据此渲染来源样式。

**前端改动**（`frontend/index.html`）：

SSE 流处理中，将所有事件统一送入 `handlePipelineEvent()`，不再分散处理：

```javascript
// SSE 流中的事件 → 统一走卡片系统
const CARD_TYPES = ['llm_call', 'tool_call', 'skill_inject', 'agent_message',
                     'stage_started', 'stage_completed', 'gate_blocked',
                     'gate_approved', 'gate_rejected', 'boss_instruction'];

if (CARD_TYPES.includes(data.type)) {
    data.source = data.source || 'chatroom';  // SSE 默认 chatroom
    handlePipelineEvent(data);
} else {
    // 原有逻辑：content, tool_start, tool_result, agent_start, done
}
```

`handlePipelineEvent()` 内部将 `source` 存入 card 对象，`renderLLMCard` / `renderToolCard` 等渲染函数读取 `card.source` 应用对应样式。

`renderMessages()` 混合渲染：聊天消息 + 卡片按时间线排列。

### 2.4 审计落盘（可选但推荐）

聊天室的 LLM/Tool 调用同时写审计表，与 Pipeline 共享事后追溯能力：

```python
# _run_single_agent_turn() 中
db.add(LLMCall(run_id=None, stage_id=None, agent_name=agent.name, ...))
db.add(ToolCall(llm_call_id=..., run_id=None, agent_name=agent.name, ...))
db.commit()
```

`run_id=null` 表示聊天室场景（非 Pipeline 运行）。

---

## 3. 实施步骤

| 步骤 | 内容 | 改动文件 |
|------|------|---------|
| 1 | SSE 辅助函数 + 事件格式统一 | `routes/api.py` |
| 2 | `_run_single_agent_turn()` 增加 llm_call / tool_call / skill_inject 事件 | `routes/api.py` |
| 3 | 聊天室 Agent 协作消息增加 agent_message 事件 | `routes/api.py` |
| 4 | 前端 SSE handler 统一走 `handlePipelineEvent()` | `frontend/index.html` |
| 5 | 前端 `renderMessages()` 混合渲染聊天消息+卡片 | `frontend/index.html` |
| 6 | 可选：聊天室写审计表 | `routes/api.py` + `models/database.py` |

---

## 4. 验收标准

- [ ] 聊天室触发 Agent 后，显示 🧠 LLM 卡片（折叠，含 system_prompt 来源标注 + response + tokens）
- [ ] 工具调用显示对应图标卡片（📖/✏️/▶️/🔍），失败自动展开+红色边框
- [ ] Agent 开始执行时显示 ⚡ Skill 注入卡片（hint + guide 内容）
- [ ] Agent 间协作消息显示 💬 卡片（from → to，默认展开）
- [ ] Pipeline 阶段开始/完成显示 ▶️/✅ 卡片
- [ ] Gate 阻塞显示 🚧 卡片（含 Approve/Reject 按钮）
- [ ] BOSS 指令显示 👤 卡片
- [ ] 所有卡片与聊天消息按时间线混合排列
- [ ] Pipeline 来源的卡片有蓝色左侧竖线 + `🔧 Pipeline` 标签
- [ ] 聊天室来源的卡片无特殊标记，融入聊天流
- [ ] Pipeline Dashboard 的卡片显示不受影响（两套入口渲染结果一致）

---

## 5. 影响范围

| 模块 | 改动量 | 说明 |
|------|--------|------|
| `routes/api.py` | 中 | `_run_single_agent_turn()` 增加 6-7 种 SSE 事件 |
| `frontend/index.html` | 中 | SSE handler 统一到 `handlePipelineEvent()`，约 80 行 |
| `models/database.py` | 小 | 可选：聊天室写审计表 |
| `pipeline/engine.py` | 无 | 已有完整事件，不受影响 |
