# Feature Doc: Chrome DevTools Performance Tool

**Status**: Implemented  
**Date**: 2026-05-30  
**Owners**: Catown Tools  
**Related**: Browser Tool, Screenshot Tool, Screenshot Compare Tool

---

## 1. Background

Catown 已有 `browser` 工具（Playwright 驱动）用于页面交互，`screenshot` 工具用于截图。但缺少一个专门用于 **性能数据采集** 的工具：

- 无法获取 Navigation Timing / Resource Timing / Paint Timing 等 Web Performance API 数据
- 无法采集 LCP / CLS / Long Tasks 等 Core Web Vitals
- 无法录制 Performance Trace（可导入 chrome://tracing 分析）
- 无法在一次调用中完成"导航 → 模拟操作 → 采集性能指标"的完整流程

`chrome_devtools` 工具填补这一空白，通过直接连接 Chrome DevTools Protocol (CDP) 实现高性能数据采集。

---

## 2. Design Goals

| 目标 | 说明 |
|------|------|
| 完整性能数据 | 覆盖 Navigation Timing、Resource Timing、Paint、LCP、CLS、Long Tasks、Memory |
| CDP 原生 | 直接通过 WebSocket 连接 Chrome DevTools Protocol，不依赖 Puppeteer |
| 操作模拟 | 支持 click / fill / type / scroll / wait / evaluate 等页面交互 |
| Trace 录制 | 可录制 Performance Trace JSON，导入 chrome://tracing 分析 |
| 一键审计 | `full_audit` 模式一次调用完成导航 + 操作 + 采集 |
| 零新依赖 | 仅使用项目已有的 `websockets` 库 |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│  Agent (developer / tester)                     │
│    ↓ chrome_devtools tool call                  │
├─────────────────────────────────────────────────┤
│  ChromeDevtoolsTool                             │
│    ├─ Chrome 进程管理（launch / connect）         │
│    ├─ CDP WebSocket 客户端 (_CDPSession)        │
│    ├─ 操作执行器 (_execute_action)              │
│    ├─ 性能数据采集 (_collect_performance_metrics)│
│    └─ Trace 录制 (_start_trace / _stop_trace)   │
├─────────────────────────────────────────────────┤
│  Chrome (--remote-debugging-port=9222)          │
│    └─ CDP WebSocket Endpoint                    │
└─────────────────────────────────────────────────┘
```

### 3.1 CDP 客户端

`_CDPSession` 是一个轻量级异步 CDP 客户端：

- 通过 `websockets` 库连接 Chrome 的 WebSocket 端点
- 支持命令发送 / 响应等待 / 事件收集
- 自动发现 Chrome 页面目标（`/json` 端点）
- 支持连接已运行的 Chrome 实例或启动新实例

### 3.2 Chrome 生命周期

- **连接已有实例**：指定 `host` + `port`，自动通过 `/json` 端点获取 WebSocket URL
- **启动新实例**：设置 `launch=true`，自动查找系统 Chrome/Chromium 并以 headless 模式启动
- Chrome 路径探测顺序：`CHROMIUM_PATH` 环境变量 → 常见系统路径

---

## 4. Supported Actions

### 4.1 页面操作

| Action | 参数 | 说明 |
|--------|------|------|
| `navigate` | `url`, `wait_until` | 导航到 URL，支持 load/domcontentloaded/networkidle/commit |
| `click` | `selector` | 点击 CSS 选择器元素，自动滚动到可视区域 |
| `fill` | `selector`, `value` | 填充表单字段，触发 input/change/blur 事件 |
| `type` | `text`, `delay_ms` | 逐字输入文本，模拟真实打字 |
| `scroll` | `dx`, `dy` | 滚动页面（默认向下 500px） |
| `wait` | `ms`, `selector` | 等待指定时间或等待元素出现 |
| `evaluate` | `expression` | 执行 JavaScript 并返回结果 |
| `screenshot` | `path` | 截图并保存为 PNG |

### 4.2 性能采集

| Action | 说明 |
|--------|------|
| `collect_metrics` | 采集所有性能指标，可选录制 trace |
| `run_trace` | 仅录制 Performance Trace |
| `full_audit` | 完整审计：导航 → 模拟操作 → 采集指标 → 可选 trace |

---

## 5. Performance Metrics

### 5.1 Navigation Timing

```json
{
  "ttfb": 120.5,
  "domContentLoaded": 450.2,
  "fullLoad": 1200.8,
  "protocol": "h2",
  "transferSize": 245000,
  "renderBlockingStatus": "blocking"
}
```

采集字段：`startTime`, `duration`, `redirectStart/End`, `domainLookupStart/End`, `connectStart/End`, `secureConnectionStart`, `requestStart`, `responseStart/End`, `domInteractive`, `domContentLoadedEventStart/End`, `domComplete`, `loadEventStart/End`, `transferSize`, `encodedBodySize`, `decodedBodySize`, `protocol`, `renderBlockingStatus`

### 5.2 Paint Timing

```json
{
  "first-paint": 320.1,
  "first-contentful-paint": 345.7
}
```

### 5.3 Largest Contentful Paint (LCP)

```json
{
  "startTime": 890.3,
  "renderTime": 890.3,
  "loadTime": 850.0,
  "size": 12500,
  "element": "IMG",
  "url": "/hero.jpg"
}
```

### 5.4 Cumulative Layout Shift (CLS)

```json
{
  "value": 0.0523,
  "entryCount": 3,
  "entries": [
    {"value": 0.02, "startTime": 1500.0},
    {"value": 0.0323, "startTime": 3200.0}
  ]
}
```

### 5.5 Long Tasks (>50ms)

```json
[
  {
    "name": "self",
    "startTime": 2100.0,
    "duration": 85.3,
    "attribution": [{"name": "script", "containerType": "script"}]
  }
]
```

### 5.6 Resource Timing

```json
{
  "count": 42,
  "total_transfer_size": 1250000,
  "total_duration": 3500.0,
  "by_type": {
    "script": {"count": 8, "total_size": 450000, "total_duration": 1200},
    "css": {"count": 3, "total_size": 120000, "total_duration": 300},
    "img": {"count": 15, "total_size": 600000, "total_duration": 2000},
    "fetch": {"count": 10, "total_size": 80000, "total_duration": 500}
  },
  "entries": [...]
}
```

### 5.7 CDP Performance Metrics

Chrome 内部指标，包括：

- `Timestamp`, `Documents`, `Frames`, `JSEventListeners`
- `LayoutCount`, `RecalcStyleCount`, `LayoutDuration`, `RecalcStyleDuration`
- `ScriptDuration`, `TaskDuration`, `JSHeapUsedSize`, `JSHeapTotalSize`

### 5.8 Memory

```json
{
  "usedJSHeapSize": 12500000,
  "totalJSHeapSize": 25000000,
  "jsHeapSizeLimit": 2147483648
}
```

---

## 6. Full Audit Flow

`full_audit` 是最常用的操作模式，一次调用完成完整性能审计：

```
1. navigate(url, wait_until="load")
       ↓
2. [可选] start_trace(categories)
       ↓
3. 依次执行 actions[]
   - click / fill / type / scroll / wait / evaluate
   - 每步记录成功/失败
       ↓
4. sleep(collect_delay_ms)  // 等待异步资源加载完成
       ↓
5. collect_performance_metrics()
   - Navigation Timing
   - Paint Timing
   - LCP / CLS / Long Tasks
   - Resource Timing
   - CDP Metrics
   - Memory
       ↓
6. [如有 trace] stop_trace() → 保存 JSON
       ↓
7. 计算 summary（人类可读摘要）
       ↓
8. 保存 metrics JSON 文件
       ↓
9. 返回结构化结果
```

### 6.1 输出结构

```json
{
  "success": true,
  "url": "https://example.com",
  "duration_ms": 5230.4,
  "action_count": 3,
  "action_results": [...],
  "summary": {
    "ttfb_ms": 120.5,
    "dom_content_loaded_ms": 450.2,
    "full_load_ms": 1200.8,
    "first_paint_ms": 320.1,
    "first_contentful_paint_ms": 345.7,
    "lcp_ms": 890.3,
    "cls_score": 0.0523,
    "resource_count": 42,
    "total_resource_size_bytes": 1250000,
    "long_task_count": 2,
    "longest_task_ms": 85.3,
    "js_heap_used_mb": 11.9,
    "js_heap_total_mb": 23.8,
    "actions_succeeded": 3,
    "actions_failed": 0
  },
  "metrics": { ... },
  "metrics_output": "/tmp/catown_metrics_abc123.json",
  "trace": {
    "path": "/tmp/catown_trace_def456.json",
    "size_bytes": 256000,
    "event_count": 15420
  }
}
```

---

## 7. Usage Examples

### 7.1 简单性能采集

```json
{
  "action": "collect_metrics",
  "launch": true
}
```

需先 `navigate` 到目标页面，然后调用此操作采集当前页面的性能数据。

### 7.2 完整审计（无操作）

```json
{
  "action": "full_audit",
  "url": "https://example.com",
  "launch": true,
  "trace": true
}
```

### 7.3 完整审计（含用户操作模拟）

```json
{
  "action": "full_audit",
  "url": "https://example.com/login",
  "launch": true,
  "trace": true,
  "actions": [
    {"kind": "fill", "selector": "#username", "value": "testuser"},
    {"kind": "fill", "selector": "#password", "value": "pass123"},
    {"kind": "click", "selector": ".login-btn"},
    {"kind": "wait", "ms": 3000},
    {"kind": "scroll", "dy": 800},
    {"kind": "wait", "selector": ".dashboard-content", "ms": 5000}
  ],
  "collect_delay_ms": 2000
}
```

### 7.4 连接已有 Chrome 实例

```json
{
  "action": "full_audit",
  "url": "https://staging.example.com",
  "host": "127.0.0.1",
  "port": 9222,
  "trace": true
}
```

### 7.5 仅录制 Trace

```json
{
  "action": "run_trace",
  "launch": true,
  "trace_output": "/workspace/perf-trace.json"
}
```

生成的 JSON 可直接导入 `chrome://tracing` 或 [Perfetto](https://ui.perfetto.dev/) 分析。

---

## 8. Tool Policy

| 属性 | 值 |
|------|-----|
| risk_level | high |
| approval | conditional |
| network_access | enabled |
| side_effect_scope | browser_interaction |
| external_targets | web, chrome_devtools |

- 读取型操作（collect_metrics / run_trace）自动放行
- 交互型操作（click / fill / type）可能修改远程状态，按 conditional 策略处理
- 启动 Chrome 进程属于本地 side effect

---

## 9. Agent Integration

工具已加入以下 Agent 的白名单：

| Agent | 用途 |
|-------|------|
| `developer` | 开发阶段性能测试、页面交互调试 |
| `tester` | 自动化性能回归测试、Core Web Vitals 监控 |

---

## 10. Dependencies

| 依赖 | 版本 | 说明 |
|------|------|------|
| `websockets` | (已有) | CDP WebSocket 通信 |
| Chrome/Chromium | 系统安装 | 通过 `CHROMIUM_PATH` 或自动探测 |

无需新增 `requirements.txt` 依赖。

---

## 11. Non-Goals

- 不替代 `browser` 工具的通用页面交互能力
- 不提供可视化 Trace 查看器（使用 chrome://tracing 或 Perfetto）
- 不做自动化性能基线比较（可由 tester agent 编排实现）
- 不支持移动端设备模拟（v1）

---

## 12. Future Iterations

1. **性能基线对比** — 与历史数据对比，自动检测性能退化
2. **Web Vitals 评分** — 按 Google 标准自动评分（Good / Needs Improvement / Poor）
3. **Throttling 模拟** — CPU / 网络限速，测试弱网性能
4. **多页面审计** — 批量审计多个 URL，生成汇总报告
5. **CI/CD 集成** — 作为 Pipeline 步骤自动执行性能门禁
6. **Lighthouse 集成** — 调用 Lighthouse 生成完整审计报告
