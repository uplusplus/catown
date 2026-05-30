# ADR-028: 上下文压缩提前触发的根因分析与系统性改进方案

**日期**: 2026-05-25
**状态**: 草案
**决策者**: BOSS
**相关**: ADR-009 上下文压缩策略, ADR-012 LLM 会话上下文管理, ADR-026 动态上下文控制触发流

---

## 1. 背景

Catown 在实际使用中，上下文压缩（`context_compaction`）在对话第一轮就会触发，甚至单条用户消息尚未进行任何工具调用时，fragment 选择就已经开始 drop 和 truncate。这意味着系统在"什么都没做"的时候就已经进入压缩状态，后续每一轮的上下文质量只会持续下降。

本 ADR 对根因做定量分析，并给出分阶段改进方案。

---

## 2. 定量分析：为什么第一轮就压缩

### 2.1 当前预算限制

`chat_prompt_builder.py` 中 `chat_interactive` profile 的默认值：

```json
{
  "max_fragments": 12,
  "max_tokens_cap": 3200,
  "max_tokens_by_role": { "developer": 1200, "user": 2000 },
  "max_tokens_by_scope": {
    "session": 240, "run": 1800, "stage": 560,
    "turn": 500, "shared_fact": 300, "agent_private": 220
  }
}
```

fragment 总预算 **3200 tokens**，最多 **12 个 fragment**。

### 2.2 单轮 fragment 生成清单

以一个典型的聊天室对话（项目已创建，有团队成员）为例：

| # | Fragment 来源 | Scope | Priority | 估算 tokens |
|---|---|---|---|---|
| 1 | `operating_developer_context` | run | 10 | 300 |
| 2 | `stage_developer_context` (tools + skills hint/guide) | turn/stage | 40 | 400 |
| 3 | `boss_instruction` (如果有) | turn | 50 | 100 |
| 4 | `task_state_fragments` (项目任务状态) | run | 30-32 | 200 |
| 5 | `history_summary_fragment` (如果有旧消息) | turn | 42 | 300 |
| 6 | `standalone_note` / session instructions | turn | 15 | 100 |
| 7 | `project_context` (ID, name, desc, workspace) | run | 20 | 150 |
| 8 | `project_strategy` (vision, outcome) | run | 22 | 100 |
| 9 | `project_delivery_state` (status, stage, health) | run | 24 | 100 |
| 10 | `chatroom_context` (ID, title, session type) | run | 25 | 100 |
| 11 | `chat_routing` (@mention 规则) | run | 26 | 150 |
| 12 | `chatroom_lineage` (source chat) | run | 27 | 50 |
| 13 | `team_members` | run | 40 | 150 |
| 14 | `inter_agent_messages` | shared_fact | 45 | 200 |
| 15 | `memory_context` (自身 8 条 + 共享 5 条) | agent_private | 50 | 500 |
| 16 | `previous_agent_work` | turn | 55 | 300 |
| 17 | `tool_round_summaries` (如果有) | turn | 48 | 200 |
| **合计** | | | | **~3500** |

**结论**：即使没有任何工具调用，fragment 候选就已经 ~3500 tokens / ~17 个 fragment，同时突破 token 限额和数量限额。

### 2.3 随轮次增长

| 轮次 | history tokens | tool round tokens | fragment tokens | 总 prompt tokens | 压缩状态 |
|---|---|---|---|---|---|
| 1 | 0 | 0 | ~3500 | ~3700 | **压缩** |
| 2 | ~800 | ~2000 | ~3500 | ~6500 | **严重压缩** |
| 3 | ~2000 | ~4000 | ~3500 | ~9700 | **极度压缩** |
| 5 | ~4000 | ~6000 | ~3500 | ~13700 | 仅剩核心 fragment |

### 2.4 压缩连锁效应

当 fragment 被 drop 时，丢失的信息按优先级排序：

1. `agent_private` (memory, priority=50) → Agent 丢失记忆上下文
2. `shared_fact` (inter-agent messages, priority=45) → Agent 间协作断裂
3. `turn` (previous_agent_work, priority=55) → 丢失前序 Agent 工作成果
4. `run` (project/chatroom context, priority=20-27) → 丢失项目状态

而这些恰恰是 Agent 做出正确决策所需的关键上下文。

---

## 3. 根因分析

### 根因 1：Fragment 预算与实际需求严重脱节

3200 tokens 的 `max_tokens_cap` 是一个静态常量，没有考虑：
- 模型的实际上下文窗口大小（128K / 400K / 1M 差异巨大）
- Agent 的工具数量和 SOUL 复杂度
- 项目阶段（分析阶段 vs 开发阶段，上下文需求完全不同）

ADR-026 已经提出了比例派生方案（Phase 3），但尚未实施。

### 根因 2：Fragment 碎片化 — 大量小 fragment 浪费配额

`build_runtime_user_fragments()` 生成 6-8 个独立的 user fragment（project_context / project_strategy / project_delivery_state / chatroom_context / chat_routing / chatroom_lineage / team_members / memory），每个只有 50-200 tokens。这些完全可以合并为 1-2 个 fragment，减少 fragment 计数开销和排序开销。

### 根因 3：工具输出无过滤直接进入上下文

ADR-009 规划的 Layer 1（RTK 策略过滤）完全未实现。当前 `run_shell` 的 `MAX_OUTPUT_CHARS = 50000`（约 12500 tokens），一次 `pytest` 输出就可能耗尽全部 fragment 预算。

工具输出通过 `TurnContextState.protocol_messages()` → `current_input_messages` 路径注入，**绕过了 fragment 选择机制**，不受 `max_tokens_cap` 约束。

### 根因 4：History 管理只有滑动窗口，无渐进式压缩

`build_recent_history(limit=10)` 保留最近 10 条消息的完整内容。当这些消息包含工具调用和返回时，10 条消息可能 5000-10000 tokens，全部作为 `static_tokens` 扣除可用预算。

`build_history_summary_fragment()` 只为更早的消息做摘要，最近 10 条原样保留。

### 根因 5：developer 消息降级为 system 消息时丢失预算独立性

当 LLM 不支持 `developer` 角色时（`developer_role_supported=False`），developer fragment 被合并进 system prompt：

```python
system_content = f"{system_content}\n\n## Developer Context\n{developer_text}"
developer = []
```

这使得原本受 `max_tokens_by_role.developer=1200` 约束的内容，变成了 system prompt 的一部分，不再受 fragment 预算约束，但也无法被选择性 drop — 全量注入或全量丢失。

---

## 4. 决策

### Phase 0：紧急止血（预计 1 天）

**目标**：让第一轮对话不触发压缩。

#### 0.1 提高默认 fragment 预算

**文件**: `backend/services/chat_prompt_builder.py`

```python
# 改前
"chat_interactive": {
    "max_fragments": 12,
    "max_tokens_cap": 3200,
    ...
}

# 改后
"chat_interactive": {
    "max_fragments": 20,
    "max_tokens_cap": 8000,
    "max_tokens_by_role": {
        "developer": 2500,
        "user": 5500,
    },
    "max_tokens_by_scope": {
        "session": 400,
        "run": 4000,
        "stage": 1200,
        "turn": 1200,
        "shared_fact": 600,
        "agent_private": 600,
    },
}
```

同步调整 `fallback_chat` 和 `consult_agent`。

#### 0.2 合并碎片化 user fragments

**文件**: `backend/services/context_builder.py` — `build_runtime_user_fragments()`

将以下 6 个独立 fragment 合并为 1 个 `project_overview` fragment：

- `project_context` (priority 20)
- `project_strategy` (priority 22)
- `project_delivery_state` (priority 24)
- `chatroom_context` (priority 25)
- `chat_routing` (priority 26)
- `chatroom_lineage` (priority 27)

合并后约 400-600 tokens，1 个 fragment 而非 6 个。

```python
def build_runtime_user_fragments(...) -> list[ContextFragment]:
    fragments: list[ContextFragment] = []
    
    # ... standalone_note, run_context, runtime_context 保持不变 ...

    # 合并: project + chatroom + routing + lineage
    overview_parts = []
    overview_parts.extend(_project_context_lines(project))
    overview_parts.extend(_chatroom_context_lines(chatroom, project, source_chatroom))
    overview_parts.extend(_chat_routing_lines())
    overview_parts.extend(_chat_lineage_lines(chatroom, project, source_chatroom))
    if overview_parts:
        fragments.append(ContextFragment(
            role="user",
            content="\n\n".join(overview_parts),
            scope=ContextScope.RUN,
            visibility=ContextVisibility.GLOBAL,
            source="project_chat_overview",
            priority=20,
        ))

    # team_members, memory, inter_agent_messages, previous_agent_work 保持独立
    ...
```

**预期效果**：fragment 数量减少 5 个，第一轮从 ~17 个降到 ~12 个。

### Phase 1：工具输出过滤（预计 3 天）

**目标**：实现 ADR-009 Layer 1 的核心策略。

#### 1.1 新增 output_filter 模块

**新增文件**:
```
backend/tools/output_filter.py          # 过滤器入口
backend/tools/filters/__init__.py       # 注册表
backend/tools/filters/base.py           # BaseFilter
backend/tools/filters/git_filter.py     # git status/diff/log/commit
backend/tools/filters/test_filter.py    # pytest/jest/vitest
backend/tools/filters/build_filter.py   # build 错误提取
backend/tools/filters/generic_filter.py # 去重 + ANSI 清理
```

#### 1.2 核心接口

```python
@dataclass
class FilterResult:
    output: str
    raw_tokens: int
    filtered_tokens: int
    savings_pct: float
    tee_path: Optional[str] = None

class OutputFilter:
    def filter(self, command: str, raw_output: str, exit_code: int) -> FilterResult:
        """根据命令类型路由到对应过滤器"""
        ...
```

#### 1.3 接入点

**文件**: `backend/tools/run_shell.py` — `_execute_sync()`

```python
# 改前
return result.stdout.decode()

# 改后
raw = result.stdout.decode()
filtered = output_filter.filter(command, raw, result.returncode)
return filtered.output
```

#### 1.4 P0 过滤策略

| 过滤器 | 算法 | 压缩率 |
|---|---|---|
| `test_filter` | 状态机：仅保留 FAILED 用例 + summary 行 | 90-99% |
| `git_filter` | 提取统计：status→文件计数, diff→+N/-N, log→提交数 | 75-99% |
| `build_filter` | 丢弃 stdout，提取 stderr error/warning | 60-80% |
| `generic_filter` | 相邻重复行合并 + ANSI 剥离 | 50-85% |

#### 1.5 Tee 机制

原始输出保存到 `.catown/tee/{uuid}.log`，输出末尾附：

```
[full output: .catown/tee/abc123.log]
```

### Phase 2：History 渐进式压缩（预计 2 天）

**目标**：解决 history 无限增长问题。

#### 2.1 分层 history 策略

**文件**: `backend/services/context_builder.py` — `build_recent_history()`

```python
def build_recent_history(
    recent_messages, *, limit, ...,
    summarize_threshold: int | None = None,
):
    """
    summarize_threshold: 当消息数超过此值时，
    将 threshold 之前的消息压缩为摘要，保留最近 limit 条完整消息
    """
```

策略：
- 最近 `limit` 条（默认 5）：完整保留
- `limit` 到 `summarize_threshold`（默认 10）：每条压缩为 1 行摘要
- 更早的消息：合并为一段 `## Earlier Conversation Summary`

#### 2.2 工具结果截断

**文件**: `backend/services/turn_state.py` — `ToolResultRecord.to_message()`

```python
def to_message(self) -> dict[str, str]:
    content = self.result
    # 工具结果超过 2000 chars 时截断
    if len(content) > 2000:
        content = content[:2000] + f"\n[truncated, full: {len(self.result)} chars]"
    return {"role": "tool", "tool_call_id": self.tool_call_id, "content": content}
```

#### 2.3 降低 history_limit 默认值

**文件**: `backend/services/chat_runtime.py` / 调用方

```python
# 改前
history_limit: int = 10

# 改后
history_limit: int = 5
```

**预期效果**：history tokens 从 ~5000 降到 ~2000，释放 ~3000 tokens 给 fragment。

### Phase 3：动态预算派生（预计 2 天）

**目标**：实现 ADR-026 Phase 3，让预算跟随模型窗口自动缩放。

#### 3.1 Profile 支持比例表达式

**文件**: `backend/services/chat_prompt_builder.py`

```python
_DEFAULT_SELECTOR_PROFILES = {
    "chat_interactive": {
        "max_tokens_cap_ratio": 0.03,  # 模型输入窗口的 3%
        "max_tokens_by_role_ratio": {
            "developer": 0.01,
            "user": 0.02,
        },
        "max_tokens_by_scope_ratio": {
            "session": 0.002,
            "run": 0.015,
            "stage": 0.005,
            "turn": 0.005,
            "shared_fact": 0.003,
            "agent_private": 0.003,
        },
    },
}
```

对于 128K 模型（输入窗口 ~112K）：
- `max_tokens_cap` = 112000 × 0.03 = **3360**（接近当前 3200）

对于 400K 模型（输入窗口 ~272K）：
- `max_tokens_cap` = 272000 × 0.03 = **8160**（自动扩大）

对于 1M 模型（输入窗口 ~1M）：
- `max_tokens_cap` = 1000000 × 0.03 = **30000**（充分利用大窗口）

#### 3.2 Materialize 逻辑

已有 `materialize_selector_profile_config()` 实现 ratio → absolute 的转换，只需将 ratio 值填入默认 profile。

#### 3.3 Usage Band 主动摘要（ADR-026 Phase 4 前置）

在 `ContextSelector` 中增加 band 检测：

```python
def _selector_usage_band(*, selected_tokens, selector):
    # 已实现
    ...
```

当 band 进入 Orange/Red 时，在 `assemble_chat_messages` 的 `on_compaction` 回调中触发：
1. 对 `agent_private` scope 的 fragment 做摘要
2. 对 `shared_fact` scope 的旧消息做摘要
3. 保留 `turn` 和 `run` scope 不动

### Phase 4：跨阶段摘要（预计 3 天）

**目标**：实现 ADR-009 Layer 2。

#### 4.1 Pipeline 阶段摘要

每个 Agent stage 完成后，生成结构化摘要：

```json
{
  "stage": "analyst",
  "agent": "analyst",
  "artifacts": ["PRD.md"],
  "key_decisions": ["使用 React + TypeScript", "REST API 设计"],
  "metrics": {"requirements": 12, "user_stories": 8},
  "duration_s": 120,
  "warnings": []
}
```

#### 4.2 摘要注入

下游 Agent 的上下文中，用阶段摘要替代上游的完整对话历史：

```python
# 在 build_runtime_user_fragments 中新增
if stage_summaries:
    fragments.append(ContextFragment(
        role="user",
        content="## Pipeline Stage Summaries\n" + format_summaries(stage_summaries),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="stage_summaries",
        priority=35,
    ))
```

### Phase 5：LLM 辅助摘要（预计 2 天）

**目标**：实现 ADR-009 Layer 3。

#### 5.1 触发条件

- Usage band 为 Orange 或 Red
- 即将进入新的 Pipeline 阶段
- 手动触发（BOSS 指令）

#### 5.2 摘要策略

使用轻量模型（如 `gpt-4o-mini` 或同等）对非结构化文本做摘要：

```python
async def summarize_for_context(text: str, max_tokens: int = 200) -> str:
    """用轻量模型压缩文本到指定 token 数"""
    client = get_default_llm_client()
    response = await client.chat([
        {"role": "system", "content": "Summarize the following text concisely."},
        {"role": "user", "content": text},
    ], max_tokens=max_tokens)
    return response
```

摘要长度控制在原始内容的 10-20%。

---

## 5. 实施计划

| 阶段 | 内容 | 优先级 | 预计工时 | 依赖 |
|---|---|---|---|---|
| **Phase 0** | 提高 fragment 预算 + 合并碎片 fragment | **P0** | 1 天 | 无 |
| **Phase 1** | 工具输出过滤 (RTK 策略) | **P0** | 3 天 | 无 |
| **Phase 2** | History 渐进式压缩 + 工具结果截断 | **P0** | 2 天 | 无 |
| **Phase 3** | 动态预算派生 (ratio profiles) | **P1** | 2 天 | Phase 0 |
| **Phase 4** | 跨阶段摘要 (Pipeline 级) | **P1** | 3 天 | 无 |
| **Phase 5** | LLM 辅助摘要 | **P2** | 2 天 | Phase 1-4 |

**Phase 0-2 可并行开发**，预期组合效果：

| 场景 | 当前 prompt tokens | Phase 0+1+2 后 | 节省 |
|---|---|---|---|
| 第 1 轮（无工具调用） | ~3700 (压缩) | ~3200 (不压缩) | 消除误压缩 |
| 第 3 轮（含工具调用） | ~9700 (严重压缩) | ~5000 (轻度压缩) | -48% |
| 第 5 轮（密集工具） | ~13700 | ~7000 | -49% |
| 一次 pytest 输出 | ~12500 | ~500 | -96% |
| 一次 git diff 输出 | ~3000 | ~200 | -93% |

---

## 6. 验证方式

### 6.1 单元测试

- `test_context_builder.py`：验证合并后的 fragment 数量和 token 分布
- `test_output_filter.py`：验证各过滤器的压缩率和信息保留
- `test_chat_prompt_builder.py`：验证 ratio profile materialization

### 6.2 集成测试

- 模拟 5 轮对话，记录每轮的 `context_compaction` 事件
- Phase 0 后：第 1 轮不应触发压缩
- Phase 1 后：工具输出 token 减少 60%+
- Phase 2 后：history token 增长速率降低 50%+

### 6.3 可观测性

Monitor → Compactions 面板应能回答：
1. 压缩是因为 fragment 预算还是 history 增长？
2. 哪个 scope 的 fragment 被 drop 最多？
3. 工具输出过滤节省了多少 token？

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 提高预算后 token 成本上升 | 每轮消耗更多 token | Phase 3 的 ratio 方案会根据模型自动调整；大窗口模型边际成本低 |
| 工具输出过滤丢失关键信息 | Agent 决策失误 | Tee 机制保留原始输出；过滤失败时 fallback 到原始输出 |
| Fragment 合并后丢失结构化语义 | Monitor 无法按来源展示 | 保留 `source` 字段；合并后的 content 内部用 `##` 标题分隔 |
| History 截断导致对话断裂 | Agent 忘记早期上下文 | 摘要保留关键信息；最近 5 条完整保留 |
| Ratio profile 对小窗口模型过于激进 | 128K 模型预算反而变小 | 设置绝对下限 `min_tokens_cap: 3200` |

---

## 8. 不做的事

1. **不做 KV Cache 压缩** — 需要改推理框架，侵入性太强
2. **不做 LLMLingua 集成** — 需要额外 GPU，延迟高，对代码场景不如规则过滤
3. **不改 Agent 的 SOUL prompt** — 过滤在工具层和 context 层做，不污染 prompt 体系
4. **不一次性实现全部 12 种 RTK 策略** — 先做 P0 的 4 种（test/git/build/generic），覆盖 80% 场景
5. **不改变 LLM API 的无状态模型** — 这是正确的架构，优化点在 messages 构造策略

---

## 参考

- RTK (Rust Token Killer): https://github.com/rtk-ai/rtk
- ADR-009 上下文压缩策略: `docs/01_ADR/ADR-009-context-compression.md`
- ADR-012 LLM 会话上下文管理: `docs/01_ADR/ADR-012-llm-session-context-management.md`
- ADR-026 动态上下文控制触发流: `docs/01_ADR/ADR-026-dynamic-context-control-trigger-flow.md`
- ADR-008 Skills 渐进式披露: `docs/01_ADR/ADR-008-skills-progressive-disclosure.md`
