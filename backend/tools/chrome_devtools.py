# -*- coding: utf-8 -*-
"""
Chrome DevTools Performance Tool — 通过 CDP 连接 Chrome，模拟页面操作并采集性能数据

功能：
  1. 启动或连接 Chrome（--remote-debugging-port）
  2. 导航到目标 URL
  3. 模拟页面操作（click / fill / type / scroll / wait / evaluate）
  4. 采集 Performance API 数据（Navigation Timing / Resource Timing / Paint / LCP / CLS / FID）
  5. 采集 CDP Performance.metrics
  6. 可选录制 Performance Trace（可导入 chrome://tracing）
  7. 返回结构化 JSON
"""

from .base import BaseTool
import asyncio
import json
import os
import subprocess
import time
import tempfile
import urllib.parse
from typing import Any, Dict, List, Optional

from monitoring import monitor_network_buffer

# ─── 常量 ────────────────────────────────────────────────────────

_CHROME_PATHS = [
    os.environ.get("CHROMIUM_PATH", ""),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]

_CDP_VERSION_TIMEOUT = 3  # seconds
_CDP_WS_TIMEOUT = 30  # seconds for websocket operations
_DEFAULT_PORT = 9222
_PERF_COLLECT_DELAY_MS = 1500  # wait after last action before collecting metrics


# ─── Chrome 进程管理 ─────────────────────────────────────────────

def _find_chrome() -> str:
    """Find available Chromium/Chrome binary"""
    for p in _CHROME_PATHS:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise RuntimeError(
        "No Chromium/Chrome binary found. "
        "Install chromium or set CHROMIUM_PATH env var."
    )


def _is_port_open(host: str, port: int) -> bool:
    """Check if a TCP port is accepting connections"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except (ConnectionRefusedError, OSError):
        return False


def _get_ws_endpoint(host: str, port: int) -> str:
    """Fetch the DevTools WebSocket endpoint from /json/version"""
    import urllib.request
    url = f"http://{host}:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=_CDP_VERSION_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data.get("webSocketDebuggerUrl", "")
    except Exception as e:
        raise RuntimeError(f"Cannot reach Chrome DevTools at {host}:{port} — {e}")


def _get_page_ws_endpoint(host: str, port: int, target_id: str = "") -> str:
    """Fetch the WS endpoint for a specific page (or the first page)"""
    import urllib.request
    url = f"http://{host}:{port}/json"
    try:
        with urllib.request.urlopen(url, timeout=_CDP_VERSION_TIMEOUT) as resp:
            pages = json.loads(resp.read())
        for page in pages:
            if page.get("type") != "page":
                continue
            if target_id and page.get("id") != target_id:
                continue
            ws = page.get("webSocketDebuggerUrl", "")
            if ws:
                return ws
        # 如果没指定 target_id，取第一个 page
        for page in pages:
            if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                return page["webSocketDebuggerUrl"]
        raise RuntimeError("No page target found in Chrome DevTools")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Cannot list Chrome pages at {host}:{port} — {e}")


def _launch_chrome(port: int, user_data_dir: str = "") -> subprocess.Popen:
    """Launch a new Chrome instance with remote debugging"""
    chrome = _find_chrome()
    if not user_data_dir:
        user_data_dir = tempfile.mkdtemp(prefix="catown_chrome_")
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--no-first-run",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 等待 DevTools 端口就绪
    for _ in range(30):
        if _is_port_open("127.0.0.1", port):
            time.sleep(0.3)  # 额外等一下让 /json 端点就绪
            return proc
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError(f"Chrome did not start within 6s on port {port}")


# ─── CDP WebSocket 客户端 ────────────────────────────────────────

class _CDPSession:
    """Minimal async CDP client over WebSocket"""

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws = None
        self._msg_id = 0
        self._callbacks: Dict[int, asyncio.Future] = {}
        self._events: List[Dict[str, Any]] = []
        self._listener_task: Optional[asyncio.Task] = None

    async def connect(self):
        import websockets
        self._ws = await websockets.connect(
            self._ws_url,
            max_size=50 * 1024 * 1024,  # 50MB for large traces
            close_timeout=2,
        )
        self._listener_task = asyncio.create_task(self._listen())

    async def _listen(self):
        """Background listener for CDP messages"""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._callbacks.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    self._events.append(msg)
        except Exception:
            pass

    async def send(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a CDP command and wait for the response"""
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        fut = asyncio.get_event_loop().create_future()
        self._callbacks[self._msg_id] = fut
        await self._ws.send(json.dumps(msg))
        result = await asyncio.wait_for(fut, timeout=_CDP_WS_TIMEOUT)
        if "error" in result:
            raise RuntimeError(f"CDP error [{method}]: {result['error']}")
        return result.get("result", {})

    def pop_events(self, method_filter: str = "") -> List[Dict[str, Any]]:
        """Pop collected events, optionally filtered by method name"""
        if method_filter:
            matched = [e for e in self._events if e.get("method") == method_filter]
            self._events = [e for e in self._events if e.get("method") != method_filter]
            return matched
        events = list(self._events)
        self._events.clear()
        return events

    async def close(self):
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()


# ─── 性能数据采集 ─────────────────────────────────────────────────

async def _collect_performance_metrics(cdp: _CDPSession) -> Dict[str, Any]:
    """Collect comprehensive performance data via CDP + Performance API"""
    metrics: Dict[str, Any] = {}

    # 1) CDP Performance.metrics
    try:
        cdp_metrics = await cdp.send("Performance.enable")
        cdp_metrics = await cdp.send("Performance.getMetrics")
        metrics["cdp_metrics"] = {
            m["name"]: m["value"]
            for m in cdp_metrics.get("metrics", [])
        }
    except Exception as e:
        metrics["cdp_metrics_error"] = str(e)

    # 2) Navigation Timing
    try:
        nav_timing = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const nav = performance.getEntriesByType('navigation')[0];
                    if (!nav) return null;
                    return {
                        name: nav.name,
                        type: nav.type,
                        startTime: nav.startTime,
                        duration: nav.duration,
                        redirectStart: nav.redirectStart,
                        redirectEnd: nav.redirectEnd,
                        domainLookupStart: nav.domainLookupStart,
                        domainLookupEnd: nav.domainLookupEnd,
                        connectStart: nav.connectStart,
                        connectEnd: nav.connectEnd,
                        secureConnectionStart: nav.secureConnectionStart,
                        requestStart: nav.requestStart,
                        responseStart: nav.responseStart,
                        responseEnd: nav.responseEnd,
                        domInteractive: nav.domInteractive,
                        domContentLoadedEventStart: nav.domContentLoadedEventStart,
                        domContentLoadedEventEnd: nav.domContentLoadedEventEnd,
                        domComplete: nav.domComplete,
                        loadEventStart: nav.loadEventStart,
                        loadEventEnd: nav.loadEventEnd,
                        transferSize: nav.transferSize,
                        encodedBodySize: nav.encodedBodySize,
                        decodedBodySize: nav.decodedBodySize,
                        protocol: nav.nextHopProtocol,
                        renderBlockingStatus: nav.renderBlockingStatus,
                        ttfb: nav.responseStart - nav.requestStart,
                        domContentLoaded: nav.domContentLoadedEventEnd - nav.startTime,
                        fullLoad: nav.loadEventEnd - nav.startTime,
                    };
                })()
            """,
            "returnByValue": True,
        })
        metrics["navigation_timing"] = nav_timing.get("result", {}).get("value")
    except Exception as e:
        metrics["navigation_timing_error"] = str(e)

    # 3) Paint Timing (FP / FCP)
    try:
        paint = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const entries = performance.getEntriesByType('paint');
                    const result = {};
                    entries.forEach(e => { result[e.name] = e.startTime; });
                    return result;
                })()
            """,
            "returnByValue": True,
        })
        metrics["paint_timing"] = paint.get("result", {}).get("value")
    except Exception as e:
        metrics["paint_timing_error"] = str(e)

    # 4) Largest Contentful Paint (LCP)
    try:
        lcp = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const entries = performance.getEntriesByType('largest-contentful-paint');
                    if (!entries.length) return null;
                    const last = entries[entries.length - 1];
                    return {
                        startTime: last.startTime,
                        renderTime: last.renderTime,
                        loadTime: last.loadTime,
                        size: last.size,
                        element: last.element ? last.element.tagName : null,
                        id: last.id,
                        url: last.url || null,
                    };
                })()
            """,
            "returnByValue": True,
        })
        metrics["largest_contentful_paint"] = lcp.get("result", {}).get("value")
    except Exception as e:
        metrics["lcp_error"] = str(e)

    # 5) Cumulative Layout Shift (CLS)
    try:
        cls = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const entries = performance.getEntriesByType('layout-shift');
                    if (!entries.length) return { value: 0, entries: [] };
                    let clsValue = 0;
                    const details = [];
                    entries.forEach(e => {
                        if (!e.hadRecentInput) {
                            clsValue += e.value;
                            details.push({ value: e.value, startTime: e.startTime });
                        }
                    });
                    return { value: clsValue, entryCount: details.length, entries: details.slice(0, 20) };
                })()
            """,
            "returnByValue": True,
        })
        metrics["cumulative_layout_shift"] = cls.get("result", {}).get("value")
    except Exception as e:
        metrics["cls_error"] = str(e)

    # 6) Resource Timing (所有资源加载)
    try:
        resources = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const entries = performance.getEntriesByType('resource');
                    return entries.map(e => ({
                        name: e.name,
                        initiatorType: e.initiatorType,
                        startTime: e.startTime,
                        duration: e.duration,
                        transferSize: e.transferSize,
                        encodedBodySize: e.encodedBodySize,
                        decodedBodySize: e.decodedBodySize,
                        responseEnd: e.responseEnd,
                        protocol: e.nextHopProtocol,
                        renderBlockingStatus: e.renderBlockingStatus,
                    }));
                })()
            """,
            "returnByValue": True,
        })
        resource_list = resources.get("result", {}).get("value", [])
        metrics["resource_timing"] = {
            "count": len(resource_list),
            "total_transfer_size": sum(r.get("transferSize", 0) for r in resource_list),
            "total_duration": max((r.get("responseEnd", 0) for r in resource_list), default=0),
            "by_type": _group_resources_by_type(resource_list),
            "entries": resource_list[:200],  # cap at 200 to avoid huge output
        }
    except Exception as e:
        metrics["resource_timing_error"] = str(e)

    # 7) Long Tasks (超过 50ms 的任务)
    try:
        long_tasks = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const entries = performance.getEntriesByType('longtask');
                    return entries.map(e => ({
                        name: e.name,
                        startTime: e.startTime,
                        duration: e.duration,
                        attribution: (e.attribution || []).map(a => ({
                            name: a.name,
                            containerType: a.containerType,
                            containerName: a.containerName,
                        })),
                    }));
                })()
            """,
            "returnByValue": True,
        })
        metrics["long_tasks"] = long_tasks.get("result", {}).get("value", [])
    except Exception as e:
        metrics["long_tasks_error"] = str(e)

    # 8) Memory info (if available)
    try:
        memory = await cdp.send("Runtime.evaluate", {
            "expression": """
                (() => {
                    if (!performance.memory) return null;
                    return {
                        usedJSHeapSize: performance.memory.usedJSHeapSize,
                        totalJSHeapSize: performance.memory.totalJSHeapSize,
                        jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
                    };
                })()
            """,
            "returnByValue": True,
        })
        mem_val = memory.get("result", {}).get("value")
        if mem_val:
            metrics["memory"] = mem_val
    except Exception:
        pass

    return metrics


def _group_resources_by_type(resources: List[Dict]) -> Dict[str, Dict]:
    """Group resource timing entries by initiatorType"""
    groups: Dict[str, Dict] = {}
    for r in resources:
        t = r.get("initiatorType", "other")
        if t not in groups:
            groups[t] = {"count": 0, "total_size": 0, "total_duration": 0}
        groups[t]["count"] += 1
        groups[t]["total_size"] += r.get("transferSize", 0)
        groups[t]["total_duration"] += r.get("duration", 0)
    return groups


# ─── Trace 录制 ──────────────────────────────────────────────────

async def _start_trace(cdp: _CDPSession, categories: str = ""):
    """Start a CDP performance trace"""
    if not categories:
        categories = (
            "devtools.timeline,disabled-by-default-devtools.timeline,"
            "disabled-by-default-devtools.timeline.frame,toplevel,"
            "blink.console,disabled-by-default-devtools.timeline.stack,"
            "cpu_profiler.disabled-by-default-cpu_profiler"
        )
    await cdp.send("Tracing.start", {
        "categories": categories,
        "transferMode": "ReturnAsStream",
        "streamCompressionFormat": "none",
    })


async def _stop_trace_and_save(cdp: _CDPSession, output_path: str) -> Dict[str, Any]:
    """Stop tracing, collect the trace data, and save to file"""
    # 收集 trace 事件
    trace_events: List[Dict] = []

    # 设置 stream 完成的回调
    stream_complete = asyncio.Event()
    stream_chunks: List[str] = []

    async def _collect_trace_stream():
        """Wait for Tracing.tracingComplete then read the stream"""
        deadline = time.time() + 30
        while time.time() < deadline:
            events = cdp.pop_events("Tracing.dataCollected")
            for ev in events:
                trace_events.extend(ev.get("params", {}).get("value", []))

            complete_events = cdp.pop_events("Tracing.tracingComplete")
            if complete_events:
                # 尝试从 stream 读取
                stream_id = complete_events[0].get("params", {}).get("stream", "")
                if stream_id:
                    try:
                        while True:
                            chunk = await cdp.send("IO.read", {
                                "handle": stream_id,
                                "size": 1024 * 1024,
                            })
                            data = chunk.get("data", "")
                            if data:
                                stream_chunks.append(data)
                            if chunk.get("eof", True):
                                break
                    except Exception:
                        pass
                    # 也释放 stream
                    try:
                        await cdp.send("IO.close", {"handle": stream_id})
                    except Exception:
                        pass
                return True
            await asyncio.sleep(0.1)
        return False

    await _collect_trace_stream()

    # 合并 trace 数据
    if stream_chunks:
        try:
            trace_data = json.loads("".join(stream_chunks))
        except json.JSONDecodeError:
            trace_data = {"traceEvents": trace_events}
    else:
        trace_data = {"traceEvents": trace_events}

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(trace_data, f, ensure_ascii=False)

    return {
        "path": output_path,
        "size_bytes": os.path.getsize(output_path),
        "event_count": len(trace_data.get("traceEvents", [])),
    }


# ─── 操作执行器 ──────────────────────────────────────────────────

async def _execute_action(cdp: _CDPSession, action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single page action via CDP"""
    kind = action.get("kind", "")
    result: Dict[str, Any] = {"kind": kind, "success": False}

    try:
        if kind == "navigate":
            url = action.get("url", "")
            if not url:
                result["error"] = "url is required"
                return result
            wait_until = action.get("wait_until", "load")
            await cdp.send("Page.enable")
            await cdp.send("Page.navigate", {"url": url})
            # 等待加载事件
            await _wait_for_load_event(cdp, wait_until)
            result["success"] = True
            result["url"] = url

        elif kind == "click":
            selector = action.get("selector", "")
            if not selector:
                result["error"] = "selector is required"
                return result
            # 先滚动到元素可见
            await cdp.send("Runtime.evaluate", {
                "expression": f"""
                    (() => {{
                        const el = document.querySelector('{_escape_js(selector)}');
                        if (el) {{ el.scrollIntoView({{ behavior: 'instant', block: 'center' }}); return true; }}
                        return false;
                    }})()
                """,
                "returnByValue": True,
            })
            await asyncio.sleep(0.1)
            # 获取元素中心坐标并点击
            coords = await cdp.send("Runtime.evaluate", {
                "expression": f"""
                    (() => {{
                        const el = document.querySelector('{_escape_js(selector)}');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
                    }})()
                """,
                "returnByValue": True,
            })
            pos = coords.get("result", {}).get("value")
            if not pos:
                result["error"] = f"Element not found: {selector}"
                return result
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1,
            })
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1,
            })
            result["success"] = True
            result["selector"] = selector

        elif kind == "fill":
            selector = action.get("selector", "")
            value = action.get("value", "")
            if not selector:
                result["error"] = "selector is required"
                return result
            await cdp.send("Runtime.evaluate", {
                "expression": f"""
                    (() => {{
                        const el = document.querySelector('{_escape_js(selector)}');
                        if (!el) return false;
                        el.focus();
                        el.value = '';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        return true;
                    }})()
                """,
                "returnByValue": True,
            })
            await cdp.send("Input.insertText", {"text": value})
            await cdp.send("Runtime.evaluate", {
                "expression": f"""
                    (() => {{
                        const el = document.querySelector('{_escape_js(selector)}');
                        if (el) {{
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                        }}
                    }})()
                """,
                "returnByValue": True,
            })
            result["success"] = True
            result["selector"] = selector

        elif kind == "type":
            text = action.get("text", "")
            delay_ms = action.get("delay_ms", 50)
            for ch in text:
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "text": ch,
                })
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "text": ch,
                })
                await asyncio.sleep(delay_ms / 1000)
            result["success"] = True

        elif kind == "scroll":
            dx = action.get("dx", 0)
            dy = action.get("dy", 500)
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": 0, "y": 0,
                "deltaX": dx, "deltaY": dy,
            })
            result["success"] = True

        elif kind == "wait":
            ms = action.get("ms", 1000)
            selector = action.get("selector", "")
            if selector:
                # 等待元素出现
                deadline = time.time() + ms / 1000 + 10
                while time.time() < deadline:
                    check = await cdp.send("Runtime.evaluate", {
                        "expression": f"!!document.querySelector('{_escape_js(selector)}')",
                        "returnByValue": True,
                    })
                    if check.get("result", {}).get("value"):
                        result["success"] = True
                        break
                    await asyncio.sleep(0.2)
                else:
                    result["error"] = f"Timeout waiting for {selector}"
            else:
                await asyncio.sleep(ms / 1000)
                result["success"] = True

        elif kind == "evaluate":
            expression = action.get("expression", "")
            if not expression:
                result["error"] = "expression is required"
                return result
            eval_result = await cdp.send("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
            })
            result["success"] = True
            result["result"] = eval_result.get("result", {}).get("value")

        elif kind == "screenshot":
            path = action.get("path", "")
            if not path:
                path = tempfile.mktemp(suffix=".png", prefix="cdp_screenshot_")
            await cdp.send("Page.enable")
            img = await cdp.send("Page.captureScreenshot", {
                "format": "png",
                "quality": 90,
            })
            import base64
            data = base64.b64decode(img.get("data", ""))
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            result["success"] = True
            result["path"] = path
            result["size_bytes"] = len(data)

        else:
            result["error"] = (
                f"Unknown action kind: '{kind}'. "
                "Available: navigate, click, fill, type, scroll, wait, evaluate, screenshot"
            )

    except Exception as e:
        result["error"] = str(e)

    return result


async def _wait_for_load_event(cdp: _CDPSession, wait_until: str):
    """Wait for the specified load event"""
    if wait_until == "commit":
        await asyncio.sleep(0.3)
        return
    deadline = time.time() + 15
    target_event = {
        "domcontentloaded": "Page.domContentEventFired",
        "load": "Page.loadEventFired",
        "networkidle": None,  # 特殊处理
    }.get(wait_until, "Page.loadEventFired")

    if wait_until == "networkidle":
        # 等待 load 后再等 500ms 无网络活动
        while time.time() < deadline:
            events = cdp.pop_events("Page.loadEventFired")
            if events:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        return

    while time.time() < deadline:
        events = cdp.pop_events(target_event)
        if events:
            return
        await asyncio.sleep(0.1)


def _escape_js(s: str) -> str:
    """Escape a string for embedding in JS single-quoted string"""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")


# ─── 主工具类 ────────────────────────────────────────────────────

class ChromeDevtoolsTool(BaseTool):
    """Tool for Chrome DevTools Protocol — performance metrics & page interaction"""

    name = "chrome_devtools"
    description = (
        "Connect to Chrome via DevTools Protocol (CDP) to simulate page interactions "
        "and collect comprehensive performance data. "
        "Actions:\n"
        "  - navigate: Go to a URL\n"
        "  - click: Click an element by CSS selector\n"
        "  - fill: Fill a form field\n"
        "  - type: Type text character by character\n"
        "  - scroll: Scroll the page\n"
        "  - wait: Wait for time or an element\n"
        "  - evaluate: Execute JavaScript\n"
        "  - screenshot: Capture a screenshot\n"
        "  - collect_metrics: Collect all performance metrics\n"
        "  - run_trace: Record a performance trace (importable in chrome://tracing)\n"
        "  - full_audit: Navigate + simulate actions + collect metrics + optional trace\n"
        "Returns structured JSON with performance data including Navigation Timing, "
        "Resource Timing, Paint Timing, LCP, CLS, Long Tasks, and memory usage."
    )

    # 类级 Chrome 进程跟踪（多工具实例共享）
    _chrome_proc: Optional[subprocess.Popen] = None
    _chrome_port: int = _DEFAULT_PORT
    _chrome_user_data_dir: str = ""

    async def execute(
        self,
        action: str = "full_audit",
        url: str = "",
        actions: Optional[List[Dict[str, Any]]] = None,
        port: int = _DEFAULT_PORT,
        host: str = "127.0.0.1",
        launch: bool = False,
        trace: bool = False,
        trace_categories: str = "",
        trace_output: str = "",
        metrics_output: str = "",
        collect_delay_ms: int = _PERF_COLLECT_DELAY_MS,
        target_id: str = "",
        # screenshot / action-specific params
        path: str = "",
        selector: str = "",
        value: str = "",
        text: str = "",
        expression: str = "",
        delay_ms: int = 0,
        ms: int = 0,
        dx: int = 0,
        dy: int = 0,
        **kwargs,
    ) -> str:
        """
        Execute a Chrome DevTools action.

        Args:
            action: One of navigate, click, fill, type, scroll, wait, evaluate,
                    screenshot, collect_metrics, run_trace, full_audit
            url: Target URL (for navigate / full_audit)
            actions: List of page actions to simulate (for full_audit)
                     Each: {"kind": "click|fill|type|scroll|wait|evaluate", ...params}
            port: Chrome DevTools port (default 9222)
            host: Chrome DevTools host (default 127.0.0.1)
            launch: Launch a new Chrome instance if True
            trace: Record a performance trace (for full_audit / collect_metrics)
            trace_categories: Custom CDP trace categories
            trace_output: Path to save trace JSON (default: temp file)
            metrics_output: Path to save metrics JSON (default: temp file)
            collect_delay_ms: Delay before collecting metrics after last action
            target_id: Specific Chrome page target ID

        Returns:
            JSON string with performance data
        """
        started_at = time.perf_counter()

        # ── 确保 Chrome 可用 ──
        if launch or not _is_port_open(host, port):
            try:
                self._chrome_proc = _launch_chrome(port)
                self._chrome_port = port
            except RuntimeError as e:
                return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

        # ── 获取 WebSocket 端点 ──
        try:
            ws_url = _get_page_ws_endpoint(host, port, target_id)
        except RuntimeError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

        # ── 连接 CDP ──
        cdp = _CDPSession(ws_url)
        try:
            await cdp.connect()
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": f"Failed to connect CDP WebSocket: {e}",
            }, ensure_ascii=False)

        try:
            result = await self._run_action(
                cdp, action,
                url=url, actions=actions,
                trace=trace, trace_categories=trace_categories,
                trace_output=trace_output, metrics_output=metrics_output,
                collect_delay_ms=collect_delay_ms,
                path=path, selector=selector, value=value, text=text,
                expression=expression, delay_ms=delay_ms, ms=ms, dx=dx, dy=dy,
            )
            result["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
            result["cdp_host"] = host
            result["cdp_port"] = port
            return json.dumps(result, ensure_ascii=False, default=str)
        finally:
            await cdp.close()

    async def _run_action(
        self,
        cdp: _CDPSession,
        action: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Dispatch to the appropriate action handler"""

        if action == "collect_metrics":
            return await self._do_collect_metrics(cdp, **kwargs)

        if action == "run_trace":
            return await self._do_run_trace(cdp, **kwargs)

        if action == "full_audit":
            return await self._do_full_audit(cdp, **kwargs)

        # 单个操作
        action_map = {
            "navigate": "navigate",
            "click": "click",
            "fill": "fill",
            "type": "type",
            "scroll": "scroll",
            "wait": "wait",
            "evaluate": "evaluate",
            "screenshot": "screenshot",
        }
        kind = action_map.get(action)
        if not kind:
            available = list(action_map.keys()) + ["collect_metrics", "run_trace", "full_audit"]
            return {"success": False, "error": f"Unknown action: '{action}'", "available_actions": available}

        action_spec = {"kind": kind}
        # 从 kwargs 构建 action spec（跳过空值）
        for k in ("url", "selector", "value", "text", "delay_ms", "ms", "expression", "path", "dx", "dy"):
            if k in kwargs and kwargs[k] is not None and kwargs[k] != "" and kwargs[k] != 0:
                action_spec[k] = kwargs[k]

        action_result = await _execute_action(cdp, action_spec)
        return {"success": action_result.get("success", False), "action_result": action_result}

    async def _do_collect_metrics(
        self,
        cdp: _CDPSession,
        trace: bool = False,
        trace_categories: str = "",
        trace_output: str = "",
        metrics_output: str = "",
        collect_delay_ms: int = _PERF_COLLECT_DELAY_MS,
        **kwargs,
    ) -> Dict[str, Any]:
        """Collect performance metrics (and optionally trace)"""
        if collect_delay_ms > 0:
            await asyncio.sleep(collect_delay_ms / 1000)

        # 开始 trace（如果需要）
        trace_task = None
        if trace:
            await _start_trace(cdp, trace_categories)
            # trace 在后台运行，收集 metrics 后再停止

        metrics = await _collect_performance_metrics(cdp)

        trace_result = None
        if trace:
            if not trace_output:
                trace_output = tempfile.mktemp(suffix=".json", prefix="cdp_trace_")
            trace_result = await _stop_trace_and_save(cdp, trace_output)

        # 保存 metrics 到文件
        if not metrics_output:
            metrics_output = tempfile.mktemp(suffix=".json", prefix="cdp_metrics_")
        os.makedirs(os.path.dirname(metrics_output) or ".", exist_ok=True)
        with open(metrics_output, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)

        result = {
            "success": True,
            "metrics": metrics,
            "metrics_output": metrics_output,
            "metrics_output_size_bytes": os.path.getsize(metrics_output),
        }
        if trace_result:
            result["trace"] = trace_result
        return result

    async def _do_run_trace(
        self,
        cdp: _CDPSession,
        trace_categories: str = "",
        trace_output: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """Record a performance trace"""
        if not trace_output:
            trace_output = tempfile.mktemp(suffix=".json", prefix="cdp_trace_")

        await _start_trace(cdp, trace_categories)
        # 给一些时间让浏览器产生 trace 事件
        await asyncio.sleep(2)
        trace_result = await _stop_trace_and_save(cdp, trace_output)

        return {
            "success": True,
            "trace": trace_result,
        }

    async def _do_full_audit(
        self,
        cdp: _CDPSession,
        url: str = "",
        actions: Optional[List[Dict[str, Any]]] = None,
        trace: bool = False,
        trace_categories: str = "",
        trace_output: str = "",
        metrics_output: str = "",
        collect_delay_ms: int = _PERF_COLLECT_DELAY_MS,
        **kwargs,
    ) -> Dict[str, Any]:
        """Full audit: navigate → simulate actions → collect metrics → optional trace"""
        action_results: List[Dict[str, Any]] = []

        # 1) Navigate
        if url:
            nav_result = await _execute_action(cdp, {"kind": "navigate", "url": url, "wait_until": "load"})
            action_results.append(nav_result)
            if not nav_result.get("success"):
                return {
                    "success": False,
                    "error": f"Navigation failed: {nav_result.get('error')}",
                    "action_results": action_results,
                }

        # 2) 开始 trace（如果需要）
        if trace:
            await _start_trace(cdp, trace_categories)

        # 3) 执行模拟操作
        if actions:
            for i, act in enumerate(actions):
                result = await _execute_action(cdp, act)
                action_results.append(result)
                if not result.get("success"):
                    # 记录失败但继续执行
                    action_results[-1]["step_index"] = i

        # 4) 等待后采集指标
        if collect_delay_ms > 0:
            await asyncio.sleep(collect_delay_ms / 1000)

        metrics = await _collect_performance_metrics(cdp)

        # 5) 停止 trace
        trace_result = None
        if trace:
            if not trace_output:
                trace_output = tempfile.mktemp(suffix=".json", prefix="cdp_trace_")
            trace_result = await _stop_trace_and_save(cdp, trace_output)

        # 6) 保存 metrics
        if not metrics_output:
            metrics_output = tempfile.mktemp(suffix=".json", prefix="cdp_metrics_")
        os.makedirs(os.path.dirname(metrics_output) or ".", exist_ok=True)
        with open(metrics_output, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)

        # 7) 计算摘要
        summary = self._build_summary(metrics, action_results)

        result = {
            "success": True,
            "url": url,
            "action_count": len(action_results),
            "action_results": action_results,
            "summary": summary,
            "metrics": metrics,
            "metrics_output": metrics_output,
            "metrics_output_size_bytes": os.path.getsize(metrics_output),
        }
        if trace_result:
            result["trace"] = trace_result
        return result

    @staticmethod
    def _build_summary(metrics: Dict, action_results: List[Dict]) -> Dict[str, Any]:
        """Build a human-readable performance summary"""
        summary: Dict[str, Any] = {}

        nav = metrics.get("navigation_timing", {})
        if nav:
            summary["ttfb_ms"] = round(nav.get("ttfb", 0), 1)
            summary["dom_content_loaded_ms"] = round(nav.get("domContentLoaded", 0), 1)
            summary["full_load_ms"] = round(nav.get("fullLoad", 0), 1)
            summary["protocol"] = nav.get("protocol", "")
            summary["transfer_size_bytes"] = nav.get("transferSize", 0)

        paint = metrics.get("paint_timing", {})
        if paint:
            summary["first_paint_ms"] = round(paint.get("first-paint", 0), 1)
            summary["first_contentful_paint_ms"] = round(paint.get("first-contentful-paint", 0), 1)

        lcp = metrics.get("largest_contentful_paint", {})
        if lcp:
            summary["lcp_ms"] = round(lcp.get("startTime", 0), 1)
            summary["lcp_element"] = lcp.get("element", "")

        cls = metrics.get("cumulative_layout_shift", {})
        if cls:
            summary["cls_score"] = round(cls.get("value", 0), 4)

        resources = metrics.get("resource_timing", {})
        if resources:
            summary["resource_count"] = resources.get("count", 0)
            summary["total_resource_size_bytes"] = resources.get("total_transfer_size", 0)
            summary["resource_types"] = resources.get("by_type", {})

        long_tasks = metrics.get("long_tasks", [])
        if long_tasks:
            summary["long_task_count"] = len(long_tasks)
            summary["longest_task_ms"] = round(max(t.get("duration", 0) for t in long_tasks), 1)

        memory = metrics.get("memory")
        if memory:
            summary["js_heap_used_mb"] = round(memory.get("usedJSHeapSize", 0) / 1024 / 1024, 1)
            summary["js_heap_total_mb"] = round(memory.get("totalJSHeapSize", 0) / 1024 / 1024, 1)

        # 动作统计
        failed = [a for a in action_results if not a.get("success")]
        summary["actions_succeeded"] = len(action_results) - len(failed)
        summary["actions_failed"] = len(failed)

        return summary

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "DevTools action to perform",
                    "enum": [
                        "navigate", "click", "fill", "type", "scroll",
                        "wait", "evaluate", "screenshot",
                        "collect_metrics", "run_trace", "full_audit",
                    ],
                },
                "url": {
                    "type": "string",
                    "description": "Target URL (for navigate / full_audit)",
                },
                "actions": {
                    "type": "array",
                    "description": (
                        "List of page actions to simulate (for full_audit). "
                        'Each: {"kind": "click|fill|type|scroll|wait|evaluate", ...params}'
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string"},
                            "url": {"type": "string"},
                            "selector": {"type": "string"},
                            "value": {"type": "string"},
                            "text": {"type": "string"},
                            "delay_ms": {"type": "integer"},
                            "ms": {"type": "integer"},
                            "expression": {"type": "string"},
                            "dx": {"type": "number"},
                            "dy": {"type": "number"},
                        },
                    },
                },
                "port": {
                    "type": "integer",
                    "description": f"Chrome DevTools debugging port (default {_DEFAULT_PORT})",
                },
                "host": {
                    "type": "string",
                    "description": "Chrome DevTools host (default 127.0.0.1)",
                },
                "launch": {
                    "type": "boolean",
                    "description": "Launch a new Chrome instance if true",
                },
                "trace": {
                    "type": "boolean",
                    "description": "Record a performance trace",
                },
                "trace_categories": {
                    "type": "string",
                    "description": "Custom CDP trace categories (advanced)",
                },
                "trace_output": {
                    "type": "string",
                    "description": "Path to save trace JSON (default: temp file)",
                },
                "metrics_output": {
                    "type": "string",
                    "description": "Path to save metrics JSON (default: temp file)",
                },
                "collect_delay_ms": {
                    "type": "integer",
                    "description": f"Delay before collecting metrics after last action (default {_PERF_COLLECT_DELAY_MS})",
                },
                "target_id": {
                    "type": "string",
                    "description": "Specific Chrome page target ID",
                },
            },
            "required": ["action"],
        }

    # ─── 工具策略 ──────────────────────────────────────────────
    def get_policy_payload(self) -> Dict[str, Any]:
        from .base import build_tool_policy_payload
        policy = build_tool_policy_payload(
            self.name,
            description=self.description,
            override={
                "risk_level": "high",
                "approval": {
                    "kind": "conditional",
                    "required": False,
                    "notes": [
                        "Read-only metrics collection is safe. "
                        "Page interactions (click/fill/type) may mutate remote state. "
                        "Launching Chrome spawns a local process.",
                    ],
                },
                "sandbox": {
                    "mode": "browser_runtime",
                    "workspace_scope": "temp_or_explicit_path",
                    "network_access": "enabled",
                    "notes": [
                        "Connects to Chrome via CDP. Launches Chrome locally if requested.",
                    ],
                },
                "escalation": {
                    "possible": True,
                    "hint": "Escalate when page interactions may submit forms or write data.",
                    "triggers": ["remote_mutation", "chrome_process_launch"],
                },
                "side_effect_scope": "browser_interaction",
                "external_targets": ["web", "chrome_devtools"],
            },
        )
        policy["system_only"] = False
        return policy
