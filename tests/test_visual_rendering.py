"""
Frontend Visual Rendering Tests — Catown
==========================================

⚠️  重要说明：这些是**静态分析**测试，不是真正的视觉渲染测试。
    它们检查 HTML 源码中的 class 名和 CSS 规则，但**无法验证**
    浏览器实际渲染出来的视觉效果。真正的视觉测试需要浏览器引擎
    (Playwright/Puppeteer) 做 computed style 断言或截图对比。

本测试能做的：
  ✅ DOM 结构完整性（必要元素是否存在）
  ✅ CSS 类名正确性（class 拼写是否正确）
  ✅ 布局约束一致性（flex 父级是否有 min-h-0）
  ✅ 外部依赖引用（CDN 资源是否引入）
  ✅ API 数据流（数据能否正确传递到渲染函数）

本测试不能做的：
  ❌ 元素是否实际可见（不被其他元素遮挡）
  ❌ 内容是否溢出容器（computed overflow）
  ❌ 颜色/字体是否正确渲染
  ❌ 动画是否平滑执行
  ❌ 响应式布局在各断点的实际表现

Run:
    cd catown
    python3 -m pytest tests/test_visual_rendering.py -v
"""

import os
import sys
import time
import pytest
from fastapi.testclient import TestClient


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import tempfile
    backend_dir = os.path.join(os.path.dirname(__file__), '..', 'backend')
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.chdir(backend_dir)

    tmp = tempfile.mkdtemp(prefix="catown-vr-")
    os.environ["DATABASE_URL"] = os.path.join(tmp, "test.db")
    os.environ["LOG_LEVEL"] = "WARNING"

    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def html(client):
    return client.get("/").text


@pytest.fixture()
def sample_project(client):
    resp = client.post("/api/projects", json={
        "name": f"vr-{int(time.time() * 1000)}",
        "description": "visual-rendering-test"
    })
    project = resp.json()
    yield project
    client.delete(f"/api/projects/{project['id']}")


# ═══════════════════════════════════════════════
# 1. 布局约束一致性（最关键！）
# ═══════════════════════════════════════════════

class TestLayoutConstraintConsistency:
    """
    Flex 布局中，overflow-y-auto 的子元素要能滚动，其父级 flex 容器
    必须有 min-h-0（或固定 height/max-height），否则 min-height: auto
    会阻止子元素收缩，overflow 形同虚设，内容无限撑开。

    这是导致日志被挤出屏幕的根因。
    """

    def _get_attr(self, html, element_id, attr='class'):
        """提取指定 id 元素的属性值。"""
        import re
        # 匹配 <tag ... id="xxx" ... class="...">
        # 先找 id="xxx" 所在的标签
        pattern = rf'<\w+[^>]*id="{element_id}"[^>]*>'
        match = re.search(pattern, html)
        assert match, f"Element id='{element_id}' not found in HTML"
        tag = match.group(0)
        # 提取 class
        cls_match = re.search(r'class="([^"]*)"', tag)
        return cls_match.group(1) if cls_match else ""

    def test_side_panel_has_min_h_0(self, html):
        """side-panel 是 flex-col 父容器，必须有 min-h-0。"""
        cls = self._get_attr(html, 'side-panel')
        assert 'min-h-0' in cls, (
            "side-panel 缺少 min-h-0。"
            "flex 子元素默认 min-height: auto，overflow-y-auto 不生效，"
            "会导致 logs 撑开容器把 System Status 推出屏幕。"
        )

    def test_chat_container_has_min_h_0(self, html):
        """chat-container 是 flex-col 父容器，必须有 min-h-0。"""
        cls = self._get_attr(html, 'chat-container')
        assert 'min-h-0' in cls, (
            "chat-container 缺少 min-h-0。"
            "同理，messages-area 的 overflow-y-auto 不生效，"
            "大量消息会撑开容器。"
        )

    def test_messages_area_has_overflow_auto(self, html):
        """messages-area 必须有 overflow-y-auto。"""
        cls = self._get_attr(html, 'messages-area')
        assert 'overflow-y-auto' in cls, "messages-area 缺少 overflow-y-auto"

    def test_logs_content_has_overflow_auto(self, html):
        """logs-content 必须有 overflow-y-auto。"""
        cls = self._get_attr(html, 'logs-content')
        assert 'overflow-y-auto' in cls, "logs-content 缺少 overflow-y-auto"

    def test_logs_content_has_flex_1(self, html):
        """logs-content 必须有 flex-1 以占满父容器剩余空间。"""
        cls = self._get_attr(html, 'logs-content')
        assert 'flex-1' in cls, "logs-content 缺少 flex-1"

    def test_side_panel_parent_has_overflow_hidden(self, html):
        """side-panel 的父级（chat-area wrapper）必须有 overflow-hidden。"""
        import re
        # 找包含 side-panel 的直接父 div
        pattern = r'<div[^>]*class="([^"]*)"[^>]*>\s*<!-- Chat.*?-->\s*<div[^>]*id="chat-container"'
        # 简化：找 flex-1 flex overflow-hidden 的那个 wrapper
        wrapper_match = re.search(r'<div[^>]*class="[^"]*flex-1 flex overflow-hidden[^"]*"[^>]*>', html)
        assert wrapper_match, (
            "未找到包含 chat-container 和 side-panel 的 flex wrapper，"
            "它必须有 overflow-hidden 防止外层滚动。"
        )

    def test_flex_col_children_have_overflow_when_scrollable(self, html):
        """
        系统性检查：所有 flex-col 容器中，有 overflow-y-auto 的子元素，
        其父级必须有 min-h-0 或固定高度。
        """
        import re

        # 已知的 flex-col 容器 + 滚动子元素对
        pairs = [
            ('side-panel', 'logs-content'),
            ('side-panel', 'config-content'),
            ('chat-container', 'messages-area'),
        ]

        for parent_id, child_id in pairs:
            parent_cls = self._get_attr(html, parent_id)
            child_cls = self._get_attr(html, child_id)

            if 'overflow-y-auto' in child_cls or 'overflow-auto' in child_cls:
                assert 'min-h-0' in parent_cls or 'h-' in parent_cls, (
                    f"{parent_id} (flex-col) 包含 {child_id} (overflow-y-auto)，"
                    f"但缺少 min-h-0 或固定高度约束。"
                    f"这会导致 overflow 不生效，内容撑开容器。"
                )


# ═══════════════════════════════════════════════
# 2. DOM 结构完整性
# ═══════════════════════════════════════════════

class TestDOMStructure:
    """验证必要的 DOM 元素存在。"""

    def test_html_has_doctype(self, html):
        assert html.strip().startswith("<!DOCTYPE html>") or html.strip().startswith("<!doctype html>")

    def test_sidebar_exists(self, html):
        assert 'id="sidebar"' in html

    def test_main_content_exists(self, html):
        assert '<main' in html

    def test_header_exists(self, html):
        assert 'id="main-header"' in html

    def test_chat_container_exists(self, html):
        assert 'id="chat-container"' in html

    def test_messages_area_exists(self, html):
        assert 'id="messages-area"' in html

    def test_message_input_exists(self, html):
        assert 'id="message-input"' in html

    def test_side_panel_exists(self, html):
        assert 'id="side-panel"' in html

    def test_agent_status_bar_exists(self, html):
        assert 'id="agent-status-bar"' in html

    def test_rooms_list_exists(self, html):
        assert 'id="rooms-list"' in html

    def test_logs_content_exists(self, html):
        assert 'id="logs-content"' in html

    def test_config_content_exists(self, html):
        assert 'id="config-content"' in html

    def test_pipeline_dashboard_exists(self, html):
        assert "pipeline" in html.lower()
        assert "renderPipelineContent" in html


# ═══════════════════════════════════════════════
# 3. 外部依赖引用
# ═══════════════════════════════════════════════

class TestExternalDependencies:
    """验证 CDN 资源被正确引用。"""

    def test_tailwind_cdn(self, html):
        assert "tailwindcss" in html

    def test_font_awesome_cdn(self, html):
        assert "font-awesome" in html or "fontawesome" in html

    def test_google_fonts_inter(self, html):
        assert "Inter" in html

    def test_google_fonts_jetbrains_mono(self, html):
        assert "JetBrains Mono" in html

    def test_marked_js_cdn(self, html):
        assert "marked/marked.min.js" in html

    def test_highlight_js_cdn(self, html):
        assert "highlight.min.js" in html
        assert "github-dark.min.css" in html


# ═══════════════════════════════════════════════
# 4. CSS 规则完整性
# ═══════════════════════════════════════════════

class TestCSSRules:
    """验证关键 CSS 规则在 HTML 的 <style> 块中。"""

    def test_body_background(self, html):
        assert "background-color: #0F1115" in html

    def test_glass_panel_style(self, html):
        assert "backdrop-filter: blur" in html

    def test_scrollbar_thumb_style(self, html):
        assert "::-webkit-scrollbar-thumb" in html

    def test_markdown_content_styles(self, html):
        for selector in [".markdown-content h1", ".markdown-content code",
                         ".markdown-content pre", ".markdown-content blockquote",
                         ".markdown-content table"]:
            assert selector in html, f"Missing CSS rule: {selector}"

    def test_mention_dropdown_styles(self, html):
        assert ".agent-mention-dropdown" in html
        assert ".agent-mention-item:hover" in html

    def test_agent_status_styles(self, html):
        assert "agent-status-idle" in html
        assert "agent-status-thinking" in html
        assert "agent-status-executing" in html

    def test_pulse_animation(self, html):
        assert "@keyframes pulse" in html

    def test_typing_animation(self, html):
        assert "@keyframes typing" in html

    def test_no_scrollbar_class(self, html):
        assert "scrollbar-width: none" in html


# ═══════════════════════════════════════════════
# 5. JS 函数完整性
# ═══════════════════════════════════════════════

class TestJSFunctions:
    """验证关键渲染函数存在。"""

    def test_render_messages(self, html):
        assert "function renderMessages" in html

    def test_render_agents(self, html):
        assert "function renderAgents" in html

    def test_render_rooms(self, html):
        assert "function renderRooms" in html

    def test_set_agent_status(self, html):
        assert "function setAgentStatus" in html

    def test_render_markdown(self, html):
        assert "function renderMarkdown" in html

    def test_show_toast(self, html):
        assert "function showToast" in html

    def test_format_time(self, html):
        assert "function formatTime" in html

    def test_get_agent_color(self, html):
        assert "function getAgentColor" in html

    def test_escape_html(self, html):
        assert "function escapeHtml" in html

    def test_connect_websocket(self, html):
        assert "function connectWebSocket" in html

    def test_send_message(self, html):
        assert "function sendMessage" in html

    def test_select_project(self, html):
        assert "function selectProject" in html


# ═══════════════════════════════════════════════
# 6. 响应式断点
# ═══════════════════════════════════════════════

class TestResponsiveBreakpoints:
    """验证响应式 class 存在（不能验证实际效果）。"""

    def test_viewport_meta(self, html):
        assert 'name="viewport"' in html
        assert "width=device-width" in html

    def test_sidebar_mobile_hidden_class(self, html):
        assert "mobile-hidden" in html

    def test_sidebar_toggle_function(self, html):
        assert "toggleSidebar()" in html

    def test_sidebar_media_query(self, html):
        assert "@media (min-width: 768px)" in html

    def test_lg_breakpoint_for_side_panel(self, html):
        assert "lg:flex" in html or "lg:hidden" in html

    def test_md_breakpoint_classes(self, html):
        assert "md:" in html


# ═══════════════════════════════════════════════
# 7. Agent 状态指示器
# ═══════════════════════════════════════════════

class TestAgentStatusIndicators:
    """验证 Agent 状态的 CSS class 和图标引用。"""

    def test_agent_chip_structure(self, html):
        assert "agent-status-chip" in html
        assert "agent-avatar" in html
        assert "agent-status-icon" in html
        assert "agent-status-label" in html

    def test_idle_icon(self, html):
        assert "fa-moon" in html

    def test_thinking_spinner(self, html):
        assert "fa-spinner" in html
        assert "fa-spin" in html

    def test_executing_gear(self, html):
        assert "fa-gear" in html

    def test_pulse_animation_on_thinking(self, html):
        assert "animate-pulse" in html

    def test_agent_color_palette(self, html):
        colors = ["purple", "emerald", "amber", "rose", "cyan", "indigo"]
        for c in colors:
            assert c in html, f"Missing agent color: {c}"


# ═══════════════════════════════════════════════
# 8. Pipeline 视觉元素
# ═══════════════════════════════════════════════

class TestPipelineVisualElements:
    """验证 Pipeline Dashboard 的视觉标记。"""

    def test_stage_status_emojis(self, html):
        for emoji in ["⏳", "🔄", "🚧", "✅", "❌", "⏪"]:
            assert emoji in html, f"Missing stage emoji: {emoji}"

    def test_pipeline_status_colors(self, html):
        for cls in ["text-emerald-400", "text-yellow-400",
                     "text-blue-400", "text-red-400"]:
            assert cls in html, f"Missing pipeline color: {cls}"

    def test_pipeline_action_button_colors(self, html):
        assert "bg-emerald-600" in html   # start/approve
        assert "bg-yellow-600" in html    # pause
        assert "bg-red-600" in html       # reject

    def test_pipeline_message_type_colors(self, html):
        assert "text-blue-300" in html     # AGENT_OUTPUT
        assert "text-emerald-300" in html  # STAGE_OUTPUT
        assert "text-yellow-300" in html   # AGENT_QUESTION
        assert "text-purple-300" in html   # AGENT_REPLY
        assert "text-orange-300" in html   # HUMAN_INSTRUCT

    def test_file_browser_icons(self, html):
        assert "📁" in html
        assert "📄" in html


# ═══════════════════════════════════════════════
# 9. Toast 通知样式
# ═══════════════════════════════════════════════

class TestToastNotifications:
    """验证 Toast 通知的样式定义。"""

    def test_toast_position(self, html):
        assert "fixed top-4 right-4 z-50" in html

    def test_toast_type_colors(self, html):
        for cls in ["bg-emerald-600", "bg-red-600", "bg-blue-600", "bg-amber-600"]:
            assert cls in html, f"Missing toast color: {cls}"

    def test_toast_icons(self, html):
        for icon in ["fa-check-circle", "fa-times-circle",
                     "fa-info-circle", "fa-exclamation-circle"]:
            assert icon in html, f"Missing toast icon: {icon}"

    def test_toast_animate_in(self, html):
        assert "opacity-0" in html
        assert "translate-y-[-10px]" in html


# ═══════════════════════════════════════════════
# 10. Input 交互反馈
# ═══════════════════════════════════════════════

class TestInputVisualFeedback:
    """验证输入区的视觉反馈 class。"""

    def test_input_focus_ring(self, html):
        assert "focus-within:border-accent-500" in html
        assert "focus-within:ring-1" in html

    def test_input_placeholder(self, html):
        assert "Message room or @agent" in html

    def test_command_hints(self, html):
        assert "Mention Agent" in html
        assert "Create Room" in html
        assert "Run Script" in html

    def test_mention_dropdown_structure(self, html):
        assert "agent-mention-dropdown" in html
        assert "agent-mention-item" in html

    def test_send_button_style(self, html):
        assert "bg-accent-500" in html
        assert "hover:bg-accent-400" in html


# ═══════════════════════════════════════════════
# 11. API → 渲染数据流
# ═══════════════════════════════════════════════

class TestAPIRenderDataFlow:
    """验证 API 数据能正确传递给渲染函数（端到端）。"""

    def test_agents_available_for_rendering(self, client):
        agents = client.get("/api/agents").json()
        assert len(agents) >= 5
        names = {a["name"] for a in agents}
        for role in ["analyst", "architect", "developer", "tester", "release"]:
            assert role in names

    def test_projects_available_for_sidebar(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_messages_available_for_chat(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        client.post(f"/api/chatrooms/{cid}/messages", json={
            "content": "render test",
            "message_type": "user"
        })
        msgs = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(m["content"] == "render test" for m in msgs)

    def test_pipeline_stages_order(self, client):
        resp = client.post("/api/projects", json={
            "name": f"vr-order-{int(time.time())}",
            "description": ""
        })
        proj = resp.json()
        pipe = client.post("/api/pipelines", json={
            "project_id": proj["id"],
            "pipeline_name": "default"
        }).json()
        detail = client.get(f"/api/pipelines/{pipe['id']}").json()
        runs = detail.get("runs", [])
        if runs:
            stages = runs[-1].get("stages", [])
            names = [s["stage_name"] for s in stages]
            assert names == ["analysis", "architecture", "development", "testing", "release"]
        client.delete(f"/api/projects/{proj['id']}")

    def test_config_provides_global_and_agent(self, client):
        data = client.get("/api/config").json()
        assert "global_llm" in data
        assert "agent_llm_configs" in data
        assert len(data["agent_llm_configs"]) >= 5


# ═══════════════════════════════════════════════
# 12. Markdown 渲染函数配置
# ═══════════════════════════════════════════════

class TestMarkdownRendering:
    """验证 Markdown 渲染管线的配置。"""

    def test_marked_configured(self, html):
        assert "marked.setOptions" in html
        assert "breaks: true" in html
        assert "gfm: true" in html

    def test_highlight_js_integration(self, html):
        assert "hljs.highlight" in html
        assert "hljs.highlightAuto" in html

    def test_render_function_uses_marked(self, html):
        assert "marked.parse(content)" in html

    def test_render_fallback_to_escape(self, html):
        """renderMarkdown 出错时 fallback 到 escapeHtml。"""
        assert "escapeHtml(content)" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
