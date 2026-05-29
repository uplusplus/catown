# Catown v2 — Architecture Decision Records

## 目录

| ADR | 标题 | 状态 |
|-----|------|------|
| [ADR-001](./ADR-001-monitor-performance.md) | Monitor/Network 页面性能问题根因分析 | Accepted |

## 改进计划

- [IMPROVEMENT-PLAN.md](./IMPROVEMENT-PLAN.md) — 基于 ADR-001 的可执行任务清单

## 核心结论

**不是 SQLite 的问题，是数据建模的问题。**

三层根因叠加：
1. runtime 数据以多层嵌套 JSON blob 存在 `messages.metadata_json` 里，每次查询 5+ 次 `json.loads()`
2. 列表/概览查询在 Python 层做过滤和聚合，而非 SQL
3. 单进程 + 中间件抄写响应体放大了争用

最关键的改进：**建立 `runtime_card_projections` 投影表**，写入时预提取 card 字段，读取时零 JSON parse。
