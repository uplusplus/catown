# ADR-022: Project-First 后端分层收口与继续下拆的目的

**日期**: 2026-04-15
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-020-service-layer-and-implementation-plan.md`, `docs/ADR-021-pipeline-disposition-and-stage-execution-kernel.md`, `docs/ADR-019-schema-and-migration-plan.md`

---

## 1. 背景

在完成 `projects` 与 `events` 的 Alembic migration 收口、移除 `main.py` 对 legacy `pipeline` 的启动期耦合之后，Catown 的 project-first v2 后端已经从“概念上成立”推进到了“可以启动、可以迁移、关键接口可回归”。

但这并不意味着当前结构已经适合作为前端对接底座。

当前代码仍存在一个中期风险：

- `ProjectService` 同时承担业务推进、查询聚合、接口返回 shape 组装
- `routes/*_v2.py` 中仍夹杂部分读模型拼装和存在性校验
- dashboard / overview / detail 这类读模型虽然已经出现，但边界还不够稳定

如果此时直接大规模推进前端，就会让前端绑定在一个仍在移动的后端 contract 上。

---

## 2. 本次决策

### 2.1 决策一句话

**继续向下拆的目的，不是为了代码好看，而是为了把 project-first 后端整理成可继续演进、可稳定对接前端的分层底座。**

### 2.2 当前阶段的核心目标

本阶段继续下拆，目标明确限定为以下三项：

1. 把“业务动作”和“读模型 / 返回 shape”分开
2. 防止 `ProjectService` 重新膨胀成万能总管
3. 让 `/api/v2/*` 的 contract 更稳定，成为后续前端改造的可靠靶子

---

## 3. 为什么要继续下拆

### 3.1 分离业务推进与接口展示

业务推进关注的是：

- 项目状态流转
- 阶段推进
- 决策 resolve 副作用
- 资产生成与依赖关系

接口展示关注的是：

- dashboard 需要什么摘要
- overview 需要什么聚合块
- asset detail / stage-run detail 需要什么返回结构

这两者变化频率不同，也不应该被同一批函数耦在一起。

如果继续把它们混在 `ProjectService` 或路由里：

- 改一个字段 shape，容易误伤业务流转
- 改一个业务步骤，容易误伤 dashboard/detail 的返回结构
- 测试只能依赖大而全的回归，定位成本高

因此需要明确把读模型能力往 `read_models/` 收拢。

### 3.2 防止 `ProjectService` 重新变成新的“上帝类”

如果 `ProjectService` 同时负责：

- create / continue / resolve
- dashboard 聚合
- project overview 聚合
- asset detail 聚合
- stage-run detail / events 聚合
- release readiness 计算
- link serialization

那么它会重新长成一个高度耦合的总控类。

这和我们刚刚决定弱化 legacy `pipeline` 主导地位的方向是冲突的。

因此，`ProjectService` 的角色应该是：

- 业务门面
- 事务边界持有者
- orchestration 调用入口
- route 与 read-model / coordinator 之间的胶水层

而不是所有数据聚合逻辑的最终归宿。

### 3.3 稳定 v2 contract，给前端一个固定靶子

现在不大动前端，不是因为前端不重要，而是因为后端 contract 仍在收口。

继续下拆的直接收益是：

- `/api/v2/dashboard`
- `/api/v2/projects`
- `/api/v2/projects/{id}/overview`
- `/api/v2/stage-runs/{id}`
- `/api/v2/stage-runs/{id}/events`

这些接口的返回 shape 会更集中地定义在 read-model 层，而不是零散地散在 route/service 里。

这样后面前端接入时，面对的是一个更稳定的 contract，而不是一个边改边猜的目标。

---

## 4. 目标分层

当前 project-first 后端的目标分层如下。

```text
HTTP Routes
  -> Services
    -> Orchestration / Coordinators
    -> Read Models
    -> AssetService 等领域服务
      -> Models / Alembic
```

### 4.1 `routes/*_v2.py`

职责：

- 接收参数
- 调用 service
- 负责最薄的 HTTP 翻译

不负责：

- 查询拼装
- 业务状态推进细节
- dashboard / overview / detail shape 构造

### 4.2 `services/*`

职责：

- 提供业务动作入口
- 管理事务边界
- 调度 orchestration / read-model builder
- 向路由暴露较稳定的调用界面

不负责：

- 承载全部查询聚合细节
- 直接成为“唯一知道所有 shape 的地方”

### 4.3 `read_models/*`

职责：

- 负责 dashboard / overview / detail / link graph 这类查询与返回结构
- 让响应 shape 有明确归属
- 让前端 contract 的变更更可控

适合放入：

- `ProjectViewBuilder`
- `StageRunViewBuilder`
- `AssetViewBuilder`
- 未来若需要，可加 `DecisionViewBuilder`

### 4.4 `orchestration/*`

职责：

- 负责跨对象推进
- 处理项目流转与决策副作用
- 驱动 stage execution kernel

当前典型模块：

- `project_flow_coordinator.py`
- `decision_effects.py`
- `project_bootstrap_coordinator.py`

### 4.5 `models + alembic`

职责：

- 模型定义
- schema ownership
- migration 变更治理

关键原则：

**不再依赖运行时 patch/shim 去掩盖 schema 漂移。**

---

## 5. 当前已经发生的收口

截至本 ADR 落地时，本轮已完成的典型收口包括：

- `main.py` 移除 legacy `pipeline` 启动期挂载与桥接依赖
- `projects` 与 `events` 通过 Alembic migration 正式收口
- `read_models/stage_run_views.py` 承接 stage-run detail / events / stage-run list
- `read_models/project_views.py` 承接 overview/dashboard 相关聚合与 readiness/link 逻辑
- `read_models/asset_views.py` 承接 asset detail 与 dependency 视图
- `routes/assets_v2.py`、`routes/stage_runs_v2.py`、`routes/projects_v2.py` 明显变薄
- 为关键 v2 contract 补上最小 API 回归保护

这说明：

**分层并不是停留在设计文件里，而是已经进入增量落地阶段。**

---

## 6. 当前不追求的事

为了避免范围失控，本阶段明确不追求：

- 一次性把所有 service 再拆成很多小 service
- 立刻完成前端全面改造
- 立刻彻底删除 legacy `pipeline/`
- 为了纯理论整洁去大范围重命名或搬文件

本阶段优先级只有一个：

**把 project-first v2 后端整理成一个稳定、可继续演化的底座。**

---

## 7. 后续动作建议

建议后续继续按以下顺序推进：

1. 继续把 `decisions_v2.py` 收薄，统一返回 shape 入口
2. 盘点 `ProjectService` 里剩余仍偏读模型的函数
3. 保持关键 v2 contract 回归测试跟随重构同步更新
4. 在 contract 稳定后，再启动前端去 `/api/pipelines/*` 化

---

## 8. 一句话总结

**继续往下拆的真正目的，是让 Catown 的 project-first 后端从“现在能跑”变成“后面敢接前端、敢继续改、敢逐步替换旧架构”的工程底座。**
