# ADR-019: 数据库 Schema 与迁移草案

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-016-core-domain-model.md`, `docs/ADR-017-main-api-design.md`, `docs/ADR-018-legacy-to-new-mapping.md`

---

## 1. 目标

本文档的目标，是把 Catown 新内核的数据库落地方案正式写清楚。

要回答的问题是：

**新的 `Project / Asset / Decision / StageRun` 内核应该如何落表？旧表如何并存？审计表如何迁移引用？实施顺序如何控制风险？**

这不是最终 ORM 代码，也不是最终 migration 文件，而是数据库层的架构蓝图。

本文档主要解决：

- 新主表有哪些
- 每张表的关键字段和索引是什么
- 哪些关系需要关联表
- 旧表如何保留和降级
- migration 分几步做最稳

---

## 2. Schema 设计原则

### 2.1 先保证新主链路可独立成立

新 schema 的第一目标不是完美迁移旧数据，而是先保证：

- 新项目可以完整走新模型
- 新前端可以只依赖新 API
- 新状态机不再依赖旧 chatroom/pipeline 语义

### 2.2 正式业务对象与执行/审计对象分层

数据库层必须清晰分成三层：

#### 业务主表

- `projects`
- `assets`
- `decisions`
- `stage_runs`

#### 业务关系/辅助表

- `asset_links`
- `decision_assets`
- `stage_run_assets`

#### 执行与观测层表

- `tasks`
- `agent_runs`
- `llm_calls`
- `tool_calls`
- `events`

### 2.3 尽量少用黑盒 JSON 表达核心关系

可以允许正文内容先用 JSON，但不建议把关键关系也全塞进 JSON。

应尽量结构化表达：

- 资产依赖
- 决策与资产关联
- 阶段输入输出资产关联
- 项目当前推进语义

### 2.4 先扩展，不急删旧

P0/P1 阶段的数据库迁移策略应是：

- 先加新表/新字段
- 先双结构并存
- 先让新项目只走新表
- 旧表晚一点再降级/只读/停写

---

## 3. 新主表设计

## 3.1 `projects`

### 定位

`projects` 是全局业务聚合根表。

### 处理方式

**建议在现有 `projects` 表基础上扩展重构，而不是新起 `product_projects` 一类平行表。**

原因：

- `Project` 是唯一应该保留名称但重写语义的核心对象
- 保留表名可以减少后续全局认知成本
- 只要明确“旧关系降级，新字段升主”，就不会被旧语义绑死

### 建议字段

#### 保留字段

- `id`
- `name`
- `description`
- `created_at`

#### 新增字段

- `slug`
- `one_line_vision`
- `target_users_json`
- `target_platforms_json`
- `primary_outcome`
- `references_json`
- `status`
- `current_stage`
- `execution_mode`
- `health_status`
- `autopilot_enabled`
- `current_focus`
- `blocking_reason`
- `latest_summary`
- `last_decision_id`
- `last_activity_at`
- `released_at`
- `legacy_mode`

### 字段说明建议

- `target_users_json` / `target_platforms_json` / `references_json` 在 P0 可先用 JSON 文本
- `legacy_mode` 用于标记旧项目是否仍主要运行在旧 pipeline/chatroom 模式中
- `status` 应迁移为新的业务状态枚举，不再使用旧 `active/completed/paused`

### 推荐索引

- `idx_projects_status`
- `idx_projects_current_stage`
- `idx_projects_last_activity_at`
- `idx_projects_legacy_mode`

---

## 3.2 `assets`

### 定位

`assets` 是正式产物主表。

### 建议字段

- `id`
- `project_id`
- `asset_type`
- `title`
- `summary`
- `content_json`
- `content_markdown`
- `version`
- `status`
- `is_current`
- `owner_agent`
- `produced_by_stage_run_id`
- `supersedes_asset_id`
- `approval_decision_id`
- `source_input_refs_json`
- `storage_path`
- `created_at`
- `updated_at`
- `approved_at`

### 字段设计说明

#### `asset_type`

P0 固定值：

- `project_brief`
- `prd`
- `ux_blueprint`
- `tech_spec`
- `task_plan`
- `build_artifact`
- `test_report`
- `release_pack`

#### `content_json`

用来承载结构化正文。

#### `content_markdown`

用于展示友好的可读正文，避免所有页面都去解析 JSON。

#### `storage_path`

给构建产物、测试报告附件、发布包等需要落盘引用的资产使用。

### 推荐索引

- `idx_assets_project_id`
- `idx_assets_project_type_current`
- `idx_assets_project_type_version`
- `idx_assets_status`
- `idx_assets_stage_run`

### 关键约束

- 同一项目、同一 `asset_type` 允许多版本
- 同一项目、同一 `asset_type`，建议最多只有一个 `is_current = true`

---

## 3.3 `decisions`

### 定位

`decisions` 是人工确认主表。

### 建议字段

- `id`
- `project_id`
- `stage_run_id`
- `decision_type`
- `title`
- `context_summary`
- `recommended_option`
- `alternative_options_json`
- `impact_summary`
- `requested_action`
- `status`
- `resolved_option`
- `resolution_note`
- `blocking_stage_run_id`
- `created_by_system_reason`
- `created_at`
- `resolved_at`
- `expires_at`

### 推荐索引

- `idx_decisions_project_id`
- `idx_decisions_status`
- `idx_decisions_project_status`
- `idx_decisions_type_status`
- `idx_decisions_stage_run_id`

### 关键约束

建议在应用层先实现：

- 同一项目、同一 `decision_type` 同时只允许一个活跃 `pending`

如果未来数据库支持更强约束，可再补局部唯一索引策略。

---

## 3.4 `stage_runs`

### 定位

`stage_runs` 是阶段推进实例主表。

### 建议字段

- `id`
- `project_id`
- `stage_type`
- `run_index`
- `status`
- `triggered_by`
- `trigger_reason`
- `execution_mode_snapshot`
- `summary`
- `checkpoint_summary`
- `failed_reason`
- `started_at`
- `ended_at`
- `created_at`

### 推荐索引

- `idx_stage_runs_project_id`
- `idx_stage_runs_project_stage_type`
- `idx_stage_runs_project_status`
- `idx_stage_runs_started_at`

### 关键约束

- 同一项目、同一 `stage_type` 可有多次 `run_index`
- `run_index` 应按项目 + 阶段类型递增
- 同一项目理论上可有多个历史 `stage_runs`，但主推进中的活跃阶段应由应用层控制

---

## 4. 关系表设计

## 4.1 `asset_links`

### 定位

表达资产之间的依赖和派生关系。

### 建议字段

- `id`
- `project_id`
- `from_asset_id`
- `to_asset_id`
- `relation_type`
- `created_at`

### `relation_type` 建议值

- `derived_from`
- `depends_on`
- `supersedes`
- `supports`

### 推荐索引

- `idx_asset_links_from_asset_id`
- `idx_asset_links_to_asset_id`
- `idx_asset_links_project_id`

---

## 4.2 `decision_assets`

### 定位

表达一个 `Decision` 关联哪些资产。

### 建议字段

- `id`
- `decision_id`
- `asset_id`
- `relation_role`
- `created_at`

### `relation_role` 建议值

- `primary_subject`
- `context_input`
- `comparison_candidate`
- `approval_target`

### 为什么不直接放 `related_asset_ids_json`

因为：

- 后续查询和过滤会很常见
- 一对多关系结构化后更容易做页面聚合

P0 即使保留 JSON 辅助字段，也建议同步有关系表。

---

## 4.3 `stage_run_assets`

### 定位

表达某次阶段运行消费和产出了哪些资产。

### 建议字段

- `id`
- `stage_run_id`
- `asset_id`
- `direction`
- `created_at`

### `direction` 建议值

- `input`
- `output`
- `reference`

### 价值

这是新链路非常重要的一张表，因为它把：

- 阶段推进
- 正式产物
- 输入输出关系

显式挂起来了。

---

## 5. 执行层表建议

## 5.1 `tasks`

### 是否 P0 必做

不是第一天必做，但很快会需要。

### 建议字段

- `id`
- `project_id`
- `stage_run_id`
- `title`
- `description`
- `status`
- `priority`
- `owner_agent`
- `depends_on_task_ids_json`
- `blocking_reason`
- `created_at`
- `updated_at`

### 建议

P0 阶段可以先做简化版，不急着把任务依赖完全结构化。

---

## 5.2 `agent_runs`

### 定位

记录某个 Agent 在某次阶段推进中的实际工作实例。

### 建议字段

- `id`
- `project_id`
- `stage_run_id`
- `agent_name`
- `goal`
- `status`
- `input_refs_json`
- `output_refs_json`
- `started_at`
- `ended_at`
- `log_ref`

### 说明

这张表把 Agent 从“产品主角”降回“执行观察对象”，但仍保留足够观测能力。

---

## 6. 审计表迁移方案

当前审计表：

- `llm_calls`
- `tool_calls`
- `events`

已经有价值，不建议推倒重来。

## 6.1 `llm_calls`

### 当前主要引用

- `run_id -> pipeline_runs`
- `stage_id -> pipeline_stages`

### 建议新增字段

- `project_id`
- `stage_run_id`
- `agent_run_id`
- `decision_id`（可选）
- `asset_id`（可选）

### 迁移策略

- 旧字段先保留
- 新写入优先补新字段
- 新页面和新聚合查询优先读新字段

---

## 6.2 `tool_calls`

### 建议新增字段

- `project_id`
- `stage_run_id`
- `agent_run_id`
- `decision_id`（可选）
- `asset_id`（可选）

### 策略

与 `llm_calls` 一致。

---

## 6.3 `events`

### 当前问题

`events` 主要围绕：

- `run_id`
- `event_type`
- `stage_name`
- `payload`

它现在更像旧 pipeline 审计事件流。

### 建议新增字段

- `project_id`
- `stage_run_id`
- `decision_id`
- `asset_id`
- `event_scope`

### `event_scope` 建议值

- `project`
- `stage_run`
- `decision`
- `asset`
- `agent_run`
- `legacy_pipeline`

### 价值

这样新事件系统就可以逐步从：

- 以旧 run/stage 为中心

迁移到：

- 以新业务对象为中心

---

## 7. 旧表处理策略

## 7.1 继续保留但降级的旧表

- `chatrooms`
- `messages`
- `pipelines`
- `pipeline_runs`
- `pipeline_stages`
- `stage_artifacts`
- `pipeline_messages`

### 作用

- 兼容旧页面
- 支撑旧执行链路
- 作为迁移期的对照数据

### 约束

新项目不应再把这些表作为主写入目标。

---

## 7.2 建议保留不动的表

- `agents`
- `memories`

### 原因

这些表不在本轮业务错位的核心路径上。

---

## 8. Migration 分阶段方案

## 8.1 Migration 1: 扩展 `projects` + 新增主表

目标：

- 扩展 `projects`
- 新增 `assets`
- 新增 `decisions`
- 新增 `stage_runs`
- 新增 `asset_links`
- 新增 `decision_assets`
- 新增 `stage_run_assets`

特点：

- 不删旧表
- 不改旧表行为
- 先把新世界容器搭起来

---

## 8.2 Migration 2: 执行层辅助表

目标：

- 新增 `tasks`
- 新增 `agent_runs`

这一步可以在主链路跑通后再加。

---

## 8.3 Migration 3: 审计表补新引用字段

目标：

- `llm_calls` 增加 `project_id`, `stage_run_id`, `agent_run_id`
- `tool_calls` 增加相同引用字段
- `events` 增加 `project_id`, `stage_run_id`, `decision_id`, `asset_id`, `event_scope`

特点：

- 旧字段保留
- 新旧可并行写入一段时间

---

## 8.4 Migration 4: 兼容层只读化准备

目标：

- 标记旧 pipeline/chatroom 相关表的主写入路径已迁出
- 为未来停写旧表做准备

这一步不一定需要立刻改 schema，但需要配套在服务层实现。

---

## 9. 推荐字段类型与实现建议

## 9.1 P0 阶段可接受的 JSON 文本字段

以下内容在 SQLite/P0 阶段可先用 `Text(JSON)`：

- `target_users_json`
- `target_platforms_json`
- `references_json`
- `source_input_refs_json`
- `alternative_options_json`
- `depends_on_task_ids_json`
- `input_refs_json`
- `output_refs_json`

### 原因

- P0 仍在快速演化
- 避免过早过度拆表
- 先把主关系结构化，细节数组可暂时 JSON 化

## 9.2 建议优先结构化的关系

不要偷懒塞 JSON 的关系：

- 资产依赖关系
- 决策与资产关系
- 阶段与资产输入输出关系

这些必须有专门表。

---

## 10. 示例 DDL 轮廓

以下不是最终 migration SQL，只是帮助对齐结构的轮廓示意。

```sql
ALTER TABLE projects ADD COLUMN slug TEXT;
ALTER TABLE projects ADD COLUMN one_line_vision TEXT;
ALTER TABLE projects ADD COLUMN current_stage TEXT;
ALTER TABLE projects ADD COLUMN execution_mode TEXT DEFAULT 'autopilot';
ALTER TABLE projects ADD COLUMN health_status TEXT DEFAULT 'healthy';
ALTER TABLE projects ADD COLUMN blocking_reason TEXT;
ALTER TABLE projects ADD COLUMN current_focus TEXT;
ALTER TABLE projects ADD COLUMN latest_summary TEXT;
ALTER TABLE projects ADD COLUMN last_activity_at DATETIME;
ALTER TABLE projects ADD COLUMN released_at DATETIME;
ALTER TABLE projects ADD COLUMN legacy_mode BOOLEAN DEFAULT 0;

CREATE TABLE assets (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  asset_type TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT,
  content_json TEXT,
  content_markdown TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'draft',
  is_current BOOLEAN NOT NULL DEFAULT 1,
  owner_agent TEXT,
  produced_by_stage_run_id INTEGER,
  supersedes_asset_id INTEGER,
  approval_decision_id INTEGER,
  source_input_refs_json TEXT,
  storage_path TEXT,
  created_at DATETIME,
  updated_at DATETIME,
  approved_at DATETIME
);

CREATE TABLE decisions (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  stage_run_id INTEGER,
  decision_type TEXT NOT NULL,
  title TEXT NOT NULL,
  context_summary TEXT,
  recommended_option TEXT,
  alternative_options_json TEXT,
  impact_summary TEXT,
  requested_action TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  resolved_option TEXT,
  resolution_note TEXT,
  blocking_stage_run_id INTEGER,
  created_by_system_reason TEXT,
  created_at DATETIME,
  resolved_at DATETIME,
  expires_at DATETIME
);

CREATE TABLE stage_runs (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  stage_type TEXT NOT NULL,
  run_index INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'queued',
  triggered_by TEXT,
  trigger_reason TEXT,
  execution_mode_snapshot TEXT,
  summary TEXT,
  checkpoint_summary TEXT,
  failed_reason TEXT,
  started_at DATETIME,
  ended_at DATETIME,
  created_at DATETIME
);
```

---

## 11. 开工建议

如果下一步开始落代码，最稳的实现顺序是：

1. 先改 ORM / schema，扩展 `projects` 并新增 `assets/decisions/stage_runs`
2. 再加关系表 `asset_links/decision_assets/stage_run_assets`
3. 先打通“创建项目 -> Brief -> scope_confirmation -> brief_confirmed”最小链路
4. 然后再补 `tasks/agent_runs`
5. 最后再改审计表引用字段

这个顺序的好处是：

- 先立住主业务语义
- 不会一上来就陷入旧审计/旧执行兼容泥潭
- 更容易用最小闭环验证方向是否正确

---

## 12. 一句话总结

**Catown 的数据库重构应采用“扩展 `projects`、新增 `assets / decisions / stage_runs` 主表、用关系表显式表达资产链与阶段输入输出、审计表渐进补新引用字段、旧 chatroom/pipeline 表先降级保留”的策略；目标不是一次性清空旧世界，而是先让新项目在数据库层完整活在新内核里。**
