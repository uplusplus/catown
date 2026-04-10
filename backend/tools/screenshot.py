# -*- coding: utf-8 -*-
"""
Screenshot Tool — 使用 Headless Chromium 截取网页截图
"""
from .base import BaseTool
import subprocess
import tempfile
import os
import json


class ScreenshotTool(BaseTool):
    """Tool for capturing screenshots of web pages or HTML content"""

    name = "screenshot"
    description = (
        "Take a screenshot of a web page URL or local HTML file. "
        "Returns the path to the saved PNG image. "
        "Supports full-page capture, custom viewport size, and element-specific screenshots."
    )

    # 浏览器路径：优先环境变量，自动探测
    BROWSER_PATHS = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]

    def _find_browser(self) -> str:
        """Find available Chromium/Chrome binary"""
        for p in self.BROWSER_PATHS:
            if p and os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        raise RuntimeError(
            "No Chromium/Chrome binary found. "
            "Install chromium or set CHROMIUM_PATH env var."
        )

    async def execute(
        self,
        url: str = None,
        html: str = None,
        output_path: str = None,
        width: int = 1280,
        height: int = 720,
        full_page: bool = False,
        selector: str = None,
        wait_ms: int = 1000,
        **kwargs,
    ) -> str:
        """
        Capture a screenshot.

        Args:
            url: Web page URL or local file path to capture
            html: Raw HTML string to render (alternative to url)
            output_path: Where to save the PNG (default: temp file)
            width: Viewport width in pixels (default: 1280)
            height: Viewport height in pixels (default: 720)
            full_page: Capture full scrollable page (default: False)
            selector: CSS selector to capture only a specific element
            wait_ms: Wait time in ms after page load before screenshot (default: 1000)

        Returns:
            JSON with screenshot path and metadata
        """
        if not url and not html:
            return "[Screenshot] Error: must provide either 'url' or 'html'"

        try:
            browser = self._find_browser()
        except RuntimeError as e:
            return f"[Screenshot] Error: {e}"

        # 如果传了 html，写成临时文件
        temp_html = None
        if html and not url:
            temp_html = tempfile.NamedTemporaryFile(
                mode="w", suffix=".html", delete=False, encoding="utf-8"
            )
            temp_html.write(html)
            temp_html.close()
            url = f"file://{temp_html.name}"

        # 输出路径
        if not output_path:
            output_path = tempfile.mktemp(suffix=".png", prefix="screenshot_")

        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        try:
            # --- 方案 A：Chromium --headless=new (Chrome 112+) 截图 ---
            cmd = [
                browser,
                "--headless=new",
                f"--window-size={width},{height}",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                f"--screenshot={output_path}",
            ]

            if full_page:
                cmd.append("--full-page-screenshot")

            if wait_ms:
                cmd.append(f"--virtual-time-budget={wait_ms}")

            cmd.append(url)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "HOME": "/tmp"},
            )

            # 如果 --headless=new 不支持，fallback 到老版 --headless
            if result.returncode != 0 and "--headless=new" in " ".join(cmd):
                cmd[1] = "--headless"
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env={**os.environ, "HOME": "/tmp"},
                )

            if result.returncode != 0:
                err = result.stderr.strip()[:500]
                return f"[Screenshot] Chromium error (exit {result.returncode}): {err}"

            if not os.path.exists(output_path):
                return "[Screenshot] Error: screenshot file was not created"

            file_size = os.path.getsize(output_path)

            # 截取指定元素：用 Node.js + Puppeteer-core 脚本（如果 Chromium 已经拿到）
            if selector and os.path.exists(output_path):
                # 元素截图需要 JS，用 Node 脚本处理
                element_result = await self._element_screenshot(
                    browser, url, output_path, selector, width, height, wait_ms
                )
                if element_result:
                    return element_result

            result_data = {
                "success": True,
                "path": output_path,
                "size_bytes": file_size,
                "viewport": f"{width}x{height}",
                "full_page": full_page,
            }
            return json.dumps(result_data, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return "[Screenshot] Error: timeout (30s limit)"
        except Exception as e:
            return f"[Screenshot] Error: {str(e)}"
        finally:
            if temp_html and os.path.exists(temp_html.name):
                os.unlink(temp_html.name)

    async def _element_screenshot(
        self, browser, url, output_path, selector, width, height, wait_ms
    ) -> str | None:
        """Use Node.js to capture a specific element via puppeteer-core"""
        # 生成 Node.js 截图脚本
        js_script = f"""
const puppeteer = require('puppeteer-core');
(async () => {{
    const browser = await puppeteer.launch({{
        executablePath: '{browser}',
        headless: 'new',
        args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
    }});
    const page = await browser.newPage();
    await page.setViewport({{ width: {width}, height: {height} }});
    await page.goto('{url}', {{ waitUntil: 'networkidle2', timeout: 15000 }});
    await new Promise(r => setTimeout(r, {wait_ms}));
    const el = await page.$('{selector}');
    if (!el) {{
        console.log(JSON.stringify({{ error: "Element not found: {selector}" }}));
    }} else {{
        await el.screenshot({{ path: '{output_path}' }});
        const fs = require('fs');
        const stat = fs.statSync('{output_path}');
        console.log(JSON.stringify({{
            success: true,
            path: '{output_path}',
            size_bytes: stat.size,
            selector: '{selector}'
        }}));
    }}
    await browser.close();
}})();
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as f:
            f.write(js_script)
            script_path = f.name

        try:
            # 检查 puppeteer-core 是否可用
            check = subprocess.run(
                ["node", "-e", "require('puppeteer-core')"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if check.returncode != 0:
                return None  # 没有 puppeteer-core，跳过元素截图

            result = subprocess.run(
                ["node", script_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.stdout.strip():
                return result.stdout.strip()
            return None
        except Exception:
            return None
        finally:
            if os.path.exists(script_path):
                os.unlink(script_path)

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Web page URL or local file path to screenshot",
                },
                "html": {
                    "type": "string",
                    "description": "Raw HTML string to render (alternative to url)",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path for the PNG screenshot",
                },
                "width": {
                    "type": "integer",
                    "description": "Viewport width in pixels (default: 1280)",
                },
                "height": {
                    "type": "integer",
                    "description": "Viewport height in pixels (default: 720)",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: false)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector to capture only a specific element (requires puppeteer-core)",
                },
                "wait_ms": {
                    "type": "integer",
                    "description": "Wait time in ms after page load before screenshot (default: 1000)",
                },
            },
            "required": [],
        }
