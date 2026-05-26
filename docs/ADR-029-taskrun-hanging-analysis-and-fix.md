# ADR-029: TaskRun 悬挂问题分析与改进计划

**日期**: 2026-05-26
**状态**: 实施中
**决策者**: Catown Runtime

---

## 背景

生产环境中频繁出现 agent working 状态悬挂、task 结束没有回报的问题。用户发送消息后，前端显示 agent 持续 working，但实际执行已中断或完成，TaskRun 永远停留在 `running` 或 `paused` 状态。

---

## 成因分析

### 1. 非流式路径异常吞没（最常见）

**文件**: `routes/api.py` — `send_message()`

`trigger_agent_response()` 抛异常时，catch 块只打日志，未将 TaskRun 标记为 failed。TaskRun 创建后状态为 `running`，异常后永远悬挂。

```
用户发送 → create_task_run(running) → trigger_agent_response() → 异常 → 日志
                                                                    ↑ TaskRun 仍在 running
```

### 2. 流式路径 SSE 断连无收尾

**文件**: `routes/api.py` — `send_message_stream()` → `raw_event_generator()`

SSE 流的 `finally` 块关闭 DB 和重置 workspace，但不调用 `complete_task_run`。客户端断连后：
- `producer_task` 被 cancel
- 后台 LLM/tool 执行可能仍在进行
- DB session 已关闭，后续事件无法写入
- TaskRun 永远 running

### 3. 编排流式步骤异常传播中断

**文件**: `orchestration_stream_runner.py`

流式编排在步骤失败时调用 `deps.fail_task_run()`，但如果 SSE 连接已断，yield 的 error/done 事件无人接收，fail_task_run 内部的 DB commit 可能因 session 状态异常而静默失败。

### 4. Approval 过期后 TaskRun 不联动

**文件**: `approval_queue.py` — `expire_stale_approvals()`

Approval TTL 过期时只更新 approval 状态为 `expired`，不处理关联的 TaskRun。TaskRun 保持 `paused` 状态，`blocked_by_queue_item_id` 指向已过期的 approval，永远无法恢复。

### 5. Tracked run_shell followup 失败不收尾

**文件**: `routes/api.py` — `_continue_agent_after_tracked_run_shell_async()`

Followup 失败后记录了 `TRACKED_RUN_SHELL_FOLLOWUP_FAILED` 事件，但不调用 `complete_task_run(status="failed")`。TaskRun 保持 running。

### 6. 委派任务回报链断裂

**文件**: `routes/api.py` — `_report_delegated_child_result_to_parent()`

子任务完成后调用 `trigger_agent_response` 给父任务。如果这步抛异常：
- 子任务已完成（不可回退）
- 父任务仍在 `waiting_for_delegated_work` 状态
- 无重试机制，父任务永远等待

### 7. 无看门狗 / 无心跳检测

系统没有定期扫描长时间 running 的 TaskRun 的机制。一旦上述任何路径导致悬挂，只能靠用户手动发现。

---

## 改进计划

### P0: 关键路径补全（消除最常见悬挂源）

- [x] **P0-1**: 非流式路径 `send_message` 异常时 fail TaskRun
- [x] **P0-2**: 流式路径 `raw_event_generator` finally 时 fail TaskRun
- [x] **P0-3**: Approval 过期时联动 fail TaskRun
- [x] **P0-4**: Tracked run_shell followup 失败时 fail TaskRun
- [x] **P0-5**: TaskRun 终态转换统一收口（`task_run_lifecycle.py`）

### P1: 防御性机制（兜底安全网）

- [x] **P1-1**: TaskRun 看门狗 — 定期扫描 stale running TaskRun
- [ ] **P1-2**: 委派任务 followup 失败重试

### P2: 架构改进（长期）

- [ ] **P2-1**: 执行层与 SSE 传输层解耦
- [ ] **P2-2**: 心咽驱动的状态自愈 cron job

---

## 实施记录

### P0-5: TaskRun 终态转换统一收口 ✅

**文件**: `services/task_run_lifecycle.py`（新增）

新增 `terminalize_task_run()` 函数，所有 TaskRun 终态转换统一经过此函数：
- 幂等：已终态的 TaskRun 不会重复转换
- 自动验证状态转换表
- 自动追加终态事件
- 支持 error/payload/event_type 覆盖

### P0-1: 非流式路径异常兜底 ✅

**文件**: `routes/api.py` — `send_message()`

catch 块增加 `terminalize_task_run(db, task_run, status="failed")` 调用。异常时 TaskRun 不再永远 running。

### P0-2: 流式路径 finally 补全 ✅

**文件**: `routes/api.py` — `raw_event_generator()`

finally 块在 `db.close()` 前检查 TaskRun 状态，如果仍为 running 则标记 failed（event_type=TASK_RUN_INTERRUPTED）。

### P0-3: Approval 过期联动 ✅

**文件**: `approval_queue.py` — `expire_stale_approvals()`

过期时检查关联 TaskRun 是否为 paused，如果是则调用 `terminalize_task_run(status="failed")`。增加了 EventType 和 logger 导入。

### P0-4: run_shell followup 失败兜底 ✅

**文件**: `routes/api.py` — `_continue_agent_after_tracked_run_shell_async()`

except 块增加 `terminalize_task_run(status="failed")` 调用。

### P1-1: TaskRun 看门狗 ✅

**文件**: `services/task_run_watchdog.py`（新增）

新增 `sweep_stale_task_runs()` 函数：
- 扫描 status=running 且 updated_at 超过阈值（默认 30 分钟）的 TaskRun
- 检查最新事件时间，有近期活动的跳过
- 调用 `terminalize_task_run()` 标记为 failed
- 返回被清扫的 TaskRun 列表，供 cron/heartbeat 调用方报告

建议通过 cron 或 heartbeat 每 5-10 分钟调用一次。

---

## 相关 ADR

- [ADR-009: Chat Runtime State 分层](./ADR-009-chat-runtime-state.md)
- [ADR-027: Chat Runtime 状态机健壮性改进](./ADR-027-chat-runtime-state-machine-hardening.md)
- [ADR-028: Chat Runtime 状态机全景分析](./ADR-028-chat-runtime-state-machine-overview.md)

## 相关文件

| 文件 | 改动 |
|------|------|
| `services/task_run_lifecycle.py` | ✅ 新增：终态转换统一收口 |
| `routes/api.py` | ✅ 修改：P0-1/P0-2/P0-4 补全 |
| `approval_queue.py` | ✅ 修改：P0-3 过期联动 |
| `services/task_run_watchdog.py` | ✅ 新增：P1-1 看门狗 |
