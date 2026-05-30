# ADR-028: Chat 运行时状态机全景分析

**日期**: 2026-05-26
**状态**: 分析文档
**来源**: feature/v2 分支代码审查

---

## 背景

Catown Chat 运行时的状态管理由多个实体协作完成，并非单一有限状态机。本文档对当前方案做全景梳理，作为后续演进的基线参考。

---

## 核心实体总览

| 实体 | 职责 | 持久化 |
|------|------|--------|
| `TaskRun` | 一次聊天 turn 的生命周期 | DB (`task_runs` 表) |
| `OrchestrationRuntimeQueue` | 多 agent 调度步骤的 DAG | 内存 + 事件日志 |
| `ApprovalQueueItem` | 阻塞式审批队列 | DB (`approval_queue_items` 表) |
| `TaskRunCheckpoint` | 快照恢复点 | DB (`task_run_checkpoints` 表) |
| Chat Timeline (ADR-019) | 后端事实投影 | DB (`task_run_events` 表) |

---

## 1. TaskRun 状态机

**文件**: `services/task_status_transition.py`

### 状态转换图

```
                    ┌──────────┐
                    │ running  │◄──────────── reopen
                    └────┬─────┘              (completed → running)
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
      ┌──────────┐ ┌──────────┐ ┌──────────┐
      │completed │ │  failed  │ │cancelled │
      │(可reopen)│ │ (终态)   │ │ (终态)   │
      └──────────┘ └──────────┘ └──────────┘
            │
            └──► paused (审批阻塞时)
                   │
                   └──► running | cancelled
```

### 转换表

```python
TASK_RUN_VALID_TRANSITIONS = {
    "running":   {"completed", "failed", "cancelled", "paused"},
    "paused":    {"running", "cancelled"},
    "completed": {"running"},      # reopen for approval followup
    "failed":    set(),            # 终态
    "cancelled": set(),            # 终态
}
```

### 设计要点

- 所有状态变更必须经过 `validate_transition()`，非法跳转抛 `InvalidTaskRunStatusTransition`
- `completed → running` 仅用于审批后续动作的 reopen
- `paused` 是审批阻塞时的中间态，由 `TaskRun.blocked_by_queue_item_id` 外键关联具体审批项
- `failed` 和 `cancelled` 为终态，无出边

### 集成点

- `run_ledger.update_task_run()` — 调用 `validate_transition()` 校验
- `routes/api.py._reopen_task_run_for_followup()` — completed → running
- `runner_lifecycle.py` — 审批暂停流 (running → paused)
- `approval_queue.py` — 审批解除 (paused → running)

---

## 2. OrchestrationRuntimeQueue — 调度步骤 DAG

**文件**: `services/orchestration_scheduler.py`

### 步骤状态流转

```
  ready → running → completed  (释放下游 waiting → ready)
                  → failed     (不释放下游，保留 _waiting 供 cancel_dependents 使用)
                  → cancelled  (级联取消下游)
  waiting → ready  (前置步骤 completed 后释放)
```

### 调度模式

1. **`linear_blocking_chain`** — 串行执行，每个步骤等待前一个完成
2. **`blocking_chain_with_sidecars`** — 主链串行 + 并行 sidecar（如 tester agent）

模式由 `build_orchestration_schedule()` 根据 agent_type 是否命中 `sidecar_agent_types` 自动决定。

### 核心数据结构

- `plan.steps[]` — `ScheduledAgentTurn` 列表，包含 `dispatch_kind`（blocking/sidecar）
- `_ready: deque` — 当前可执行步骤，按 blocking 优先 + position 排序
- `_waiting: dict[str, list]` — step_id → 等待它的下游步骤列表
- `_completed / _failed / _cancelled: set` — 终态集合
- `_runtime_state: dict` — 每个步骤的运行时状态快照

### 失败级联

```python
def mark_failed(step_id, reason):
    """标记失败，不释放下游（cancel_dependents 需要 _waiting 数据）"""

def cancel_dependents(failed_step_id):
    """BFS 级联取消所有传递依赖"""
```

### 快照

`OrchestrationRuntimeSnapshot` 提供可序列化的运行时视图，包含各状态步骤计数和 ID 列表，供前端 Monitor 展示。

---

## 3. ApprovalQueue — 审批阻塞机制

**文件**: `services/approval_queue.py`

### 生命周期

```
创建 (pending) ──► approved / rejected / expired
```

### 阻塞流

1. Tool call 被策略拦截 → `create_approval_queue_item()` 创建 pending 审批项
2. TaskRun 状态 → `paused`，`blocked_by_queue_item_id` 指向审批项
3. 用户审批/拒绝 → `resolve_approval_queue_item()` → TaskRun 回到 `running`
4. TTL 过期 → `expire_stale_approvals()` → 自动 `expired`，`resolved_by='system'`

### 防重入

- `request_key` 幂等：相同 request_key 的 pending 审批不会重复创建
- `claim_approval_queue_resolution_lease()` 使用乐观锁（`UPDATE … WHERE`）防止并发解决

### TTL 机制

- 默认 24 小时（`DEFAULT_APPROVAL_TTL_SECONDS`）
- `expires_at` 列，nullable DateTime，indexed
- `expire_stale_approvals()` 批量过期处理

---

## 4. 前端三层消息状态（ADR-009）

### 数据流

```
用户发送消息
  → ChatTab 立即写入 local-overlay (极小 pending-turn 兜底)
  → App.tsx 创建 optimisticMessages (完整流式状态)
  → 后端 SSE/WS 流式返回
  → optimisticMessages 持续更新步骤/内容
  → 后端消息落库
  → messages 覆盖同 turn 的 optimistic / overlay
  → overlay 清理
```

### 三层分工

| 层 | 作用 | 持久化策略 |
|---|---|---|
| `messages` | 后端落库的正式事实源 | 后端 DB，刷新后以它为准 |
| `optimisticMessages` | 前端运行态（流式步骤、tool call 进度） | localStorage（有体积裁剪和写入失败兜底） |
| `chat-local-overlay` | 极小 pending-turn 兜底 | localStorage（只保留 client_turn_id + 正文 + 状态摘要） |

### 持久化原则

**应该持久化**: `client_turn_id`、用户消息正文、assistant 是否仍在处理中、最后一条简短状态摘要

**不应该持久化**: 完整 system prompt、prompt payload、tool 参数/输出、长篇 response draft

---

## 5. Canonical Chat Timeline（ADR-019）

**文件**: `services/orchestration_events.py` + 前端渲染

### 核心原则

1. **每个事件代表一个事实** — LLM 请求创建、响应开始/完成、tool call 开始/完成等
2. **每个事实事件必须携带后端排序** — `sequence`（单调递增）+ `occurred_at` + `recorded_at`
3. **运行时状态由后端投影** — 不同前端客户端看到相同状态
4. **Runtime cards 不是步骤源** — 仅供详情检查，前端不从中推断步骤
5. **不做兼容性回退** — 旧的 runtime card → 步骤映射已移除

### 步骤形状

```json
{
  "id": "task-run:12:event:4",
  "scope": "task_run",
  "task_run_id": 12,
  "chatroom_id": 3,
  "sequence": 4,
  "occurred_at": "2026-05-15T10:12:30.000000",
  "recorded_at": "2026-05-15T10:12:30.000000",
  "event_type": "llm_request_created",
  "step_id": "llm:Valet:1",
  "actor": "Valet",
  "kind": "llm",
  "phase": "request",
  "state": "done",
  "summary": "Valet sent a request to the LLM."
}
```

---

## 6. Checkpoint 快照恢复

**文件**: `services/task_run_checkpoint.py`

### 机制

- 每 N 个事件（默认 20）自动保存 checkpoint
- `TaskRunCheckpoint` 模型：`task_run_id` + `event_index` + `snapshot_json`
- 恢复时从最新 checkpoint 开始 replay，而非从头

### 快照内容（`TaskRunCheckpointSnapshot` 类型）

- 事件计数、最新事件类型/时间
- 最新 agent turn 预览
- 最新 context compaction 详情
- Scheduler 运行时快照
- Subagent 生命周期状态
- Continuation cursor/state
- 待审批计数
- 当前 turn 本地状态（tool_names、blocked_tool、protocol_messages 等）

---

## 7. 事件类型枚举（`models/enums.py`）

统一为 `EventType` 枚举（继承 `str`），覆盖 40+ 种事件类型：

| 分类 | 事件数 | 示例 |
|------|--------|------|
| Orchestration 生命周期 | 3 | `orchestration_started`, `scheduler_plan_created` |
| Scheduler 步骤 | 5 | `scheduler_step_dispatched/completed/failed/cancelled/resumed` |
| Agent turn 生命周期 | 3 | `agent_turn_started/completed/resumed` |
| LLM 请求/响应 | 4 | `llm_request_created/response_started/response_completed/call` |
| Tool 执行 | 4 | `tool_call_started/blocked/round_recorded/call` |
| Approval 队列 | 5 | `approval_queue_item_created/resolved/followup_*` |
| TaskRun 生命周期 | 9 | `task_run_created/failed/cancelled/interrupted/recovery_*` |
| Pipeline 门控 | 8 | `gate_approved/rejected/blocked`, `stage_start/end` |
| 其他 | ~10 | `context_compaction`, `handoff_created`, `runtime_mode_selected` |

`RunKind` 枚举定义 8 种运行模式，区分 stream/non-stream 和 single/multi-agent。

---

## 8. ADR-027 健壮性改进总结

| 改进项 | 解决的问题 | 状态 |
|--------|-----------|------|
| P0-1: TaskRun 状态转换表 | 防止任意 status 字符串写入 | ✅ 已实施 |
| P0-2: Scheduler failed/cancelled 状态 | 步骤失败不再卡在 running，下游不再永远 waiting | ✅ 已实施 |
| P0-3: Approval TTL + 自动过期 | 审批不再永久阻塞 TaskRun | ✅ 已实施 |
| P1-1: blocked_by_queue_item_id | 直接关联 TaskRun 和审批项，无需扫描全部 | ✅ 已实施 |
| P1-2: SELECT FOR UPDATE | 消除恢复租约竞态窗口 | ✅ 已实施 |
| P1-3: Checkpoint 快照 | 长任务恢复无需 replay 全部事件 | ✅ 已实施 |
| P2-1: EventType 枚举 | 统一事件类型字符串 | ✅ 已实施（`models/enums.py`） |
| P2-2: RunKind 枚举 | 统一 run_kind 字符串 | ✅ 已实施（`models/enums.py`） |
| P2-3: TurnContextState reset | 多轮对话 tool_rounds 积累问题 | ⏳ 待排期 |

---

## 9. 架构特征总结

### 设计风格

- **事件溯源 (Event Sourcing)** — 所有状态变更通过事件记录
- **CQRS 投影** — 后端拥有事实，前端只做渲染（ADR-019）
- **分层状态** — TaskRun（turn 级）+ Scheduler Steps（步骤级）+ Approval（审批级）
- **乐观 UI** — 前端三层状态保证即时回显

### 已知遗留

- P2-3: `TurnContextState.tool_rounds` 在循环中不断 append，多轮对话时可能积累
- 事件类型虽已枚举化，但部分旧代码可能仍有硬编码字符串
- Checkpoint 快照的 `snapshot_builder` callback 由调用方提供，格式不完全统一

---

## 相关 ADR

- [ADR-009: Chat Runtime State 分层](./ADR-009-chat-runtime-state.md)
- [ADR-019: Canonical Chat Timeline](./ADR-019-canonical-chat-timeline.md)
- [ADR-027: Chat Runtime 状态机健壮性改进](./ADR-027-chat-runtime-state-machine-hardening.md)

## 相关文件

| 文件 | 职责 |
|------|------|
| `services/task_status_transition.py` | TaskRun 状态转换表 + 验证 |
| `services/orchestration_scheduler.py` | 调度步骤 DAG + 运行时队列 |
| `services/approval_queue.py` | 审批队列 CRUD + TTL + 租约 |
| `services/task_run_checkpoint.py` | Checkpoint 快照服务 |
| `services/runner_lifecycle.py` | Agent turn 事件记录 + 审批暂停流 |
| `services/orchestration_step_runner.py` | 非流式调度步骤执行 |
| `services/orchestration_stream_runner.py` | 流式调度步骤执行 |
| `services/run_ledger.py` | TaskRun/Event 读写 |
| `models/enums.py` | EventType + RunKind 枚举 |
| `models/database.py` | ORM 模型定义 |
| `frontend/src/types.ts` | 前端类型定义 |
