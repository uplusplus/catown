# -*- coding: utf-8 -*-
"""
Web Fetch Tool - 抓取网页内容并提取可读文本
"""
from .base import BaseTool
import urllib.parse
import httpx
import re
import html as html_module
import time

from monitoring import monitor_network_buffer


class WebFetchTool(BaseTool):
    """抓取网页内容，提取可读文本"""

    name = "web_fetch"
    description = "Fetch a URL and return the readable text content. Strips HTML tags and scripts."

    async def execute(self, url: str, max_chars: int = 8000, **kwargs) -> str:
        """
        抓取网页并提取文本

        Args:
            url: 要抓取的 URL
            max_chars: 返回内容最大字符数（默认 8000）

        Returns:
            提取的文本内容
        """
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return f"[WebFetch] Invalid URL scheme: {parsed.scheme}"

            started_at = time.perf_counter()
            async with httpx.AsyncClient(
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CatownBot/1.0)",
                    "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
                },
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                raw = response.content
                monitor_network_buffer.append(
                    {
                        "category": "backend_other",
                        "source": "backend",
                        "protocol": parsed.scheme.upper(),
                        "from_entity": "Backend",
                        "to_entity": parsed.netloc or url,
                        "request_direction": f"Backend -> {parsed.netloc or url}",
                        "response_direction": f"{parsed.netloc or url} -> Backend",
                        "method": "GET",
                        "url": url,
                        "host": parsed.netloc,
                        "path": parsed.path or "/",
                        "status_code": response.status_code,
                        "success": True,
                        "request_bytes": 0,
                        "response_bytes": len(raw),
                        "duration_ms": int((time.perf_counter() - started_at) * 1000),
                        "content_type": content_type,
                        "preview": self._extract_text(raw.decode("utf-8", errors="replace"))[:280] if raw else "",
                    }
                )

            # 尝试解码
            for enc in ("utf-8", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode("utf-8", errors="replace")

            # 如果是 HTML，提取文本
            if "html" in content_type.lower() or text.strip().startswith("<"):
                text = self._extract_text(text)

            # 截断
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n...(truncated, {len(text)} total chars)"

            return f"[WebFetch] {url}\n\n{text}"

        except httpx.HTTPStatusError as e:
            monitor_network_buffer.append(
                {
                    "category": "backend_other",
                    "source": "backend",
                    "protocol": parsed.scheme.upper() if 'parsed' in locals() else "HTTP",
                    "from_entity": "Backend",
                    "to_entity": parsed.netloc if 'parsed' in locals() else url,
                    "request_direction": f"Backend -> {parsed.netloc if 'parsed' in locals() else url}",
                    "response_direction": f"{parsed.netloc if 'parsed' in locals() else url} -> Backend",
                    "method": "GET",
                    "url": url,
                    "host": parsed.netloc if 'parsed' in locals() else "",
                    "path": parsed.path if 'parsed' in locals() else "",
                    "status_code": e.response.status_code,
                    "success": False,
                    "duration_ms": int((time.perf_counter() - started_at) * 1000) if 'started_at' in locals() else 0,
                    "content_type": e.response.headers.get("Content-Type", ""),
                    "error": str(e),
                }
            )
            return f"[WebFetch] HTTP {e.response.status_code}: {e.response.reason_phrase} for {url}"
        except Exception as e:
            monitor_network_buffer.append(
                {
                    "category": "backend_other",
                    "source": "backend",
                    "protocol": parsed.scheme.upper() if 'parsed' in locals() else "HTTP",
                    "from_entity": "Backend",
                    "to_entity": parsed.netloc if 'parsed' in locals() else url,
                    "request_direction": f"Backend -> {parsed.netloc if 'parsed' in locals() else url}",
                    "response_direction": f"{parsed.netloc if 'parsed' in locals() else url} -> Backend",
                    "method": "GET",
                    "url": url,
                    "host": parsed.netloc if 'parsed' in locals() else "",
                    "path": parsed.path if 'parsed' in locals() else "",
                    "success": False,
                    "duration_ms": int((time.perf_counter() - started_at) * 1000) if 'started_at' in locals() else 0,
                    "error": str(e),
                }
            )
            return f"[WebFetch] Error fetching {url}: {str(e)}"

    def _extract_text(self, html: str) -> str:
        """从 HTML 提取可读文本"""
        # 移除 script/style 块
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # 移除 HTML 注释
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        # 常见块级元素换行
        html = re.sub(r'<(br|hr|p|div|li|h[1-6]|tr|blockquote)[^>]*/?>', '\n', html, flags=re.IGNORECASE)
        # 移除所有标签
        text = re.sub(r'<[^>]+>', '', html)
        # 解码 HTML 实体
        text = html_module.unescape(text)
        # 合并空白
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 8000)",
                    "default": 8000
                }
            },
            "required": ["url"]
        }
