# -*- coding: utf-8 -*-
"""
Browser Tool — 使用 Playwright 进行浏览器自动化交互
支持导航、点击、输入、截图、执行 JS 等操作。
"""
from .base import BaseTool
import json
import os
import asyncio

# 延迟导入：仅在首次使用时加载 playwright
_playwright = None
_browser_instance = None
_page_instance = None


def _get_browser_path() -> str:
    """Find available Chromium/Chrome binary"""
    candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in candidates:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise RuntimeError(
        "No Chromium/Chrome binary found. "
        "Install chromium or set CHROMIUM_PATH env var."
    )


async def _ensure_browser():
    """Lazily launch a shared browser instance"""
    global _playwright, _browser_instance, _page_instance
    if _browser_instance is not None:
        return _browser_instance, _page_instance

    from playwright.async_api import async_playwright

    _playwright = await async_playwright().start()
    browser_path = _get_browser_path()
    _browser_instance = await _playwright.chromium.launch(
        executable_path=browser_path,
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    _page_instance = await _browser_instance.new_page()
    return _browser_instance, _page_instance


class BrowserTool(BaseTool):
    """Tool for browser automation via Playwright"""

    name = "browser"
    description = (
        "Control a headless browser to interact with web pages. "
        "Actions: navigate, click, fill, type, select, screenshot, "
        "get_content, get_text, evaluate (JS), wait, back, forward, "
        "close. Use this for testing web UIs, scraping dynamic content, "
        "or interacting with web applications."
    )

    async def execute(self, action: str, **kwargs) -> str:
        """
        Perform a browser action.

        Args:
            action: One of: navigate, click, fill, type, select,
                    screenshot, get_content, get_text, evaluate,
                    wait, back, forward, close, new_page
            **kwargs: Action-specific parameters

        Returns:
            JSON result string
        """
        try:
            browser, page = await _ensure_browser()
        except RuntimeError as e:
            return json.dumps({"success": False, "error": str(e)})
        except Exception as e:
            return json.dumps({"success": False, "error": f"Browser launch failed: {e}"})

        try:
            handler = getattr(self, f"_action_{action}", None)
            if handler is None:
                available = [
                    a.replace("_action_", "")
                    for a in dir(self)
                    if a.startswith("_action_")
                ]
                return json.dumps({
                    "success": False,
                    "error": f"Unknown action: '{action}'",
                    "available_actions": available,
                })
            return await handler(page, **kwargs)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    # ─── Actions ────────────────────────────────────────────────

    async def _action_navigate(self, page, url: str = "", wait_until: str = "load", **kw) -> str:
        """Navigate to a URL"""
        if not url:
            return json.dumps({"success": False, "error": "url is required"})
        resp = await page.goto(url, wait_until=wait_until, timeout=15000)
        return json.dumps({
            "success": True,
            "url": page.url,
            "title": await page.title(),
            "status": resp.status if resp else None,
        })

    async def _action_click(self, page, selector: str = "", **kw) -> str:
        """Click an element"""
        if not selector:
            return json.dumps({"success": False, "error": "selector is required"})
        await page.click(selector, timeout=5000)
        return json.dumps({"success": True, "action": "click", "selector": selector})

    async def _action_fill(self, page, selector: str = "", value: str = "", **kw) -> str:
        """Fill a form field (clears existing content first)"""
        if not selector:
            return json.dumps({"success": False, "error": "selector is required"})
        await page.fill(selector, value, timeout=5000)
        return json.dumps({"success": True, "action": "fill", "selector": selector, "value": value})

    async def _action_type(self, page, selector: str = "", text: str = "", delay_ms: int = 50, **kw) -> str:
        """Type text character by character (simulates real typing)"""
        if not selector:
            return json.dumps({"success": False, "error": "selector is required"})
        await page.type(selector, text, delay=delay_ms, timeout=5000)
        return json.dumps({"success": True, "action": "type", "selector": selector, "text": text})

    async def _action_select(self, page, selector: str = "", value: str = "", **kw) -> str:
        """Select an option in a <select> element"""
        if not selector:
            return json.dumps({"success": False, "error": "selector is required"})
        await page.select_option(selector, value, timeout=5000)
        return json.dumps({"success": True, "action": "select", "selector": selector, "value": value})

    async def _action_screenshot(self, page, path: str = "", full_page: bool = False, selector: str = "", **kw) -> str:
        """Take a screenshot of the current page or element"""
        if not path:
            import tempfile
            path = tempfile.mktemp(suffix=".png", prefix="browser_screenshot_")

        kwargs = {"path": path, "full_page": full_page}
        if selector:
            el = await page.query_selector(selector)
            if not el:
                return json.dumps({"success": False, "error": f"Element not found: {selector}"})
            await el.screenshot(path=path)
        else:
            await page.screenshot(**kwargs)

        size = os.path.getsize(path) if os.path.exists(path) else 0
        return json.dumps({"success": True, "path": path, "size_bytes": size, "full_page": full_page})

    async def _action_get_content(self, page, selector: str = "", **kw) -> str:
        """Get HTML content of the page or a specific element"""
        if selector:
            el = await page.query_selector(selector)
            if not el:
                return json.dumps({"success": False, "error": f"Element not found: {selector}"})
            html = await el.inner_html()
        else:
            html = await page.content()
        return json.dumps({"success": True, "content": html[:50000]})  # cap at 50KB

    async def _action_get_text(self, page, selector: str = "", **kw) -> str:
        """Get visible text content of the page or a specific element"""
        if selector:
            el = await page.query_selector(selector)
            if not el:
                return json.dumps({"success": False, "error": f"Element not found: {selector}"})
            text = await el.inner_text()
        else:
            text = await page.inner_text("body")
        return json.dumps({"success": True, "text": text[:50000]})

    async def _action_evaluate(self, page, expression: str = "", **kw) -> str:
        """Execute JavaScript in the page context"""
        if not expression:
            return json.dumps({"success": False, "error": "expression is required"})
        result = await page.evaluate(expression)
        # 安全序列化结果
        try:
            serialized = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            serialized = str(result)
        return json.dumps({"success": True, "result": serialized[:50000]})

    async def _action_wait(self, page, selector: str = "", timeout_ms: int = 5000, state: str = "visible", **kw) -> str:
        """Wait for an element to appear"""
        if not selector:
            return json.dumps({"success": False, "error": "selector is required"})
        await page.wait_for_selector(selector, timeout=timeout_ms, state=state)
        return json.dumps({"success": True, "action": "wait", "selector": selector, "state": state})

    async def _action_wait_for_url(self, page, url_pattern: str = "", timeout_ms: int = 10000, **kw) -> str:
        """Wait for the page URL to match a pattern"""
        if not url_pattern:
            return json.dumps({"success": False, "error": "url_pattern is required"})
        await page.wait_for_url(url_pattern, timeout=timeout_ms)
        return json.dumps({"success": True, "url": page.url})

    async def _action_back(self, page, **kw) -> str:
        """Go back in browser history"""
        await page.go_back()
        return json.dumps({"success": True, "url": page.url, "title": await page.title()})

    async def _action_forward(self, page, **kw) -> str:
        """Go forward in browser history"""
        await page.go_forward()
        return json.dumps({"success": True, "url": page.url, "title": await page.title()})

    async def _action_new_page(self, page, url: str = "", **kw) -> str:
        """Open a new tab/page and optionally navigate"""
        browser, _ = await _ensure_browser()
        global _page_instance
        _page_instance = await browser.new_page()
        if url:
            await _page_instance.goto(url, wait_until="load", timeout=15000)
        return json.dumps({
            "success": True,
            "action": "new_page",
            "url": _page_instance.url,
            "title": await _page_instance.title(),
        })

    async def _action_close(self, page, **kw) -> str:
        """Close the browser and release resources"""
        global _browser_instance, _page_instance, _playwright
        if _browser_instance:
            await _browser_instance.close()
        if _playwright:
            await _playwright.stop()
        _browser_instance = None
        _page_instance = None
        _playwright = None
        return json.dumps({"success": True, "action": "close"})

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Browser action to perform",
                    "enum": [
                        "navigate", "click", "fill", "type", "select",
                        "screenshot", "get_content", "get_text", "evaluate",
                        "wait", "wait_for_url", "back", "forward",
                        "new_page", "close",
                    ],
                },
                "url": {
                    "type": "string",
                    "description": "URL for navigate/new_page action",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for click/fill/type/select/wait/screenshot actions",
                },
                "value": {
                    "type": "string",
                    "description": "Value for fill/select action",
                },
                "text": {
                    "type": "string",
                    "description": "Text for type action",
                },
                "expression": {
                    "type": "string",
                    "description": "JavaScript expression for evaluate action",
                },
                "path": {
                    "type": "string",
                    "description": "Output file path for screenshot action",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (screenshot)",
                },
                "delay_ms": {
                    "type": "integer",
                    "description": "Typing delay in ms (type action)",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Timeout in ms (wait action)",
                },
                "state": {
                    "type": "string",
                    "description": "Wait state: visible/hidden/attached/detached",
                    "enum": ["visible", "hidden", "attached", "detached"],
                },
                "wait_until": {
                    "type": "string",
                    "description": "Navigation wait condition: load/domcontentloaded/networkidle/commit",
                },
            },
            "required": ["action"],
        }
