# ADR-007: UI/UX Pro Max Skill 集成可行性

**日期**: 2026-04-10
**状态**: Phase 1 进行中 — P0 全部完成 ✅
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 的 Skill 体系当前全部是**文本/代码导向**（code-generation、unit-testing 等）。随着 AI 软件工厂的目标推进，需要引入 UI/UX 专业设计类 Skill，让 Agent 能够生成高质量前端界面并闭环验证。

## 问题

当前架构能否支撑 UI/UX Pro Max 类 Skill 的完整工作流？

## 分析

### UI/UX Skill 需要的核心能力

| 能力 | 说明 |
|------|------|
| 视觉生成 | 生成 HTML/CSS/组件代码 |
| 视觉验证 | 截图预览，确认渲染效果 |
| 迭代闭环 | 生成 → 截图 → 对比 → 修改 |
| 设计规范注入 | 设计系统/tokens/组件库约束 |
| 响应式测试 | 多分辨率截图对比 |
| 设计稿解析 | 读取截图/设计稿并理解 |

### 现状评估

| 维度 | 现状 | 差距 | 优先级 |
|------|------|------|--------|
| Skill 配置框架 | ✅ 够用 | 无 | — |
| SOUL 注入 | ✅ 够用 | 无 | — |
| Pipeline 扩展 | ✅ 够用 | 无 | — |
| 工具层（screenshot/browser） | ❌ 缺失 | 需新增 | **P0** |
| 执行环境（Node.js/浏览器） | ❌ 缺失 | 需新增 | **P0** |
| 专门 UI Agent 角色 | ❌ 缺失 | 需新增 | P1 |
| 截图式审计/记忆 | ❌ 缺失 | 需新增 | P1 |
| 设计资产产出物类型 | ⚠️ 不足 | 需扩展 | P2 |
| 设计稿解析 | ❌ 缺失 | 需新增 | P2 |

### 关键结论

**Skill 配置框架和注入机制完全能承载** UI/UX 类 Skill，但**工具层和执行环境是硬瓶颈**。

没有 screenshot + browser 工具，Agent 无法形成「生成→预览→修改」的迭代闭环，只能盲写前端代码。

## 决策

分三阶段引入：

### Phase 1 — 硬门槛（必须先做）

| 新增 | 状态 | 说明 |
|------|------|------|
| `screenshot` 工具 | ✅ 完成 | Headless Chromium 截图，支持全页面/指定元素/多分辨率 |
| `browser` 工具 | ✅ 完成 | Playwright 自动化（navigate/click/fill/type/screenshot/evaluate 等 15 个动作） |
| `execute_code` 增强 | ✅ 完成 | Python + Node.js 双语言沙箱，危险模块拦截 |
| `ui-designer` Agent | ⏳ 待做 | 专门的 UI 设计师角色，独立 SOUL |

### Phase 2 — 闭环验证

| 新增 | 说明 |
|------|------|
| 截图对比能力 | baseline vs actual 差异检测 |
| 截图式审计 | 截图存入审计日志和记忆体系 |
| `ui-ux-pro-max` Skill | 完整的 prompt_fragment + 工具绑定 |

### Phase 3 — 高级能力

| 新增 | 说明 |
|------|------|
| 设计稿解析 | 图片 → 结构化需求 |
| 多分辨率响应式测试 | 自动化 viewport 切换 |
| 设计资产产出物管理 | design_spec/component/screenshot 类型 |

## 影响

### 需要修改的模块

| 模块 | 改动 |
|------|------|
| 工具注册 (`tools/`) | 新增 screenshot、browser 工具实现 |
| `execute_code` | 增加 Node.js 运行时支持 |
| `agents.json` | 新增 ui-designer 角色 |
| `skills.json` | 新增 ui-ux-pro-max Skill |
| `pipelines.json` | Pipeline 中可引用 ui-designer |
| 审计体系 | 支持截图类型的审计记录 |
| 记忆体系 | 支持截图标注存入长期记忆 |
| 产出物模型 | 新增设计资产类型 |
| 前端 Dashboard | 产出物预览支持截图展示 |

### 不需要修改的部分

- Skill 配置框架（完全兼容）
- SOUL 注入机制（完全兼容）
- Pipeline 阶段流转机制（完全兼容）
- Agent 白名单/临时授权机制（完全兼容）

## 参考

- PRD §4.5 — 工具/技能白名单机制
- PRD §4.2 — Agent SOUL 体系
- PRD §5 — Pipeline 工作流引擎
- ADR-006 — OMNI 多模态能力集成方案
