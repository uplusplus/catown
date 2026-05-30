# ADR-030: Event-Sourced TaskRun State — 从根源消除状态悬挂

**日期**: 2026-05-26
**状态**: 实施中
**决策者**: Catown Runtime

---

## 背景

ADR-029 通过 P0 补全堵住了最常见的人口。但根本问题在于：**`status` 是需要主动维护的变量，每个代码路径都必须记得更新它**。10+ 个文件直接修改 `TaskRun.status`，遗漏任何一个都是悬挂源。

ADR-030 将 `status` 从"被代码设置的变量"重构为"从事实推导的结论"，从根本上消除"忘记更新状态"这一整类问题。

---

## 核心思想

```
当前方案：  代码 → 写 status → 写 event（两个动作，可能遗漏）
重构方案：  代码 → 只写 event → status = derive(events)（一个动作，不可能遗漏）
```

**TaskRun.status 不再是独立的持久化变量，而是从事件流实时推导的只读视图。**

---

## 设计

### 1. 终态事件集合

```python
TERMINAL_EVENT_TYPES = {
    "task_run_failed",
    "task_run_cancelled",
    "task_run_interrupted",
    "agent_turn_completed",  # 当无 pending approval 时为终态
}
```

### 2. 状态推导函数

```python
def derive_task_run_status(events: list[TaskRunEvent]) -> str:
    """从事件流推导当前状态，永不悬挂。"""
    if not events:
        return "unknown"
    
    last = events[-1]
    
    # 终态事件：一旦进入永不改变
    if last.event_type in ("task_run_failed", "task_run_interrupted"):
        return "failed"
    if last.event_type == "task_run_cancelled":
        return "cancelled"
    
    if last.event_type == "agent_turn_completed":
        # 检查是否有未解决的审批
        if _has_pending_approval(events):
            return "paused"
        return "completed"
    
    if last.event_type == "approval_expired":
        return "failed"
    
    if last.event_type == "approval_created":
        return "paused"
    
    # 默认：仍在运行
    return "running"
```

### 3. 写入点统一

所有状态变更经过**唯一入口** `record_task_run_event()`，该函数：
- 追加事件
- 自动推导并缓存 status
- 不需要额外的 `complete_task_run()` / `terminalize_task_run()` 调用

### 4. "永不悬挂"保证

- 终态是事件的固有属性，不是代码需要主动设置的
- `task_run_failed` 事件本身就是终态，无需额外函数调用
- 看门狗只需检查"最后一个事件是否是终态事件"
- Approval 过期由推导函数自然处理，不需要主动扫描

---

## 改进计划

### 阶段 1：基础设施（推导函数 + 统一入口）

- [x] **1-1**: `services/task_run_state.py` — 推导函数 `derive_task_run_status()`
- [x] **1-2**: `services/task_run_state.py` — 推导函数 `derive_step_status()`
- [x] **1-3**: `services/run_ledger.py` — 增强 `append_task_event()` 自动更新缓存 status
- [x] **1-4**: `services/run_ledger.py` — `complete_task_run()` 改为事件驱动

### 阶段 2：消除直接 status 赋值

- [x] **2-1**: `routes/api.py` — `_reopen_task_run_for_followup` 改用事件驱动
- [x] **2-2**: `services/runner_lifecycle.py` — 移除 approval 阻塞时的直接 status 赋值
- [x] **2-3**: `services/run_ledger.py` — `update_task_run()` 标记为 deprecated
- [x] **2-4**: `services/approval_queue.py` — 无直接 TaskRun status 赋值（已确认）

### 阶段 3：简化上层

- [x] **3-1**: `services/task_run_watchdog.py` — 改用推导函数判断 stale
- [x] **3-2**: `routes/api.py` — 移除所有 `terminalize_task_run` 调用（ADR-029 补全变为冗余）
- [x] **3-3**: `services/task_run_lifecycle.py` — 标记为 deprecated

---

## 实施记录

### 1-1: 状态推导函数 ✅

**文件**: `services/task_run_state.py`（新增）

`derive_task_run_status(events)` 从事件流推导 TaskRun 状态，包含：
- 终态事件直接返回（failed/cancelled/interrupted）
- `agent_turn_completed` 检查 pending approval 后返回
- `approval_expired` 自动返回 failed
- `approval_created` 返回 paused
- 默认返回 running

---

## 相关 ADR

- [ADR-009: Chat Runtime State 分层](./ADR-009-chat-runtime-state.md)
- [ADR-027: Chat Runtime 状态机健壮性改进](./ADR-027-chat-runtime-state-machine-hardening.md)
- [ADR-029: TaskRun 悬挂问题分析与改进](./ADR-029-taskrun-hanging-analysis-and-fix.md)

## 相关文件

| 文件 | 改动 |
|------|------|
| `services/task_run_state.py` | ✅ 新增：推导函数 |
| `services/run_ledger.py` | 修改：事件写入自动更新缓存 status |
| `services/task_run_lifecycle.py` | 修改/弃用：简化为事件包装器 |
| `routes/api.py` | 修改：移除直接 status 赋值 |
| `services/runner_lifecycle.py` | 修改：移除直接 status 赋值 |
| `services/approval_queue.py` | 修改：移除直接 status 赋值 |
