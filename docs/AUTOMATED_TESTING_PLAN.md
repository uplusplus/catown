# Catown 自动测试方案

> 调研人：Roy 🧪
> 日期：2026-04-04

---

## 推荐方案：Playwright（Python）

### 为什么选 Playwright

| 维度 | Playwright | Selenium | Requests（当前） |
|------|-----------|----------|----------------|
| 前端交互 | ✅ 浏览器自动化 | ✅ 浏览器自动化 | ❌ 无 |
| @提及下拉 | ✅ 直接模拟点击/键盘 | ✅ | ❌ |
| WebSocket | ✅ 内置支持 | ⚠️ 需要插件 | ❌ |
| API 测试 | ✅ 内置请求 | ❌ 需配合 | ✅ |
| 速度 | ⚡ 快 | 🐢 慢 | ⚡ 极快 |
| 安装复杂度 | 中（需浏览器） | 低 | 无 |
| 代码可维护性 | 高 | 低 | 高 |

### 安装步骤

```bash
pip install playwright
playwright install chromium   # 下载 Chromium 浏览器（约 150MB）
```

### 测试覆盖范围

| 测试套件 | 内容 | 优先级 |
|---------|------|--------|
| **API 测试** | REST 接口、状态码、数据结构 | P0 |
| **前端 E2E** | 页面渲染、@提及交互、消息发送 | P0 |
| **WebSocket** | 实时推送、断线重连 | P1 |
| **性能** | 响应时间、并发 | P2 |

### 示例测试代码

```python
from playwright.async_api import async_playwright
import httpx
import pytest

BASE_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:3001"

# ==== API 测试 ====
async def test_api_status():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/api/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

# ==== 前端 E2E 测试 ====
async def test_page_loads():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(FRONTEND_URL)
        assert "Catown" in await page.title()
        await browser.close()

async def test_at_mention_dropdown():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(FRONTEND_URL)
        
        # 等待页面 Agent 数据加载
        await page.wait_for_selector("#agent-status-bar")
        
        # 输入 @ 触发下拉
        input_box = page.locator("#message-input")
        await input_box.click()
        await input_box.fill("@")
        await page.wait_for_selector(".agent-mention-dropdown")
        
        # 验证下拉列表出现
        dropdown = page.locator(".agent-mention-dropdown")
        assert await dropdown.is_visible()
        
        # 输入过滤
        await input_box.press("a")
        await page.wait_for_timeout(200)
        items = await page.locator(".agent-mention-item").count()
        assert items > 0
        
        await browser.close()

async def test_send_message_and_get_response():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(FRONTEND_URL)
        
        # 等待项目加载
        await page.wait_for_selector("#rooms-list")
        
        # 发送消息
        input_box = page.locator("#message-input")
        await input_box.click()
        await input_box.fill("你好")
        
        # 点击发送按钮
        await page.click('button[onclick="sendMessage()"]')
        
        # 等待 Agent 响应（最多 15s）
        await page.wait_for_selector(".markdown-content", timeout=15000)
        
        # 验证 Agent 有回复
        messages = await page.locator(".markdown-content").count()
        assert messages >= 1
        
        await browser.close()

async def test_websocket_realtime():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(FRONTEND_URL)
        
        # 验证 WebSocket 连接状态
        # 页面日志面板会显示 WS 连接状态
        await page.wait_for_timeout(2000)
        logs = await page.locator("#logs-content").inner_text()
        assert "Connected" in logs or "connected" in logs.lower()
        
        await browser.close()
```

### 集成到 CI/CD

```yaml
# .github/workflows/test.yml 示例
name: Catown E2E Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          pip install playwright pytest httpx pytest-asyncio
          playwright install chromium
      - name: Start backend
        run: python -m uvicorn backend.main:app --port 8000 &
      - name: Start frontend
        run: python -m http.server 3001 -d frontend/public &
      - name: Wait for services
        run: sleep 5
      - name: Run tests
        run: pytest tests/e2e/ -v
```

### 预期产出

1. **API 自动化回归**（10+ 用例，秒级完成）
2. **前端 E2E 测试**（5+ 用例，覆盖 @提及、发消息、Agent 响应）
3. **WebSocket 连通性验证**（2+ 用例）
4. **CI 集成**：每次 commit 自动跑

### 成本

- **安装时间**：约 5 分钟（含浏览器下载）
- **单轮测试时间**：约 30 秒
- **维护成本**：低（API 稳定后基本不需要改）

### 备选方案

如果 Playwright 安装有问题（需要浏览器），可以用纯 API + 请求的方式：
- `requests` / `httpx` — 已可用，覆盖后端 API
- `websockets` 库 — Python WebSocket 客户端，可测试实时通信
- 缺点：无法测试前端 UI 交互（@提及下拉等）

---

**结论**：推荐 Playwright Python，一套方案覆盖 API + 前端 E2E + WebSocket。
如果当前环境受限，先用 `requests` + `websockets` 覆盖后端，前端 E2E 等环境准备好再接入。
