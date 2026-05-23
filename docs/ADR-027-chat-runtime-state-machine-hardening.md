# ADR-027: Chat Runtime 状态机健壮性改进

**日期**: 2026-05-23
**状态**: 已实施（P0 全部完成，P1 全部完成，P2 待排期）
**决策者**: Catown Runtime

---

## 背景

Chat 运行时的状态机由多个实体组成：`TaskRun`、`OrchestrationRuntimeQueue`、`ApprovalQueueItem`、`OrchestrationHandoffDelivery`、`PipelineRun` 等。代码审查发现以下缺陷：

1. **TaskRun 状态转换无校验** — 任意 status 字符串可直接写入，`completed → running` 等非法跳转不会被阻止
2. **Scheduler 步骤无 failed 状态** — 步骤失败时仅记录事件，步骤本身停留在 `running`，下游永远 `waiting`
3. **Approval 队列无超时** — pending 审批永远不过期，TaskRun 永久卡在 `paused`
4. **TaskRun 与审批无直接关联** — 判断阻塞状态需要扫描全部 approval items
5. **恢复租约有竞态** — `UPDATE … WHERE` 模式在高并发下可能两个进程同时 claim
6. **长运行任务恢复性能差** — 需要 replay 全部事件重建状态，无快照压缩
7. **事件类型/枚举散落** — 硬编码字符串，易拼写错误

---

## 决策

### P0: 关键缺陷修复

#### P0-1: TaskRun 状态转换表 ✅

**文件**: `services/task_status_transition.py`

引入显式转换表，所有 `TaskRun.status` 变更必须经过 `validate_transition()`：

```
running   → completed | failed | cancelled | paused
paused    → running | cancelled
completed → running    (reopen for approval followup)
failed    → (terminal)
cancelled → (terminal)
```

- `InvalidTaskRunStatusTransition` 异常用于非法跳转
- 集成到 `run_ledger.update_task_run()`、`routes/api.py._reopen_task_run_for_followup()`、`runner_lifecycle.py` 审批暂停流
- 23 个单元测试

#### P0-2: Scheduler failed/cancelled 状态 ✅

**文件**: `services/orchestration_scheduler.py`

- `mark_failed(step_id, reason)` — 标记步骤为 `failed`，记录失败原因
- `cancel_dependents(failed_step_id)` — 级联取消所有传递依赖
- 新状态：`failed`、`cancelled` 加入运行时追踪
- `ScheduledStepRuntimeState.failed_reason` 诊断字段
- `OrchestrationRuntimeSnapshot` 新增 `failed_step_count`、`cancelled_step_count`、`failed_step_ids`、`cancelled_step_ids`
- 集成到 `orchestration_step_runner.py` 和 `orchestration_stream_runner.py`
- 12 个单元测试

#### P0-3: Approval TTL + 自动过期 ✅

**文件**: `models/database.py`、`services/approval_queue.py`

- `ApprovalQueueItem.expires_at` 列（nullable DateTime, indexed）
- `create_approval_queue_item()` 接受 `ttl_seconds` 参数（默认 24 小时）
- `expire_stale_approvals()` 将过期的 pending 项转为 `expired`，`resolved_by='system'`
- `serialize_approval_queue_item()` 输出包含 `expires_at`
- 11 个单元测试

### P1: 中等改进

#### P1-1: TaskRun blocked_by_queue_item_id ✅

**文件**: `models/database.py`、`services/runner_lifecycle.py`、`routes/api.py`、`services/run_ledger.py`

- `TaskRun.blocked_by_queue_item_id` 外键指向 `approval_queue_items.id`
- 审批创建时设置，reopen 时清除
- 序列化输出中暴露，支持直接查询"什么阻塞了这个 run"
- `TaskRun.blocked_by_queue_item` relationship（带 `foreign_keys` 消歧义）

#### P1-2: 恢复租约 SELECT FOR UPDATE ✅

**文件**: `services/orchestration_recovery_lease.py`

- `claim_recovery_lease()` 改用 `SELECT … FOR UPDATE` + 读后更新模式
- `renew_recovery_lease()` 同样使用行锁
- 消除 claim 竞态窗口
- `TaskRun.approval_queue_items` 和 `ApprovalQueueItem.task_run` relationship 加 `foreign_keys=` 消歧义

#### P1-3: 定期 Checkpoint 快照 ✅

**文件**: `services/task_run_checkpoint.py`、`models/database.py`

- `TaskRunCheckpoint` 模型：`task_run_id`、`event_index`、`snapshot_json`
- `save_checkpoint()` / `latest_checkpoint()` / `delete_checkpoints()`
- `should_checkpoint()` 每 N 个事件触发（默认 20）
- `maybe_save_checkpoint()` 条件保存，接受 builder callback
- `TaskRun.checkpoints` relationship，级联删除
- 7 个单元测试

### P2: 待排期

#### P2-1: 事件类型统一为枚举

当前事件类型字符串（`agent_turn_started`、`scheduler_step_completed` 等）散落在多处硬编码。计划引入 `EventType` 枚举，统一引用。

#### P2-2: run_kind 统一为枚举

`TaskRun.run_kind` 当前为自由字符串（`chat_turn` 等），无类型约束。计划引入 `RunKind` 枚举。

#### P2-3: TurnContextState reset 机制

`TurnContextState.tool_rounds` 在循环中不断 append，多轮对话时可能积累大量无用历史。计划增加 `reset()` / `compact()` 方法。

---

## 影响

- **向后兼容**: 所有改动均为增量，不破坏现有 API
- **数据库迁移**: 新增 `task_run_checkpoints` 表和 `expires_at`/`blocked_by_queue_item_id` 列，需 `Base.metadata.create_all()` 或 Alembic migration
- **测试覆盖**: 新增 60+ 单元测试，覆盖状态转换、调度器失败、审批过期、checkpoint 逻辑

---

## 相关文件

| 文件 | 改动 |
|------|------|
| `services/task_status_transition.py` | 新增：状态转换表 + 验证 |
| `services/orchestration_scheduler.py` | 修改：failed/cancelled 状态 |
| `services/approval_queue.py` | 修改：TTL + expires_at |
| `services/task_run_checkpoint.py` | 新增：checkpoint 快照服务 |
| `services/run_ledger.py` | 修改：序列化 blocked_by |
| `services/runner_lifecycle.py` | 修改：设置 blocked_by + 转换验证 |
| `services/orchestration_recovery_lease.py` | 修改：SELECT FOR UPDATE |
| `services/orchestration_step_runner.py` | 修改：调用 mark_failed |
| `services/orchestration_stream_runner.py` | 修改：调用 mark_failed |
| `routes/api.py` | 修改：reopen 验证 + 清除 blocked_by |
| `models/database.py` | 修改：新列 + 新表 + relationship 消歧义 |
| `tests/test_task_status_transition.py` | 新增：23 tests |
| `tests/test_orchestration_scheduler_failure.py` | 新增：12 tests |
| `tests/test_approval_ttl.py` | 新增：11 tests |
| `tests/test_task_run_checkpoint.py` | 新增：7 tests |
