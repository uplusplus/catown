# ADR-031: Telemetry 拆库与串行写适配层

**Date**: 2026-05-26  
**Status**: In Progress  
**Decision Owner**: Catown Runtime

---

## Context

Catown 当前把业务事实、运行时事件、网络监控、LLM 审计全部写入同一个 SQLite 数据库 `catown.db`。

在单 agent 流式路径中，以下几类高频写会同时争抢 SQLite 写锁：

- 业务事实写：`messages`、`task_runs`、`task_run_events`
- 运行时审计写：`llm_calls`、`tool_calls`、`events`
- 网络监控写：`monitor_network_records`

当前实现还存在两个放大问题：

1. 高频路径存在大量“小事务”  
   多处代码在单条记录级别直接 `db.commit()`。

2. 所有写共用同一主库  
   观测类写与业务关键写互相影响，一旦监控/审计写变密，会拖慢甚至阻塞主业务链路。

运行态证据表明，流式请求卡住时，日志中会反复出现 `sqlite3.OperationalError: database is locked`，并伴随 `monitor_network_records`、`llm_wait`、`request_sent` 等持续写入。

---

## Decision

### 1. 拆库

将观测/审计类数据从主业务库拆到独立 telemetry 库。

主库 `catown.db` 保留：

- `messages`
- `task_runs`
- `task_run_events`
- 其他业务核心表

Telemetry 库 `telemetry.db` 承载：

- `monitor_network_records`
- `llm_calls`
- `tool_calls`
- `events`

### 2. 串行写适配层

为 telemetry 库新增统一写入口，所有高频观测写不再直接 `Session.commit()`，而是：

1. 主流程构造写请求
2. 写请求进入内存队列
3. 后台单写者顺序刷盘
4. 按批次提交事务

该层负责把“多调用方并发写”转换为“单写者串行提交”。

### 3. 先治理 telemetry 写，不立即改造主库业务写

第一阶段只改造高频观测写：

- `monitor_network_records`
- `llm_calls`
- `tool_calls`
- `events`

`task_run_events` 暂时保留在主库，避免第一阶段扩大语义面。

### 4. SQLite 参数同步优化

主库与 telemetry 库都应启用适合桌面本地运行的 SQLite 设置：

- `journal_mode=WAL`
- 合理的 `busy_timeout`
- telemetry 写端允许批量 `commit`

---

## Rationale

### 为什么先拆 telemetry，而不是直接整体换数据库？

因为当前最密集、最容易放大锁冲突的，是观测类写，不是核心业务写。先把 telemetry 抽离，可以最低风险地降低主链路阻塞。

### 为什么需要串行写适配层？

SQLite 可以支持并发读，但不能让多个写事务真正并行提交。  
正确做法不是“让更多调用方直接写 SQLite”，而是“扇入成单写者顺序提交”。

### 为什么第一阶段不动 `task_run_events`？

`task_run_events` 同时承担事实来源和状态派生语义，直接异步化会影响“事件写入后立刻可见”的假设。第一阶段优先切掉高频观测写，收益最大、风险最小。

---

## Design

### 1. 新增 telemetry 数据库配置

- `TELEMETRY_DATABASE_URL`
- `TELEMETRY_SQLALCHEMY_DATABASE_URL`
- telemetry 独立 engine / session factory

默认路径：

- 主库：`~/.catown/state/catown.db`
- telemetry 库：`~/.catown/state/telemetry.db`

### 2. 新增串行写适配层

建议新增统一组件，例如：

- `services/telemetry_store.py`
- `services/telemetry_writer.py`

适配层能力：

- `enqueue_*` 写接口
- 单后台 worker
- 批量刷盘
- `flush()` / `shutdown()`
- 失败回退日志

### 3. 监控写接入方式

`MonitorNetworkBuffer.append()` 改为：

- 先写内存缓冲
- 再 enqueue 到 telemetry writer
- 不在请求线程内直接 `Session.commit()`

### 4. 审计写接入方式

`audit_recorder.py` 改为通过 telemetry store 记录：

- `LLMCall`
- `ToolCall`
- `Event`

第一阶段允许保留原有 API 形状，但内部不再自行 `db.commit()`。

### 5. 初始化与生命周期

运行时启动时：

- 初始化 telemetry 库
- 启动 writer

运行时关闭时：

- drain 队列
- flush 剩余批次
- 关闭 writer

---

## Implementation Plan

### Phase 1: Telemetry 拆库基础设施

- [x] **1-1**: 配置层新增 telemetry 数据库路径
- [x] **1-2**: 新增 telemetry engine / session factory
- [x] **1-3**: 审计模型改为绑定 telemetry metadata
- [x] **1-4**: 启动时自动初始化 telemetry 表

### Phase 2: 串行写适配层

- [x] **2-1**: 新增 telemetry writer
- [ ] **2-2**: 支持单写者队列 + 批量提交
- [x] **2-3**: 支持 flush / shutdown / 错误日志

### Phase 3: 接管 monitor 高频写

- [x] **3-1**: `monitor_network_records` 改为入队写
- [x] **3-2**: 清理 `network_buffer.py` 内直接 commit 的热点路径

### Phase 4: 接管 audit 高频写

- [ ] **4-1**: `llm_calls` 改为入队写
- [ ] **4-2**: `tool_calls` 改为入队写
- [ ] **4-3**: `events` 改为入队写
- [ ] **4-4**: 移除 `audit_recorder.py` 中分散的直接 commit

### Phase 5: SQLite 参数与验证

- [ ] **5-1**: 主库启用 WAL / busy timeout
- [ ] **5-2**: telemetry 库启用 WAL / busy timeout
- [ ] **5-3**: 增加针对 writer 的测试
- [ ] **5-4**: 验证单 agent 流式路径不再被 telemetry 写阻塞

---

## Progress Notes

### 2026-05-26

- 新增 ADR，确定方案为“业务主库 + telemetry 独立库 + 单写者串行提交”
- 已完成 Phase 1：telemetry 配置、独立 engine/session、独立 metadata、启动建表
- 已完成第一批 Phase 2/3：新增 `TelemetryWriter`，`monitor_network_records` 改为后台串行写
- telemetry 表已从主库 metadata 拆出，默认写入 `~/.catown/state/telemetry.db`
- 下一步优先事项：补批量提交，并把 `audit_recorder.py` 的 `llm_calls/tool_calls/events` 切到 telemetry writer

---

## Consequences

### Positive

- 高频观测写不再直接和主业务写争抢同一主库写锁
- SQLite 的单写者约束被显式吸收进适配层
- 后续可以继续把更多观测写迁入 telemetry writer，而不破坏业务路径

### Negative

- telemetry 数据会从“请求内同步可见”变为“短暂异步落盘”
- 需要处理 shutdown flush、队列积压和失败回退
- 代码结构从“直接 ORM 写”演进为“存储适配层”，复杂度上升

---

## Related

- [ADR-010: 监控审计与交互可视化](./ADR-010-monitoring-audit-visualization.md)
- [ADR-014: Network Monitor Semantics](./ADR-014-network-monitor-semantics.md)
- [ADR-029: TaskRun 悬挂问题分析与改进](./ADR-029-taskrun-hanging-analysis-and-fix.md)
- [ADR-030: Event-Sourced TaskRun State](./ADR-030-event-sourced-taskrun-state.md)
