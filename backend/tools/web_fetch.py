# -*- coding: utf-8 -*-
"""
Web Fetch Tool - 抓取网页内容并提取可读文本
"""
from .base import BaseTool
import urllib.request
import urllib.parse
import re
import html as html_module


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

            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; CatownBot/1.0)",
                "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
            })

            with urllib.request.urlopen(req, timeout=15) as response:
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()

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

        except urllib.error.HTTPError as e:
            return f"[WebFetch] HTTP {e.code}: {e.reason} for {url}"
        except Exception as e:
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
