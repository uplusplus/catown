# ADR-001: Monitor/Network 页面性能问题根因分析与改进方案

- **状态**: Accepted
- **日期**: 2026-05-29
- **决策者**: Architecture Review
- **关联代码**: `backend/models/database.py`, `backend/monitoring/network_buffer.py`, `backend/routes/monitor.py`, `backend/services/monitor_projection.py`, `backend/main.py`

---

## 背景

Monitor/Network 页面加载缓慢，runtime-card 详情接口延迟抖动严重（同一接口 142ms ~ 5913ms），且响应大小与延迟无相关性（4.7KB 响应可达 2857ms，100KB 响应有时仅 212ms）。需要定位根因并制定改进方案。

## 问题陈述

三个独立的性能问题叠加形成了当前的慢查询体验：

1. **数据建模问题**：runtime 运行时数据以多层嵌套 JSON blob 形式存储在 `messages.metadata_json` 中，每次读取都需要多次 `json.loads()` + Python 遍历。
2. **查询模式问题**：列表/概览接口从 SQLite 取全量数据到 Python 再做过滤和聚合，而非在 SQL 层完成。
3. **运行时架构问题**：单 uvicorn worker、同步 DB 调用阻塞 async event loop、请求中间件抄写响应体到 telemetry 放大写入负载。

## 根因分析

### 根因 1：数据形态 —— runtime 数据与消息数据共用同一张表

**现状**：`messages` 表同时服务两种完全不同的访问模式：

| 访问模式 | 特征 | 关心字段 |
|---------|------|---------|
| 聊天窗口 (场景 A) | 按 chatroom 顺序写入/读取 | `content`, `message_type`, `created_at` |
| Monitor 监控 (场景 B) | 跨 chatroom 聚合/过滤/统计 | `metadata_json` 内嵌套的 `card.agent`, `card.tokens_in`, `card.tool` 等 |

场景 A 的最优 schema 是宽行文本表；场景 B 的最优 schema 是结构化列存表。当前方案用场景 A 的 schema 服务场景 B，导致每次 Monitor 查询都要做 **3 层 JSON 反序列化**：

```
messages.metadata_json (Text → json.loads #1)
  → {"card": "{...JSON string...}"}
    → json.loads #2
      → {"prompt_messages": "[...JSON string...]", "raw_response": "{...}", ...}
        → json.loads #3, #4, #5 (对每个嵌套 JSON string 字段)
```

一条 runtime_card 详情请求最多触发 5+ 次 `json.loads()`，涉及几十 KB 的字符串解析。这不是索引能优化的——是数据序列化层级的根本问题。

**证据**：
- `routes/monitor.py` 中 `_query_recent_runtime_activity()` 对每条 message 调用 `_parse_metadata(message.metadata_json)` 再取 `card` dict
- `services/monitor_projection.py` 中 `build_runtime_detail_sections()` 对 card 内的 `prompt_messages`、`raw_response`、`tool_calls` 等字段再次做 JSON parse
- Overview 页面 `_build_overview_usage_window()` 加载全部 runtime_card 到 Python，逐条做 JSON parse 后聚合 token 统计

### 根因 2：查询模式 —— Python 侧过滤与聚合

**现状**：多个查询路径采用 "SQL 取全量 → Python 过滤" 模式：

1. **列表过滤**：`network_buffer._list_persisted()` 先取 N 行 ORM 对象，逐个转 dict，再在 Python 中做文本搜索和分类过滤
2. **概览聚合**：`_build_overview_usage_window()` 加载几千条 runtime_card 到 Python，逐条解析 JSON 后做 `sum()` / `count()` 聚合
3. **Agent 映射**：`_query_recent_message_activity()` 每次请求都 `db.query(Agent).all()` 全表扫描构建 name 映射

**证据**：
- `_build_overview_llm_summary()` 中 `total_input = sum(int(row.token_input or 0) for row in llm_rows)` — 这是本该用 SQL `SUM()` 完成的操作
- `_filter_network_entries()` 对每条记录调用 4 个判断函数（`_is_frontend_backend_traffic`, `_is_monitor_page_network`, `_is_frontend_backend_heartbeat`, `_is_frontend_meta_request`），总计 10+ 次字符串比较

### 根因 3：运行时架构 —— 单进程争用 + 中间件放大

**现状**：

- 单个 uvicorn worker 承载所有请求（包括 Monitor 读取 + chat/runtime 写入 + telemetry 写入）
- Monitor 路由是 async 外壳内跑同步 SQLAlchemy/SQLite 调用，阻塞 event loop
- `RequestLoggingMiddleware` 把每次详情请求的**完整响应体**（包含 `raw_response`、`prompt_messages` 等大字段）抄写到 `monitor_network_buffer`，触发又一次 SQLite INSERT

**证据**：
- 同一路由、同一行数据，延迟从 142ms 到 5913ms 抖动——说明不是查询本身慢，而是进程内争用
- `main.py` 中 `_record_frontend_backend_event()` 接收 `response_body=getattr(response, "body", b"")` 并写入 telemetry
- 响应大小与延迟不相关，排除 I/O 瓶颈，指向 CPU 密集的 JSON parse 阻塞了 event loop

### SQLite 的角色

SQLite 不是根因，但放大了上述问题：

- WAL 模式下主键读取本身很快（<1ms），但 writer lock 会阻塞同步读取
- 单进程模型下，chat/runtime 的写入与 Monitor 的读取在同一个 SQLite connection 上竞争
- 换 PostgreSQL 可消除锁争用导致的抖动，但不解决 JSON parse 的 CPU 开销

---

## 决策

### 改进方案（按优先级）

#### P0-1: Runtime Card 投影表（解决根因 1）

**目标**：将 runtime 数据从消息 JSON 中解耦，建立专用的结构化投影表。

**方案**：

```sql
CREATE TABLE runtime_card_projections (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL UNIQUE,
    chatroom_id INTEGER NOT NULL,
    task_run_id INTEGER,

    -- 从 card JSON 预提取的高频查询字段
    card_type TEXT NOT NULL,           -- llm_call / tool_call / agent_message / ...
    agent_name TEXT,
    tool_name TEXT,
    model TEXT,
    turn INTEGER,

    -- 预聚合的数值字段（列表/概览直接用，不需要 JSON parse）
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    success BOOLEAN,

    -- 预截断的预览字段（列表接口用）
    title TEXT,
    preview TEXT,
    prompt_preview TEXT,
    response_preview TEXT,

    -- 原始 card JSON（仅详情接口按需读取）
    card_json TEXT,

    created_at DATETIME NOT NULL,

    INDEX ix_rcp_created (created_at),
    INDEX ix_rcp_type_agent (card_type, agent_name),
    INDEX ix_rcp_task_run (task_run_id)
);
```

**写入时机**：在 message 保存的同一事务中，提取 card 字段写入投影表。

**收益**：
- 列表查询：零 JSON parse，直接 `SELECT card_type, agent_name, preview FROM runtime_card_projections ORDER BY created_at DESC LIMIT 80`
- 概览聚合：`SELECT agent_name, SUM(tokens_in), COUNT(*) FROM runtime_card_projections WHERE card_type='llm_call' GROUP BY agent_name` — 一条 SQL 替代几千次 JSON parse
- 详情查询：按 `message_id` 读一行 `card_json`，只做一次 `json.loads`

**影响范围**：
- 新增表 + Alembic 迁移
- 修改 message 写入路径，增加投影写入
- 修改 Monitor 路由，从投影表读取
- 需要数据回填脚本（从现有 messages 表提取）

#### P0-1A: Projection durability contract（补足 runtime_card -> projection 断层）

**2026-05-30 live 现场验证**（WSL DB `/home/sun/.catown/state/catown.db`）：
- `messages WHERE message_type='runtime_card' = 738`
- `runtime_card_projections = 0`
- 文件类 `tool_call` runtime card = 22（`list_files=19`, `read_file=3`）
- `/api/monitor/files` 只查询 `runtime_card_projections`，因此会出现“前台已有 runtime card，Files 仍为空”的假阴性

**问题本质**：
- `runtime_card_projections` 不能被当成“可选缓存”或“后续再补”的索引表。
- 它已经是 Monitor 多个页面的主读模型；如果实时写入未接入，Monitor 得到的是错误的空结果，而不是单纯变慢。
- 只要仍允许多个调用点直接 `chatroom_manager.send_message(..., message_type="runtime_card", ...)`，就会持续出现“有 message、无 projection”的读模型漂移。

**补充决策**：
- 所有 `runtime_card` 写入路径必须统一收敛到一个 shared persistence helper，单入口完成：
  1. 持久化 `messages` 行
  2. 同事务调用 `upsert_runtime_card_projection(...)`
  3. `flush/commit` 后再发布 room / monitor websocket 事件
- 禁止新的运行时路径直接调用 `chatroom_manager.send_message(..., message_type="runtime_card", ...)` 绕过投影写入；现有分支（如 `store_runtime_card`、consult/runtime 辅助路径）也必须收敛到同一 helper。
- `runtime_card_projections.message_id` 的唯一键保持幂等；同一 message 可以安全地被 backfill/reconcile 重放而不产生重复投影。

**为什么不让 `/monitor/files` 永久回退扫 `messages`**：
- 这会把性能问题重新带回热路径，隐藏 projection 漂移，并让“空页面”退化成静默降级。
- 正确做法是修复读模型 contract，并在缺口出现时显式暴露健康状态。

#### P0-1B: Startup / background reconcile（补齐历史与漏写）

**目标**：projection 缺口应自动收敛，而不是完全依赖人工点击 backfill。

**方案**：
- 保留 `POST /api/monitor/backfill-projections` 作为 operator repair endpoint。
- 启动时执行幂等 reconcile：扫描缺失 `message_id` 的 runtime card 并补写 projection，直到 backlog = 0。
- 在高频写入期增加低频后台 sweep，用于修复极端异常、重启中断或历史遗留 gap。
- 在 overview/health 响应中暴露 `runtime_card_count`、`projection_count`、`missing_projection_count`、`last_backfill_at`，让前端与运维能区分“无数据”和“投影落后”。

**收益**：
- 既能修复已有数据库，又能把未来漏写从“用户先看到空白页面”前移成“系统自愈 + 明确告警”。
- backfill 不再只是一次性迁移脚本，而是长期的 read-model repair 机制。

#### P0-1C: `/monitor/files` 统计口径显式化

**现状**：
- Files 页面只统计 `read_file`、`write_file`、`list_files`、`delete_file`、`search_files` 这 5 个显式 file tools。
- 现场大部分“代理在读写文件”的 activity 实际来自 `run_shell`；它们不会进入该页面。
- 因此前台可见文件相关操作，Files 页却接近空白，这不一定是写库失败，也可能是产品口径差异。

**决策**：
- `Files` 默认继续保持“显式 file tools 审计”语义，不把 `run_shell` 的任意命令文本或命令结果混入同一数据集。
- 页面与接口需要明确标注：shell 方式的文件访问不计入 `Files`。
- 如果后续需要覆盖 shell 文件活动，应以单独的数据源/标签实现（例如 `shell_file_access` 或 `best_effort`），而不是污染 file-tool 审计口径。
- 当 `missing_projection_count > 0` 且存在 file-tool runtime card 时，Files 页面应显示 `projection lag / backfill needed` 诊断态，而不是空列表的成功态。

#### P0-2: 中间件瘦身（解决根因 3 的放大器）

**目标**：详情接口的响应体不再完整写入 monitor telemetry。

**方案**：
- 对 `/api/monitor/runtime-cards/*` 路径，`_record_frontend_backend_event()` 只记录摘要（状态码、耗时、响应大小），不记录 `raw_response` / `request_body`
- 或对所有 `/api/monitor/*` 路径启用 `preview_only` 模式

**收益**：消除每次详情请求的额外大文本 SQLite 写入。

#### P0-3: Overview 聚合下推到 SQL（解决根因 2 的主要表现）

**目标**：用 SQL 聚合替代 Python 全量加载 + 遍历。

**方案**：
- `_build_overview_llm_summary()`：用 `func.sum()`, `func.count()` 替代 Python `sum()`
- `_build_overview_usage_window()`：用 `GROUP BY agent_name` 替代 Python dict 聚合
- Agent name 映射：用 `lru_cache` + TTL 缓存替代每次全表扫描

**收益**：概览页查询从 "加载几千行到 Python" 变为 "SQL 返回几行聚合结果"。

#### P1-1: Network 列表过滤下推到 SQL

**目标**：`_list_persisted()` 的过滤逻辑从 Python 搬到 SQL WHERE 子句。

**方案**：
- 将 `_include_network_entry()` 的判断逻辑转为 SQLAlchemy filter 条件
- `query` 文本搜索用 SQL `LIKE` 替代 Python `in` 匹配
- 列表接口使用轻量序列化（不返回 `raw_request`, `raw_response`, `request_headers_json`）

#### P1-2: 详情接口懒加载

**目标**：列表接口不返回大字段，详情接口按需加载。

**方案**：
- 列表接口只返回投影表中的轻量字段
- 详情接口单独读取 `card_json`，解析后构建 `detail_sections`
- 前端：列表渲染不需要 `prompt_messages` / `raw_response`，点击展开时再请求详情

#### P2-1: 多 Worker / 进程分离

**目标**：消除单进程争用。

**方案**：
- uvicorn 启用多 worker（`--workers 2`）
- 或将 Monitor 路由分离到独立进程/端口

---

## 后果

### 采纳后

- Monitor 列表查询延迟：从 200-500ms 降至 20-50ms（投影表直接查询）
- Overview 聚合延迟：从 500ms-2s 降至 10-30ms（SQL 聚合）
- 详情接口抖动：消除 JSON parse 导致的 CPU 阻塞，配合中间件瘦身消除写入放大
- 写入路径增加一次投影写入开销（INSERT 时多一次 JSON extract，~0.5ms）
- `runtime_card_projections` 从“性能优化缓存”升级为“Monitor 正确性依赖的 durable read model”
- `/monitor/files` 不再把 projection 漏写与“确实没有文件操作”混成同一种空状态

### 不采纳

- 现状持续：Monitor 页面随数据增长线性变慢
- 单进程争用问题在并发场景下继续恶化
- 无法通过换数据库（PostgreSQL）根本解决数据形态问题

---

## 参考

- Codex 分析：runtime-card 详情接口 telemetry 数据（142ms ~ 5913ms 抖动）
- 架构分析：`models/database.py` 73KB 单文件、双 SQLite 数据库设计
- 性能分析：`network_buffer._list_persisted()` Python 过滤、`_build_overview_*()` 全表扫描
- 2026-05-30 live WSL DB 验证：`messages.runtime_card = 738`、`runtime_card_projections = 0`、文件类 runtime card = 22（`list_files=19`, `read_file=3`）
