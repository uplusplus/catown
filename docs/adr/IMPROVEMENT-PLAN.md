# Monitor 性能改进计划

> 基于 ADR-001 根因分析，按优先级排列的可执行改进任务。

---

## P0: Runtime Card 投影表（核心改进）

### 任务 1.1: 定义投影表模型

- **文件**: `backend/models/runtime_projection.py`
- **内容**: 新增 `RuntimeCardProjection` SQLAlchemy 模型
- **字段**: `message_id`, `chatroom_id`, `task_run_id`, `card_type`, `agent_name`, `tool_name`, `model`, `turn`, `tokens_in`, `tokens_out`, `duration_ms`, `success`, `title`, `preview`, `prompt_preview`, `response_preview`, `card_json`, `created_at`
- **索引**: `(created_at)`, `(card_type, agent_name)`, `(task_run_id)`, `(message_id)` UNIQUE

### 任务 1.2: Alembic 迁移

- **文件**: `backend/alembic/versions/` 新增迁移脚本
- **内容**: 创建 `runtime_card_projections` 表
- **验证**: 迁移可回滚，不影响现有表

### 任务 1.3: 投影写入逻辑

- **文件**: `backend/services/runtime_card_projection.py`
- **内容**:
  - `extract_card_fields(message: Message) -> dict` — 从 `metadata_json` 提取 card 字段
  - `write_projection(db, message: Message)` — INSERT 投影记录
  - 处理 card 为 JSON string 或 dict 两种情况
- **调用点**: message 保存路径中，`message_type == "runtime_card"` 时同步写入投影

### 任务 1.4: 数据回填脚本

- **文件**: `backend/scripts/backfill_runtime_projections.py`
- **内容**:
  - 扫描 `messages` 表中 `message_type = 'runtime_card'` 的记录
  - 提取 `metadata_json` → card 字段 → INSERT 投影表
  - 支持增量回填（按 id 范围分批）
  - 打印进度和统计

### 任务 1.5: Monitor 路由切换到投影表

- **文件**: `backend/routes/monitor.py`
- **修改**:
  - `_query_recent_runtime_activity()` — 从投影表查询，不再解析 `metadata_json`
  - `_build_overview_usage_window()` — 用 SQL 聚合替代 Python 遍历
  - `_build_overview_llm_summary()` — 用 `func.sum()` / `func.count()` 替代 Python `sum()`
  - 详情接口 — 按 `message_id` 读投影表的 `card_json`，只做一次 `json.loads`

---

## P0: 中间件瘦身

### 任务 2.1: Monitor 路由响应不写入 telemetry

- **文件**: `backend/main.py`
- **修改**: `_record_frontend_backend_event()` 中，对 `/api/monitor/*` 路径跳过 `raw_request` / `raw_response` 抄写，只记录摘要（状态码、耗时、响应大小）
- **验证**: Monitor 页面的网络活动列表不再出现自身的 API 调用

---

## P0: Overview 聚合下推

### 任务 3.1: LLM Summary SQL 聚合

- **文件**: `backend/routes/monitor.py`
- **修改**: `_build_overview_llm_summary()` 改为：
  ```python
  stats = telemetry_db.query(
      func.count(LLMCall.id),
      func.coalesce(func.sum(LLMCall.token_input), 0),
      func.coalesce(func.sum(LLMCall.token_output), 0),
  ).filter(LLMCall.created_at >= scan_start).first()
  ```

### 任务 3.2: Agent name 缓存

- **文件**: `backend/routes/monitor.py`
- **修改**: `_query_recent_message_activity()` 中的 `db.query(Agent).all()` 改为 TTL 缓存
- **实现**: `@lru_cache` + 按分钟 bucket 刷新

---

## P1: Network 列表优化

### 任务 4.1: 过滤下推到 SQL

- **文件**: `backend/monitoring/network_buffer.py`
- **修改**: `_list_persisted()` 将 `_include_network_entry()` 逻辑转为 SQLAlchemy WHERE 条件
- **新增索引**: `(category, aggregated, id)`, `(client_source, id)`

### 任务 4.2: 轻量序列化

- **文件**: `backend/monitoring/network_buffer.py`
- **修改**: 列表接口不返回 `raw_request`, `raw_response`, `request_headers_json`, `response_headers_json`

---

## P1: 详情接口懒加载

### 任务 5.1: 列表/详情分离

- **文件**: `backend/routes/monitor.py`
- **修改**: 列表接口返回轻量字段；详情接口按 `message_id` 读 `card_json` 构建 `detail_sections`

---

## 依赖关系

```
1.1 → 1.2 → 1.3 → 1.4 → 1.5
                              ↘
2.1 (独立)                      3.1 (依赖投影表)
3.2 (独立)                      4.1 (独立)
4.2 (独立)                      5.1 (依赖投影表)
```

## 预估工作量

| 任务 | 复杂度 | 预估时间 |
|------|--------|---------|
| 1.1 投影表模型 | 低 | 0.5h |
| 1.2 Alembic 迁移 | 低 | 0.5h |
| 1.3 投影写入逻辑 | 中 | 2h |
| 1.4 回填脚本 | 中 | 2h |
| 1.5 路由切换 | 高 | 4h |
| 2.1 中间件瘦身 | 低 | 1h |
| 3.1 SQL 聚合 | 中 | 2h |
| 3.2 Agent 缓存 | 低 | 0.5h |
| 4.1 过滤下推 | 中 | 2h |
| 4.2 轻量序列化 | 低 | 1h |
| 5.1 列表/详情分离 | 中 | 2h |
| **合计** | | **~17.5h** |

## 验收标准

- [ ] Monitor 列表页加载 < 100ms（当前 200-500ms）
- [ ] Overview 页加载 < 100ms（当前 500ms-2s）
- [ ] Runtime-card 详情接口延迟稳定 < 300ms，无 >1s 抖动
- [ ] 详情请求不再触发额外的大文本 telemetry 写入
- [ ] 现有功能无回归（聊天、pipeline、approval 等不受影响）
