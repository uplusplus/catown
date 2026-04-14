# ADR-017: 主 API 设计稿（Project / Asset / Decision / StageRun）

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-014-project-flow-and-state-machine.md`, `docs/ADR-015-current-architecture-gap-analysis.md`, `docs/ADR-016-core-domain-model.md`

---

## 1. 目标

本文档的目标，是把 Catown 下一阶段的主 API 边界正式定义出来。

要回答的问题是：

**在新的 `Project / Asset / Decision / StageRun` 内核下，前端、自动推进逻辑、执行层和兼容层分别应该通过哪些 API 读写什么对象？**

本文档重点解决：

- 资源边界怎么划分
- 哪些接口是主入口
- 哪些动作是资源更新，哪些动作是命令型接口
- 状态迁移由谁触发
- 旧 chatroom/pipeline API 在新世界里应该如何降级

---

## 2. API 设计原则

### 2.1 资源优先，命令补充

主 API 应遵循：

- 用资源型接口表达稳定业务对象
- 用命令型接口表达明确业务动作

因此主结构应以以下资源为中心：

- `projects`
- `assets`
- `decisions`
- `stage-runs`

必要时补充命令型端点，例如：

- `POST /projects/:id/continue`
- `POST /decisions/:id/resolve`
- `POST /stage-runs/:id/retry`

### 2.2 主 API 服务产品层，不服务旧引擎心智

新 API 的主要消费者应该是：

- Dashboard
- Mission Board
- Release Center
- 系统自动推进协调器

而不是旧的：

- chat-first 前端状态流
- pipeline-first 页面心智
- 直接面向内部引擎对象的客户端

### 2.3 列表接口表达“读模型”，详情接口表达“工作对象”

建议区分：

#### 列表/概览接口

用于首页和看板聚合：

- 返回摘要字段
- 避免塞入大正文
- 优先解决“现在该看什么”

#### 详情接口

用于单资源操作：

- 返回完整对象
- 包含正文、关系、状态和相关动作
- 优先解决“这个对象到底是什么、现在能做什么”

### 2.4 状态更新必须可解释

不能允许前端随意 PATCH 一个状态值，导致业务语义失真。

例如：

- 不建议前端直接 `PATCH /projects/:id { status: released }`
- 应通过 `POST /decisions/:id/resolve` 或 `POST /projects/:id/release/approve` 来完成

原则：

**状态迁移必须附着在有业务语义的动作上。**

### 2.5 兼容层不进入新主命名

旧对象如果保留：

- `chatrooms`
- `messages`
- `pipelines`

也不应再进入新页面的主查询链路。

它们最多作为：

- 审计视图
- 协作视图
- 调试视图

---

## 3. 主资源划分

### 3.1 Project APIs

`Project` 是全局业务聚合根。

它负责：

- 项目列表
- 项目详情
- 项目状态变更入口
- 项目推进命令入口

#### 推荐接口

- `POST /projects`
- `GET /projects`
- `GET /projects/:id`
- `PATCH /projects/:id`
- `POST /projects/:id/continue`
- `POST /projects/:id/replan`
- `GET /projects/:id/overview`
- `GET /projects/:id/release`

### 3.2 Asset APIs

`Asset` 是正式产物对象。

它负责：

- 按项目列出资产
- 查看资产正文和版本链
- 重新生成资产
- 批准资产（或由决策间接批准）

#### 推荐接口

- `GET /projects/:id/assets`
- `GET /assets/:id`
- `GET /assets/:id/versions`
- `GET /assets/:id/links`
- `PATCH /assets/:id`
- `POST /assets/:id/regenerate`
- `POST /assets/:id/approve`

### 3.3 Decision APIs

`Decision` 是人工确认对象。

它负责：

- 拉取待处理决策
- 查看决策上下文
- 执行批准/拒绝

#### 推荐接口

- `GET /decisions`
- `GET /projects/:id/decisions`
- `GET /decisions/:id`
- `POST /decisions/:id/resolve`

### 3.4 StageRun APIs

`StageRun` 是阶段推进实例。

它负责：

- 查看阶段执行历史
- 查看当前阶段细节
- 重试阶段
- 取消阶段

#### 推荐接口

- `GET /projects/:id/stage-runs`
- `GET /stage-runs/:id`
- `POST /stage-runs/:id/retry`
- `POST /stage-runs/:id/cancel`

### 3.5 执行/审计 APIs

以下对象暂不进入主产品内核，但需要保留读取接口：

- `tasks`
- `agent-runs`
- `events`

#### 推荐接口

- `GET /projects/:id/tasks`
- `GET /projects/:id/agent-runs`
- `GET /stage-runs/:id/tasks`
- `GET /stage-runs/:id/agent-runs`
- `GET /projects/:id/events`

---

## 4. Project API 细化

### 4.1 POST /projects

#### 作用

创建项目，并初始化最小业务骨架。

#### 请求体建议

```json
{
  "name": "FitPet",
  "one_line_vision": "做一个帮助宠物主人管理饮食和运动的移动应用",
  "target_platforms": ["ios", "android"],
  "target_users": ["宠物主人"],
  "references": ["https://example.com/app-a"],
  "execution_mode": "autopilot"
}
```

#### 系统行为

- 创建 `Project(status=draft)`
- 创建初始 `project_brief` 草稿资产
- 返回项目摘要

#### 响应建议

```json
{
  "project": {
    "id": "proj_001",
    "name": "FitPet",
    "status": "draft",
    "current_stage": "briefing",
    "execution_mode": "autopilot"
  },
  "next_action": "generate_project_brief"
}
```

### 4.2 GET /projects

#### 作用

返回项目列表，服务 Dashboard。

#### 查询参数建议

- `status`
- `health_status`
- `limit`
- `cursor`
- `view=dashboard`

#### 返回字段建议

只返回摘要：

- `id`
- `name`
- `status`
- `current_stage`
- `health_status`
- `current_focus`
- `pending_decision_count`
- `latest_summary`
- `last_activity_at`

### 4.3 GET /projects/:id

#### 作用

返回项目完整详情，服务 Mission Board 顶部区域。

#### 建议返回

- 基础信息
- 当前状态
- 当前阶段
- 推进模式
- 当前聚焦点
- 阻塞原因
- 最近决策引用
- 关键资产摘要

### 4.4 PATCH /projects/:id

#### 作用

更新项目的可直接编辑属性。

#### 允许修改的字段建议

- `name`
- `description`
- `execution_mode`
- `target_platforms`
- `one_line_vision`

#### 不建议允许直接修改的字段

- `status`
- `current_stage`
- `health_status`（除非有明确运维/人工 override 语义）

### 4.5 POST /projects/:id/continue

#### 作用

显式请求系统继续推进项目。

#### 使用场景

- Checkpoint 模式下，用户确认继续
- 项目从 `blocked` 恢复后重新推进
- 项目处于可推进状态，但尚未自动触发

#### 请求体建议

```json
{
  "reason": "user_continue",
  "note": "继续生成 PRD 和 UX Blueprint"
}
```

#### 核心规则

- 若存在 `pending` 且阻塞型 `Decision`，应拒绝继续
- 若当前已有 `running` 主 `StageRun`，应避免重复触发
- 响应中应返回被创建或激活的 `StageRun`

### 4.6 POST /projects/:id/replan

#### 作用

在当前方向出现问题时，触发重新规划。

#### 典型场景

- Brief 被多次拒绝
- 设计方向偏离
- 构建/测试反复失败，需要回到定义阶段

#### 业务语义

该接口不是简单 retry，而是：

- 重新评估当前资产链
- 更新项目焦点
- 可能创建新的 `Decision` 或新的 `StageRun`

### 4.7 GET /projects/:id/overview

#### 作用

为 Mission Board 提供聚合读模型。

#### 返回建议

一个聚合对象，包含：

- `project_summary`
- `current_stage_run`
- `key_assets`
- `pending_decisions`
- `open_tasks_summary`
- `recent_agent_runs`
- `release_readiness`

原则：

**这是页面聚合接口，不是原子资源替代品。**

### 4.8 GET /projects/:id/release

#### 作用

为 Release Center 提供聚合读模型。

#### 返回建议

- 当前 `build_artifact`
- 当前 `test_report`
- 当前 `release_pack`
- `release_approval` 决策
- `release_checklist_status`

---

## 5. Asset API 细化

### 5.1 GET /projects/:id/assets

#### 作用

按项目列出资产。

#### 查询参数建议

- `type`
- `status`
- `current_only=true|false`
- `include_superseded=true|false`

#### 返回建议

列表中每项至少包括：

- `id`
- `asset_type`
- `title`
- `version`
- `status`
- `summary`
- `updated_at`
- `is_current`

### 5.2 GET /assets/:id

#### 作用

查看单个资产详情。

#### 返回建议

- 基础字段
- 正文内容
- 来源阶段
- 输入引用
- 依赖链
- 是否已批准
- 可执行动作

### 5.3 GET /assets/:id/versions

#### 作用

查看同类资产版本链。

#### 返回建议

按时间或版本号返回：

- `id`
- `version`
- `status`
- `supersedes_asset_id`
- `created_at`
- `approved_at`
- `is_current`

### 5.4 GET /assets/:id/links

#### 作用

查看资产依赖关系。

#### 返回建议

```json
{
  "upstream": [
    { "asset_id": "asset_brief_v1", "relation": "derived_from" }
  ],
  "downstream": [
    { "asset_id": "asset_prd_v2", "relation": "produces" }
  ]
}
```

### 5.5 PATCH /assets/:id

#### 作用

允许人工编辑某些资产内容。

#### 适用对象

优先用于：

- `project_brief`
- `prd`
- `release_pack`

#### 不建议场景

- 对系统生成的构建产物做任意结构破坏式修改

#### 建议规则

- 如果资产已 `approved`，人工修改应生成新版本，而不是原地覆写
- PATCH 可以作为编辑入口，但服务端应决定是否转为新版本

### 5.6 POST /assets/:id/regenerate

#### 作用

基于当前上下文重新生成该资产。

#### 业务语义

这不是“保存编辑”，而是“请求系统重做”。

#### 请求体建议

```json
{
  "reason": "user_requested_revision",
  "note": "缩小 MVP 范围，去掉社区功能"
}
```

#### 核心规则

- 应创建新版本资产
- 必要时创建新的 `StageRun`
- 如果该资产关联活跃决策，应明确更新决策关系

### 5.7 POST /assets/:id/approve

#### 作用

批准某个资产。

#### 使用限制

不建议所有资产都通过这个接口批准。

更合理的是：

- 对明确的正式审阅动作可直接批准资产
- 对重大关口更推荐通过 `Decision.resolve` 驱动批准

#### P0 建议

- `project_brief` 可支持直接 approve
- 但 `release_pack` 更推荐由 `release_approval` 决策驱动

---

## 6. Decision API 细化

### 6.1 GET /decisions

#### 作用

拉取决策列表，服务 Dashboard 待办区。

#### 查询参数建议

- `status=pending`
- `project_id`
- `decision_type`
- `limit`

#### 返回摘要建议

- `id`
- `project_id`
- `decision_type`
- `title`
- `recommended_option`
- `status`
- `created_at`

### 6.2 GET /projects/:id/decisions

#### 作用

查看项目的全部决策历史。

#### 返回建议

- 待处理决策优先
- 包含历史决策
- 明确每个决策关联的 `StageRun` 和 `Asset`

### 6.3 GET /decisions/:id

#### 作用

查看单个决策详情。

#### 返回建议

- 基础字段
- `context_summary`
- `recommended_option`
- `alternative_options`
- `impact_summary`
- `related_assets`
- `requested_action`
- `allowed_resolutions`

### 6.4 POST /decisions/:id/resolve

#### 作用

批准或拒绝一个决策。

#### 请求体建议

```json
{
  "resolution": "approved",
  "selected_option": "accept_scope_v2",
  "note": "范围清晰了，继续推进"
}
```

#### 核心规则

- 只允许处理 `pending` 决策
- 决策处理后必须触发明确副作用，例如：
  - 更新资产状态
  - 更新项目状态
  - 创建下一条 `StageRun`
  - 关闭当前阻塞

#### 典型副作用举例

##### `scope_confirmation` 被批准

- `project_brief -> approved`
- `Project: draft -> brief_confirmed`
- 创建 `product_definition` 的 `StageRun`

##### `release_approval` 被拒绝

- `Project` 保持 `release_ready` 或回退到 `testing`
- `release_pack` 保持 `in_review` 或生成新版本
- 可触发重新准备发布材料

---

## 7. StageRun API 细化

### 7.1 GET /projects/:id/stage-runs

#### 作用

查看项目的阶段历史和当前阶段。

#### 查询参数建议

- `status`
- `stage_type`
- `limit`
- `current_only=true|false`

### 7.2 GET /stage-runs/:id

#### 作用

查看某次阶段推进的完整细节。

#### 返回建议

- 基础字段
- 输入资产
- 输出资产
- 相关决策
- 状态变化
- 阶段摘要
- 关联任务和 AgentRun 的摘要计数

### 7.3 POST /stage-runs/:id/retry

#### 作用

重试一次失败或被拒绝后的阶段。

#### 业务语义

应新建一条 `StageRun`，而不是复活旧记录。

#### 请求体建议

```json
{
  "reason": "fix_after_rejection",
  "note": "按新的 Brief 重新生成 PRD"
}
```

#### 核心规则

- 原 `StageRun` 保持历史状态不变
- 新 `StageRun` 继承必要输入资产
- 必要时创建新的资产版本链

### 7.4 POST /stage-runs/:id/cancel

#### 作用

取消当前阶段推进。

#### 使用场景

- 用户明确停止
- 模式切换
- 需要改走重新规划路径

#### 核心规则

- 只能取消 `queued` 或 `running` 的 `StageRun`
- 被取消的阶段不自动改写相关资产状态，除非有显式清理逻辑

---

## 8. 页面聚合接口建议

除了原子资源接口，建议增加少量页面级聚合接口，降低前端拼装复杂度。

### 8.1 GET /dashboard

#### 作用

为首页返回聚合数据。

#### 返回建议

- `projects`
- `pending_decisions`
- `recent_assets`
- `active_stage_runs`
- `alerts`

### 8.2 GET /projects/:id/overview

#### 作用

为 Mission Board 返回完整项目看板数据。

#### 返回建议

- `project`
- `current_stage_run`
- `key_assets`
- `pending_decisions`
- `open_tasks_summary`
- `recent_activity`
- `recommended_next_action`

### 8.3 GET /projects/:id/release

#### 作用

为 Release Center 返回发布聚合视图。

#### 返回建议

- `build_artifact`
- `test_report`
- `release_pack`
- `release_approval`
- `checklist`
- `blocking_items`

原则：

这些接口是读优化，不替代基础资源接口。

---

## 9. 状态迁移与 API 职责边界

### 9.1 哪些状态可以 PATCH，哪些必须走命令接口

#### 可以直接 PATCH 的

- 项目名称、描述、目标平台
- 某些资产的可编辑内容
- 非关键展示性字段

#### 必须走命令接口的

- 项目继续推进
- 重新规划
- 决策批准/拒绝
- 阶段重试/取消
- 发布批准

### 9.2 服务端必须掌握的业务逻辑

以下逻辑不能下放给前端拼：

- `Project.status` 如何变化
- 哪个 `StageRun` 才算当前主推进
- 资产批准后是否生成下阶段
- 决策处理后的副作用链
- 发布是否满足前置条件

### 9.3 幂等性建议

对于命令型接口，建议支持幂等控制，避免重复点击造成双重推进。

适合幂等控制的接口：

- `POST /projects/:id/continue`
- `POST /decisions/:id/resolve`
- `POST /stage-runs/:id/retry`

---

## 10. 旧 API 的降级策略

### 10.1 chatroom APIs

旧接口如：

- `/chatrooms/{id}/messages`
- `/chatrooms/{id}/messages/stream`

新定位应为：

- 协作记录读取接口
- 审计/上下文查看接口
- 调试辅助接口

不再用于：

- 项目主推进
- 页面主状态来源
- 决策入口

### 10.2 pipeline APIs

旧接口如：

- `/pipelines/*`

新定位应为：

- 内部执行编排接口
- 兼容层接口
- 暂时性适配层接口

不再作为：

- 前端主页面主数据源
- 外部产品接口中心

### 10.3 兼容策略建议

P0 阶段允许：

- 新页面调用新 API
- 旧页面继续调用旧 API
- 中间通过适配器对接底层引擎

但必须避免：

- 新页面直接混用旧 chatroom/pipeline 作为主读模型
- 新资源接口只是旧接口换皮代理

---

## 11. P0 最小 API 闭环

如果只做一个最小可跑版本，建议先落以下接口：

### 11.1 Project

- `POST /projects`
- `GET /projects`
- `GET /projects/:id`
- `POST /projects/:id/continue`

### 11.2 Asset

- `GET /projects/:id/assets`
- `GET /assets/:id`
- `PATCH /assets/:id`

### 11.3 Decision

- `GET /projects/:id/decisions`
- `GET /decisions/:id`
- `POST /decisions/:id/resolve`

### 11.4 StageRun

- `GET /projects/:id/stage-runs`
- `GET /stage-runs/:id`

### 11.5 聚合读模型

- `GET /dashboard`
- `GET /projects/:id/overview`

这组接口足够支撑：

1. 创建项目
2. 生成/查看 Brief
3. 处理 `scope_confirmation`
4. 进入下一阶段
5. 在看板上展示项目推进状态

---

## 12. 一句话总结

**Catown 的主 API 应以 `projects / assets / decisions / stage-runs` 四类资源作为稳定边界，以 `continue / resolve / retry / replan` 等命令接口承载关键业务动作，并通过少量聚合读模型服务 Dashboard、Mission Board 与 Release Center；旧 `chatroom` 与 `pipeline` 接口只能降级为兼容层和辅助视图，不能再充当产品主入口。**
