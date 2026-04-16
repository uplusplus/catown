# ADR-026: Cockpit 交互设计建议

**日期**: 2026-04-16
**状态**: 待确认
**决策人**: BOSS
**相关**: PRD §8, ADR-010, ADR-011, ADR-025

---

## 1. 背景

Catown 采用"Cockpit（驾驶舱）"隐喻作为核心交互范式。航空驾驶舱解决的核心问题——**一个操作员管理复杂的自动化系统，同时保留终极控制权**——与 Catown BOSS 的角色完全同构。

当前 Cockpit 实现（`feature/business-architecture-refactor` 分支）做好了"仪表盘"层（三栏布局 + 状态指示），但缺少若干关键层级。

---

## 2. 隐喻映射

| 航空驾驶舱 | Catown BOSS |
|-----------|------------|
| 飞行员 | BOSS |
| 自动驾驶系统 | 6 个 Agent + Pipeline |
| 仪表盘（高度/速度/姿态） | 项目状态/阶段/产出物 |
| 告警灯 + 声音 | 决策阻塞/Gate 审批/Agent 报错 |
| 检查单 (Checklist) | Pipeline 阶段定义 |
| 塔台通信 | Agent 间消息 |
| 飞行记录仪 (黑匣子) | 审计日志 |
| 雷达（周围空域） | 多项目 Portfolio 视角 |

当前实现占了"仪表盘"的 70%，但检查单、塔台通信、黑匣子、雷达基本空白。

---

## 3. 建议分层架构

```
Layer 0: Fleet Overview      → 多项目概览（雷达）
Layer 1: Mission Control     → 单项目控制面（当前 Cockpit，保留三栏）
  ├── Pipeline Tracker       → 阶段 + 检查单（替代 StageLane）
  ├── Readiness Panel        → Gate + Release readiness（保留右栏）
  └── Inspector              → 产出物/决策/事件详情（保留 DetailRail）
Layer 2: Agent Telemetry     → Agent 内部仪表（新增）
  ├── Engine Gauges          → 每个 Agent 的 token/tool/turn 指标
  ├── Live Feed              → 实时行为滚动流（SSE 推送）
  └── Alert System           → 异常告警 + 处置按钮
Layer 3: Comm Channel        → Agent 间通信 + BOSS 指令（新增）
```

当前实现覆盖 Layer 1 的约 70%。Layer 0/2/3 基本空白。

---

## 4. 具体建议

### 4.1 缺"发动机仪表"——Agent 内部状态不可见

航空仪表盘不会只告诉你"飞机正在飞行中"，它告诉你每个引擎的转速、温度、油压、推力。Catown 应该为每个 Agent 提供类似的"引擎仪表"面板。

**建议：在 Cockpit 中增加 Agent Telemetry 面板。**

展示维度：
- 当前状态（空闲/运行中/阻塞/出错）
- LLM 模型 + 累计 token 消耗
- 当前 turn 编号 + 工具调用次数 + 失败次数
- 当前正在做什么（thinking preview + 工具调用详情）
- 最近输出摘要 + 下一步意图

这不需要渲染完整对话历史——是仪表化：展示关键指标 + 当前行为 + 下一步意图。类似飞机仪表不显示引擎的每颗螺丝，只显示转速和温度。

### 4.2 缺"检查单系统"——阶段检查单

飞行员在每个阶段（起飞前、巡航、降落）都有标准化检查单。Catown 的 Pipeline 阶段也应该有结构化检查单，实时跟踪完成进度。

**建议：每个 Stage 定义一个 checklist，自动检测完成状态。**

示例（development stage）：
- ✅ 项目结构就绪（自动检测 `ls src/ && ls tests/`）
- ✅ 核心功能实现（自动检测 `grep -r 'def ' src/`）
- 🔄 单元测试通过（自动检测 `pytest` 结果）
- ⬜ 代码质量检查（自动检测 `ruff check`）

前端显示为进度条形式，BOSS 一眼看出卡在哪一步，而不是等 Agent 跑完才知道。

**配置格式建议（`pipelines.json`）：**

```json
{
  "name": "development",
  "agent": "developer",
  "checklist": [
    {
      "id": "setup",
      "label": "项目结构就绪",
      "auto_detect": "ls src/ && ls tests/"
    },
    {
      "id": "impl",
      "label": "核心功能实现",
      "auto_detect": "grep -r 'def ' src/"
    },
    {
      "id": "tests",
      "label": "单元测试通过",
      "auto_detect": "pytest tests/ --tb=no -q"
    },
    {
      "id": "lint",
      "label": "代码质量检查",
      "auto_detect": "ruff check src/"
    }
  ]
}
```

### 4.3 缺"塔台频率"——实时通信流

飞机驾驶舱有无线电，飞行员能听到其他飞机和塔台的通信。当前实现的 ActivityFeed 是 REST 轮询，不是实时的。

**建议：Agent 的所有行为通过 SSE 流式推送，形成实时行为流。**

关键不是把所有内容推给前端（token 爆炸），而是推事件摘要：

```typescript
type CockpitEvent =
  | { type: "agent_turn_start"; agent: string; turn: number }
  | { type: "agent_thinking"; agent: string; preview: string }      // 前 50 字
  | { type: "tool_called"; agent: string; tool: string; status: "ok" | "error" }
  | { type: "file_changed"; path: string; delta: "+12/-3" }
  | { type: "test_result"; passed: number; failed: number }
  | { type: "agent_message"; from: string; to: string; preview: string }
  | { type: "checkpoint_hit"; checklist_id: string; label: string }
  | { type: "gate_blocked"; stage: string; reason: string }
```

前端把这些事件流化为一行一行的滚动日志，配上每个 Agent 的颜色标记：

```
12:01:23  [developer] 🟢 Turn 7 started
12:01:24  [developer] 🧠 "需要检查 auth 模块的接口定义..."
12:01:25  [developer] 🔧 read_file src/auth.py → 1.2KB ✅
12:01:27  [developer] 🔧 execute_code pytest tests/test_auth.py → 3 passed, 1 failed
12:01:28  [developer] 🧠 "test_login_with_expired_token 失败..."
12:01:30  [developer] ✏️ write_file src/auth.py (+8/-2)
12:01:32  [developer] 🔧 execute_code pytest tests/test_auth.py → 4 passed ✅
12:01:32  [developer] ✅ checkpoint: unit tests passed (4/4)
```

这是"仪表化"而非"聊天室化"——BOSS 看到的是行为摘要，不是原始对话。

### 4.4 缺"多空域雷达"——多项目并行视图

飞行员看雷达不只关心自己的航线，还关心周围空域。BOSS 应该能同时监控多个项目。

**建议：Cockpit 顶部加一个 Fleet Overview 概览条。**

```
┌─ Fleet Overview ──────────────────────────────────────────────┐
│  🟢 用户管理系统    [development]   developer   73%  →2h     │
│  🟡 订单服务        [testing]       tester      55%  →4h     │
│  🔴 支付网关        [analysis]      analyst     —   ⚠ blocked│
│  ⚪ 数据平台        [idle]          —           —             │
└────────────────────────────────────────────────────────────────┘
```

点击任何一个项目直接切到该项目的 Cockpit 视图。红色项目有告警需要立即关注。

### 4.5 缺"紧急程序"——异常处理协议

飞行中有标准操作程序（SOP）应对异常。Catown 的 Agent 出错时，当前只是在事件里标记失败。BOSS 需要明确的异常处置协议。

**建议：定义 Agent 异常的自动处置 + BOSS 介入流程。**

| 异常类型 | 自动处置 | BOSS 介入 |
|---------|---------|----------|
| 工具调用失败 (3次) | Agent 自动重试 | 超过阈值弹告警 |
| LLM 调用超时 | 降级到备用模型 | 通知 BOSS |
| 阶段超时 (预设) | 暂停 + 通知 | BOSS 决定继续/终止 |
| 产出物不达标 | 打回重做 | BOSS 审批 |
| 依赖冲突 | 暂停 + 告警 | BOSS 介入 |

在 Cockpit 中，异常以告警灯形式呈现，附带处置按钮：

```
┌─ ⚠️ Alert ──────────────────────────────────┐
│ developer: execute_code 连续失败 3 次        │
│ 最后错误: ModuleNotFoundError: No module     │
│           named 'pydantic_settings'          │
│ Agent 已自动: 安装依赖失败（权限不足）        │
│ [🔧 手动修复]  [🔄 跳过此步骤]  [⏸️ 暂停]  │
└──────────────────────────────────────────────┘
```

### 4.6 命名和信息架构改善

当前 Cockpit 用了航空术语但语义不完全匹配。如果要借航空隐喻，建议借全套：

| 当前命名 | 问题 | 建议 |
|---------|------|------|
| "Navigation Core" | 太抽象，不懂导航什么 | **"Mission Control"（任务控制）** |
| "Stage Lane" | Lane 在 UI 里通常指列 | **"Pipeline Tracker"** 或 **"Stage Timeline"** |
| "Status Rail" | Rail 无意义 | **"Readiness Panel"（就绪面板）** |
| "Detail Rail" | 同上 | **"Inspector"（检查器）** |
| "Activity Feed" | 正确但被动 | **"Live Feed"** 或 **"Comm Channel"（通信频道）** |
| "User Action Area" | 像软件测试术语 | **"Decision Center"（决策中心）** |
| "Cockpit-First Homepage" | 标题不该有 "First" | 直接叫 **"Catown Cockpit"** |

核心原则：混合隐喻（部分航空、部分软件、部分 generic）会让用户困惑。要借就借全套。

---

## 5. 实施优先级

| 排名 | 建议 | 影响 | 预估工作量 |
|------|------|------|-----------|
| 1 | Agent Telemetry 面板 | 直接回答"Agent 在干什么" | 3-4 天 |
| 2 | SSE 实时事件流 | 取代 REST 轮询，实时感知 | 2-3 天 |
| 3 | 阶段检查单 | 消除"阶段黑箱" | 2 天 |
| 4 | 异常告警系统 | Agent 出错时 BOSS 能及时介入 | 2 天 |
| 5 | Fleet Overview | 多项目管理能力 | 2-3 天 |
| 6 | 命名统一 | 用户体验一致性 | 半天 |

建议先做 Layer 2（Agent Telemetry），因为这直接回答 BOSS 的核心问题："Agent 现在在干什么？"
