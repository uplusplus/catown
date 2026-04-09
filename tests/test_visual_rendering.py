"""
Frontend Visual Rendering Tests — Catown
==========================================
Verifies that the frontend HTML/CSS/JS delivers correct visual output:

  1. DOM structure & layout hierarchy
  2. CSS / Tailwind class presence for visual components
  3. Color theme & dark-mode tokens
  4. Responsive design breakpoints
  5. Agent status visual indicators (idle / thinking / executing)
  6. Markdown rendering styles
  7. Modal & overlay rendering
  8. Toast notification styles
  9. Animation & transition classes
 10. Scrollbar & overflow styles
 11. Icon / font assets loading
 12. Pipeline Dashboard visual elements
 13. Input & interaction visual feedback
 14. Sidebar & navigation visual state

Run:
    cd catown
    python3 -m pytest tests/test_visual_rendering.py -v
"""

import os
import sys
import re
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
    """Cached HTML source of the index page."""
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
# 1. DOM Structure & Layout Hierarchy
# ═══════════════════════════════════════════════

class TestDOMStructure:
    """Verify the essential DOM tree that supports visual layout."""

    def test_html_has_doctype(self, html):
        assert html.strip().startswith("<!DOCTYPE html>") or html.strip().startswith("<!doctype html>")

    def test_body_is_flex_container(self, html):
        """Body must be flex for sidebar + main layout."""
        assert 'class="h-screen overflow-hidden flex' in html

    def test_sidebar_element_exists(self, html):
        assert 'id="sidebar"' in html

    def test_main_content_area_exists(self, html):
        assert '<main' in html
        assert 'flex-1 flex flex-col' in html

    def test_header_bar_exists(self, html):
        assert 'id="main-header"' in html

    def test_chat_container_exists(self, html):
        assert 'id="chat-container"' in html

    def test_messages_area_exists(self, html):
        assert 'id="messages-area"' in html

    def test_input_area_exists(self, html):
        assert 'id="message-input"' in html

    def test_side_panel_exists(self, html):
        assert 'id="side-panel"' in html

    def test_agent_status_bar_exists(self, html):
        assert 'id="agent-status-bar"' in html

    def test_overlay_layer_exists(self, html):
        """Background overlay for glass-panel effect."""
        assert 'bg-dark-900/95 z-0' in html

    def test_sidebar_has_logo_section(self, html):
        assert 'Catown' in html
        assert 'fa-layer-group' in html

    def test_sidebar_has_navigation(self, html):
        assert '<nav' in html
        assert 'showHome()' in html
        assert 'showAgents()' in html
        assert 'showTemplates()' in html

    def test_sidebar_has_rooms_list(self, html):
        assert 'id="rooms-list"' in html

    def test_sidebar_has_user_profile(self, html):
        assert 'Pro Plan' in html

    def test_input_has_attachment_button(self, html):
        assert 'fa-paperclip' in html

    def test_input_has_send_button(self, html):
        assert 'fa-paper-plane' in html
        assert 'sendMessage()' in html

    def test_input_has_microphone_button(self, html):
        assert 'fa-microphone' in html


# ═══════════════════════════════════════════════
# 2. CSS / Tailwind Classes
# ═══════════════════════════════════════════════

class TestTailwindClasses:
    """Verify critical Tailwind utility classes are applied."""

    def test_flex_layout_classes(self, html):
        for cls in ["flex", "flex-1", "flex-col", "items-center", "justify-between"]:
            assert cls in html, f"Missing Tailwind class: {cls}"

    def test_spacing_classes(self, html):
        for cls in ["p-4", "px-4", "py-2", "gap-3", "space-y-1", "space-y-6"]:
            assert cls in html, f"Missing spacing class: {cls}"

    def test_border_classes(self, html):
        assert "border-b" in html
        assert "border-r" in html
        assert "border-dark-700" in html

    def test_rounded_classes(self, html):
        for cls in ["rounded-lg", "rounded-xl", "rounded-2xl", "rounded-full"]:
            assert cls in html, f"Missing rounded class: {cls}"

    def test_text_size_classes(self, html):
        for cls in ["text-xs", "text-sm", "text-base", "text-lg", "text-xl"]:
            assert cls in html, f"Missing text size class: {cls}"

    def test_font_weight_classes(self, html):
        for cls in ["font-medium", "font-semibold", "font-bold"]:
            assert cls in html, f"Missing font weight class: {cls}"

    def test_transition_classes(self, html):
        assert "transition-colors" in html
        assert "transition-all" in html

    def test_shadow_classes(self, html):
        assert "shadow-lg" in html or "shadow-md" in html

    def test_z_index_layers(self, html):
        assert "z-0" in html
        assert "z-10" in html
        assert "z-30" in html
        assert "z-50" in html

    def test_overflow_classes(self, html):
        assert "overflow-hidden" in html
        assert "overflow-y-auto" in html
        assert "overflow-x-auto" in html


# ═══════════════════════════════════════════════
# 3. Color Theme & Dark Mode
# ═══════════════════════════════════════════════

class TestColorTheme:
    """Catown uses a dark theme with custom color tokens."""

    def test_dark_background_body(self, html):
        assert "background-color: #0F1115" in html

    def test_dark_text_color_body(self, html):
        assert "color: #E5E7EB" in html

    def test_dark_color_tokens_in_config(self, html):
        """Tailwind config extends colors with dark-* tokens."""
        assert "dark-900" in html
        assert "dark-800" in html
        assert "dark-700" in html
        assert "dark-600" in html

    def test_accent_color_tokens(self, html):
        assert "accent-500" in html
        assert "accent-400" in html

    def test_glass_panel_style(self, html):
        """Glass panel uses backdrop-filter blur."""
        assert "backdrop-filter: blur" in html
        assert "rgba(22, 24, 29, 0.6)" in html

    def test_scrollbar_custom_style(self, html):
        """Custom scrollbar thumb color."""
        assert "::-webkit-scrollbar" in html
        assert "#2A2E35" in html  # scrollbar thumb

    def test_scrollbar_hover_accent(self, html):
        assert "#3B82F6" in html  # scrollbar hover → accent

    def test_agent_avatar_gradient(self, html):
        """Agent avatars use gradient backgrounds."""
        assert "from-accent-400 to-purple-500" in html

    def test_welcome_gradient(self, html):
        """Welcome hero uses multi-color gradient."""
        assert "from-purple-500 via-accent-500 to-emerald-500" in html

    def test_status_colors(self, html):
        """Agent status indicators use semantic colors."""
        # idle = gray
        assert "agent-status-idle" in html
        # thinking = yellow with pulse
        assert "agent-status-thinking" in html
        # executing = green
        assert "agent-status-executing" in html


# ═══════════════════════════════════════════════
# 4. Responsive Design
# ═══════════════════════════════════════════════

class TestResponsiveDesign:
    """Verify responsive breakpoint classes."""

    def test_viewport_meta_tag(self, html):
        assert 'name="viewport"' in html
        assert "width=device-width" in html
        assert "initial-scale=1.0" in html

    def test_sidebar_mobile_toggle(self, html):
        """Sidebar is hidden on mobile, toggleable."""
        assert "mobile-hidden" in html
        assert "toggleSidebar()" in html

    def test_md_breakpoint_classes(self, html):
        """md: prefixed classes for tablet+."""
        assert "md:" in html
        assert "md:relative" in html or "md:flex" in html

    def test_sm_breakpoint_classes(self, html):
        """sm: prefixed classes for small screens."""
        html_lower = html
        assert "sm:" in html_lower or "sm:inline" in html_lower

    def test_lg_breakpoint_for_side_panel(self, html):
        """Side panel hidden on small screens, shown on lg."""
        assert "lg:hidden" in html or "lg:flex" in html

    def test_sidebar_transform_transition(self, html):
        """Sidebar slides in/out with transform."""
        assert "translateX(-100%)" in html
        assert "duration-300" in html

    def test_media_query_for_sidebar(self, html):
        assert "@media (min-width: 768px)" in html

    def test_truncate_for_overflow(self, html):
        """Text truncation for long room names."""
        assert "truncate" in html

    def test_max_width_constraints(self, html):
        assert "max-w-" in html  # max-w-[80%], max-w-4xl, etc.


# ═══════════════════════════════════════════════
# 5. Agent Status Visual Indicators
# ═══════════════════════════════════════════════

class TestAgentStatusVisuals:
    """Agent status bar renders correct visual states."""

    def test_agent_chip_structure(self, html):
        """Agent chip contains avatar + status icon + label."""
        assert "agent-status-chip" in html
        assert "agent-avatar" in html
        assert "agent-status-icon" in html
        assert "agent-status-label" in html

    def test_idle_icon_is_moon(self, html):
        assert "fa-moon" in html
        assert "agent-status-idle" in html

    def test_thinking_spinner_class(self, html):
        """Thinking state uses spinner icon."""
        # In JS, setAgentStatus sets fa-spinner fa-spin
        assert "fa-spinner" in html
        assert "fa-spin" in html

    def test_executing_gear_class(self, html):
        """Executing state uses gear icon."""
        assert "fa-gear" in html

    def test_pulse_animation_for_thinking(self, html):
        """Thinking agent avatar pulses."""
        assert "animate-pulse" in html

    def test_status_ring_effect(self, html):
        """Active agent chip gets a ring highlight."""
        assert "ring-1" in html

    def test_agent_color_mapping(self, html):
        """getAgentColor hashes name to consistent color."""
        assert "getAgentColor" in html
        colors = ["purple", "emerald", "amber", "rose", "cyan", "indigo"]
        for c in colors:
            assert c in html, f"Agent color '{c}' missing from palette"

    def test_status_progress_bar(self, html):
        """System status shows active agents progress bar."""
        assert "agents-progress" in html
        assert "bg-blue-500" in html


# ═══════════════════════════════════════════════
# 6. Markdown Rendering Styles
# ═══════════════════════════════════════════════

class TestMarkdownStyles:
    """Verify CSS rules for rendered markdown content."""

    def test_markdown_content_class(self, html):
        assert ".markdown-content" in html

    def test_markdown_headings_styled(self, html):
        assert ".markdown-content h1" in html
        assert ".markdown-content h2" in html
        assert ".markdown-content h3" in html

    def test_markdown_code_inline(self, html):
        assert ".markdown-content code" in html
        assert "color: #60A5FA" in html  # accent-400

    def test_markdown_code_block(self, html):
        assert ".markdown-content pre" in html
        assert "background: #16181D" in html

    def test_markdown_blockquote(self, html):
        assert ".markdown-content blockquote" in html
        assert "border-left: 3px solid #3B82F6" in html

    def test_markdown_table_styled(self, html):
        assert ".markdown-content table" in html
        assert ".markdown-content th" in html
        assert ".markdown-content td" in html

    def test_markdown_links_styled(self, html):
        assert ".markdown-content a" in html

    def test_markdown_lists_styled(self, html):
        assert ".markdown-content ul" in html
        assert ".markdown-content ol" in html

    def test_markdown_hr_styled(self, html):
        assert ".markdown-content hr" in html

    def test_markdown_images_responsive(self, html):
        assert ".markdown-content img" in html
        assert "max-width: 100%" in html

    def test_markdown_render_function(self, html):
        """JS function renderMarkdown uses marked + highlight.js."""
        assert "renderMarkdown" in html
        assert "marked.parse" in html
        assert "hljs.highlight" in html

    def test_highlight_js_github_dark_theme(self, html):
        assert "github-dark.min.css" in html


# ═══════════════════════════════════════════════
# 7. Modal & Overlay Rendering
# ═══════════════════════════════════════════════

class TestModalVisuals:
    """Modals use consistent visual style."""

    def test_status_modal_structure(self, html):
        """Status modal is dynamically created with correct classes."""
        assert "status-modal" in html
        assert "fixed inset-0 z-50" in html

    def test_modal_backdrop_blur(self, html):
        assert "bg-black/60" in html
        assert "backdrop-blur-sm" in html

    def test_modal_container_style(self, html):
        assert "bg-dark-800" in html
        assert "rounded-2xl" in html
        assert "border-dark-600" in html
        assert "shadow-2xl" in html

    def test_modal_has_close_button(self, html):
        assert "fa-xmark" in html

    def test_modal_tabs_rendered(self, html):
        """Status modal has tab navigation."""
        for tab in ["overview", "agents", "projects", "collab", "pipeline", "config"]:
            assert f"status-tab-{tab}" in html, f"Missing modal tab: {tab}"

    def test_memory_modal_structure(self, html):
        assert "memory-modal" in html
        assert "fa-brain" in html

    def test_create_project_modal(self, html):
        assert "create-project-modal" in html
        assert "showCreateProjectModal()" in html

    def test_pipeline_modal_dynamically_created(self, html):
        """Pipeline modals are created via JS createElement."""
        assert "pipeline-modal" in html


# ═══════════════════════════════════════════════
# 8. Toast Notification Styles
# ═══════════════════════════════════════════════

class TestToastVisuals:
    """Toast notifications have correct visual behavior."""

    def test_showToast_function_exists(self, html):
        assert "showToast" in html

    def test_toast_position_fixed_top_right(self, html):
        """Toast is positioned fixed top-right."""
        assert "fixed top-4 right-4 z-50" in html

    def test_toast_type_colors(self, html):
        """Each toast type has a distinct color."""
        assert "bg-emerald-600" in html  # success
        assert "bg-red-600" in html      # error
        assert "bg-blue-600" in html     # info
        assert "bg-amber-600" in html    # warning

    def test_toast_icons(self, html):
        assert "fa-check-circle" in html   # success
        assert "fa-times-circle" in html   # error
        assert "fa-info-circle" in html    # info
        assert "fa-exclamation-circle" in html  # warning

    def test_toast_animation(self, html):
        """Toast slides in from top with opacity transition."""
        assert "opacity-0" in html
        assert "translate-y-[-10px]" in html
        assert "duration-300" in html


# ═══════════════════════════════════════════════
# 9. Animation & Transition Classes
# ═══════════════════════════════════════════════

class TestAnimations:
    """CSS animations and transitions for interactive elements."""

    def test_pulse_keyframes(self, html):
        assert "@keyframes pulse" in html
        assert "opacity: 1" in html
        assert "opacity: 0.5" in html

    def test_typing_indicator_keyframes(self, html):
        assert "@keyframes typing" in html
        assert "translateY(-4px)" in html

    def test_typing_indicator_spans(self, html):
        """Typing animation staggered across 3 spans."""
        assert "typing-indicator" in html
        assert "animation-delay: 0.2s" in html
        assert "animation-delay: 0.4s" in html

    def test_sidebar_slide_transition(self, html):
        assert "transform: translateX" in html
        assert "transition-transform" in html
        assert "duration-300" in html

    def test_focus_ring_transition(self, html):
        assert "focus-within:border-accent-500" in html
        assert "focus-within:ring-1" in html

    def test_hover_color_transitions(self, html):
        assert "hover:bg-dark-700" in html
        assert "hover:text-white" in html

    def test_agent_avatar_pulse(self, html):
        """Agent thinking state adds animate-pulse to avatar."""
        assert "animate-pulse" in html

    def test_progress_bar_transition(self, html):
        """Pipeline progress bar animates width changes."""
        assert "transition-all" in html
        assert "duration-500" in html


# ═══════════════════════════════════════════════
# 10. Scrollbar & Overflow Styles
# ═══════════════════════════════════════════════

class TestScrollbarStyles:
    """Custom scrollbar styling for the dark theme."""

    def test_webkit_scrollbar_width(self, html):
        assert "::-webkit-scrollbar { width: 6px" in html

    def test_webkit_scrollbar_track(self, html):
        assert "::-webkit-scrollbar-track" in html
        assert "background: transparent" in html

    def test_webkit_scrollbar_thumb(self, html):
        assert "::-webkit-scrollbar-thumb" in html
        assert "border-radius: 3px" in html

    def test_scrollbar_thumb_hover(self, html):
        assert "::-webkit-scrollbar-thumb:hover" in html

    def test_no_scrollbar_class(self, html):
        """Some containers hide scrollbar entirely."""
        assert "no-scrollbar" in html
        assert "-ms-overflow-style: none" in html
        assert "scrollbar-width: none" in html

    def test_dropdown_scrollbar(self, html):
        """Mention dropdown has its own thin scrollbar."""
        assert ".agent-mention-dropdown::-webkit-scrollbar" in html


# ═══════════════════════════════════════════════
# 11. Icon & Font Assets
# ═══════════════════════════════════════════════

class TestIconFonts:
    """Verify external icon/font resources are referenced."""

    def test_font_awesome_loaded(self, html):
        assert "font-awesome" in html or "fontawesome" in html

    def test_font_awesome_icons_used(self, html):
        """Verify a range of FA icons used across the UI."""
        icons = [
            "fa-house", "fa-compass", "fa-book",
            "fa-search", "fa-plus", "fa-paper-plane",
            "fa-paperclip", "fa-microphone", "fa-bars",
            "fa-xmark", "fa-terminal", "fa-sliders",
            "fa-layer-group", "fa-robot", "fa-chevron-up",
            "fa-circle", "fa-brain", "fa-trash",
            "fa-folder", "fa-folder-open", "fa-door-open",
            "fa-users", "fa-hashtag", "fa-at",
            "fa-moon", "fa-spinner", "fa-gear",
            "fa-play", "fa-pause", "fa-check",
            "fa-rotate-left", "fa-diagram-project",
            "fa-handshake", "fa-comments", "fa-file-code",
            "fa-arrow-left", "fa-refresh", "fa-plug",
            "fa-save", "fa-eraser", "fa-globe",
            "fa-users-gear", "fa-bolt", "fa-database",
            "fa-chart-pie", "fa-info-circle",
            "fa-check-circle", "fa-times-circle",
            "fa-exclamation-circle", "fa-magnifying-glass",
            "fa-broadcast-tower", "fa-tasks", "fa-code",
            "fa-envelope", "fa-chevron-up",
        ]
        for icon in icons:
            assert icon in html, f"Missing FA icon: {icon}"

    def test_google_fonts_inter(self, html):
        assert "Inter" in html

    def test_google_fonts_jetbrains_mono(self, html):
        assert "JetBrains Mono" in html

    def test_tailwind_cdn_loaded(self, html):
        assert "tailwindcss" in html

    def test_marked_js_loaded(self, html):
        assert "marked/marked.min.js" in html

    def test_highlight_js_loaded(self, html):
        assert "highlight.min.js" in html


# ═══════════════════════════════════════════════
# 12. Pipeline Dashboard Visual Elements
# ═══════════════════════════════════════════════

class TestPipelineVisuals:
    """Pipeline tab renders visual elements correctly."""

    def test_pipeline_stage_icons(self, html):
        """Stage status uses emoji indicators."""
        assert "⏳" in html  # pending
        assert "🔄" in html  # running
        assert "🚧" in html  # blocked
        assert "✅" in html  # completed
        assert "❌" in html  # failed
        assert "⏪" in html  # rejected

    def test_pipeline_status_colors(self, html):
        """Pipeline status uses semantic colors."""
        assert "text-emerald-400" in html  # running
        assert "text-yellow-400" in html   # paused
        assert "text-blue-400" in html     # completed
        assert "text-red-400" in html      # failed

    def test_pipeline_progress_bar(self, html):
        """Progress bar renders with conditional color."""
        assert "bg-accent-500" in html     # default
        assert "bg-emerald-500" in html    # completed
        assert "bg-red-500" in html        # failed

    def test_pipeline_stage_card_style(self, html):
        """Stage cards have rounded + border + padding."""
        assert "rounded-xl" in html
        assert "border-dark-600" in html
        assert "p-4" in html

    def test_pipeline_active_stage_ring(self, html):
        """Active stage card gets accent ring."""
        assert "ring-accent-500/30" in html

    def test_pipeline_action_buttons(self, html):
        """Action buttons have correct visual styles."""
        assert "bg-emerald-600" in html   # start/resume/approve
        assert "bg-yellow-600" in html    # pause
        assert "bg-red-600" in html       # reject

    def test_pipeline_file_icons(self, html):
        """Artifacts use file/folder emoji."""
        assert "📁" in html
        assert "📄" in html

    def test_pipeline_message_type_colors(self, html):
        """Agent messages colored by type."""
        assert "text-blue-300" in html     # AGENT_OUTPUT
        assert "text-emerald-300" in html  # STAGE_OUTPUT
        assert "text-yellow-300" in html   # AGENT_QUESTION
        assert "text-purple-300" in html   # AGENT_REPLY
        assert "text-orange-300" in html   # HUMAN_INSTRUCT


# ═══════════════════════════════════════════════
# 13. Input & Interaction Visual Feedback
# ═══════════════════════════════════════════════

class TestInputVisualFeedback:
    """Input area provides correct visual cues."""

    def test_input_wrapper_focus_ring(self, html):
        assert "focus-within:border-accent-500" in html
        assert "focus-within:ring-accent-500" in html

    def test_input_placeholder_text(self, html):
        assert "Message room or @agent" in html

    def test_command_hints_visible(self, html):
        """Command hint buttons are styled as pills."""
        assert 'Mention Agent' in html
        assert 'Create Room' in html
        assert 'Run Script' in html

    def test_command_hint_style(self, html):
        assert "bg-dark-700" in html
        assert "border border-dark-600" in html

    def test_mention_dropdown_style(self, html):
        """@mention dropdown has correct visual style."""
        assert "agent-mention-dropdown" in html
        assert "background: #16181D" in html
        assert "box-shadow: 0 -4px 12px" in html

    def test_mention_item_hover_style(self, html):
        assert ".agent-mention-item:hover" in html

    def test_mention_item_selected_style(self, html):
        assert ".agent-mention-item.selected" in html

    def test_send_button_accent_color(self, html):
        assert "bg-accent-500" in html
        assert "hover:bg-accent-400" in html

    def test_search_input_style(self, html):
        """Sidebar search input styled for dark theme."""
        assert 'bg-dark-900' in html
        assert 'placeholder-gray-500' in html


# ═══════════════════════════════════════════════
# 14. Sidebar & Navigation Visual State
# ═══════════════════════════════════════════════

class TestSidebarVisuals:
    """Sidebar rendering and active states."""

    def test_sidebar_width(self, html):
        assert "w-64" in html

    def test_sidebar_border(self, html):
        assert "border-r border-dark-700" in html

    def test_sidebar_bg(self, html):
        assert "bg-dark-800" in html

    def test_active_room_indicator(self, html):
        """Active room shows green dot."""
        assert "bg-green-500" in html

    def test_inactive_room_indicator(self, html):
        """Inactive room shows gray dot."""
        assert "bg-gray-500" in html

    def test_active_room_bg_highlight(self, html):
        assert "bg-dark-700 text-gray-300" in html

    def test_agent_count_badge(self, html):
        """Room items show agent count badge."""
        assert "AI" in html  # "N AI" badge

    def test_user_avatar_gradient(self, html):
        assert "from-accent-400 to-purple-500" in html

    def test_logo_gradient(self, html):
        assert "from-accent-400 to-accent-500" in html


# ═══════════════════════════════════════════════
# 15. Message Bubble Visual Styles
# ═══════════════════════════════════════════════

class TestMessageBubbleStyles:
    """Message rendering uses correct visual styles (from JS)."""

    def test_user_message_align_right(self, html):
        """User messages are right-aligned in JS renderMessages."""
        assert "justify-end" in html

    def test_user_message_bg(self, html):
        """User message bubble uses accent color."""
        assert "bg-accent-600" in html

    def test_agent_message_bg(self, html):
        """Agent message bubble uses dark background."""
        assert "bg-dark-700/80" in html

    def test_agent_message_border(self, html):
        assert "border-dark-600" in html

    def test_agent_name_colored(self, html):
        """Agent name in message uses agent-specific color."""
        assert "text-${color}-400" in html or "text-" in html

    def test_message_timestamp(self, html):
        assert "formatTime" in html
        assert "Just now" in html
        assert "mins ago" in html

    def test_message_max_width(self, html):
        assert "max-w-[85%]" in html or "max-w-[80%]" in html

    def test_user_bubble_rounded_tr(self, html):
        """User bubble has rounded top-right corner small."""
        assert "rounded-tr-sm" in html

    def test_agent_bubble_rounded_tl(self, html):
        """Agent bubble has rounded top-left corner small."""
        assert "rounded-tl-sm" in html


# ═══════════════════════════════════════════════
# 16. Config Tab Visual Styles
# ═══════════════════════════════════════════════

class TestConfigTabVisuals:
    """Config tab renders LLM settings with correct visuals."""

    def test_config_input_style(self, html):
        """Config inputs use dark background."""
        assert "bg-dark-900" in html
        assert "border-dark-600" in html
        assert "focus:border-accent-500" in html

    def test_config_agent_own_indicator(self, html):
        """Agents with own config get accent border."""
        assert "border-accent-500/40" in html

    def test_config_agent_global_indicator(self, html):
        """Agents using global config show globe icon."""
        assert "fa-globe" in html

    def test_config_save_button(self, html):
        assert "bg-accent-500" in html
        assert "fa-save" in html

    def test_config_test_button(self, html):
        assert "fa-plug" in html

    def test_config_sync_checkbox(self, html):
        assert "global-sync-agents" in html
        assert "accent-accent-500" in html

    def test_effective_config_source_badge(self, html):
        """Effective config shows source: agent vs global."""
        assert "source === 'agent'" in html or "'agent'" in html
        assert "'global'" in html


# ═══════════════════════════════════════════════
# 17. System Status Panel Visuals
# ═══════════════════════════════════════════════

class TestSystemStatusVisuals:
    """System status panel in the side panel."""

    def test_system_status_section(self, html):
        assert "System Status" in html

    def test_active_agents_progress_bar(self, html):
        """Progress bar for active agent ratio."""
        assert "h-1.5 w-full bg-dark-700 rounded-full" in html
        assert "bg-blue-500" in html

    def test_backend_status_indicator(self, html):
        assert "fa-circle text-[8px]" in html

    def test_connected_state_green(self, html):
        assert "text-green-400" in html

    def test_disconnected_state_red(self, html):
        assert "text-red-400" in html


# ═══════════════════════════════════════════════
# 18. Welcome / Empty State Visuals
# ═══════════════════════════════════════════════

class TestWelcomeStateVisuals:
    """Welcome message and empty states have proper visuals."""

    def test_welcome_avatar_ring(self, html):
        """Welcome avatar has gradient ring."""
        assert "bg-gradient-to-tr from-purple-500 via-accent-500 to-emerald-500" in html

    def test_welcome_shadow(self, html):
        assert "shadow-lg shadow-accent-500/20" in html

    def test_welcome_title(self, html):
        assert "Welcome to Catown" in html

    def test_welcome_subtitle(self, html):
        assert "Multi-Agent Collaboration Platform" in html

    def test_no_rooms_empty_state(self, html):
        """When no rooms exist, shows empty state text."""
        assert "No rooms yet" in html


# ═══════════════════════════════════════════════
# 19. Overview Tab Visual Styles
# ═══════════════════════════════════════════════

class TestOverviewTabVisuals:
    """Overview tab in status modal has correct card layouts."""

    def test_stat_card_style(self, html):
        """Overview stat cards."""
        assert "bg-dark-700 rounded-xl p-4 border border-dark-600" in html

    def test_stat_card_number_colors(self, html):
        """Each stat card number has a unique color."""
        assert "text-accent-400" in html   # total agents
        assert "text-emerald-400" in html  # active agents
        assert "text-purple-400" in html   # projects
        assert "text-amber-400" in html    # messages

    def test_agent_card_in_overview(self, html):
        """Agent cards in overview have avatar + info."""
        assert "w-8 h-8 rounded-full" in html

    def test_project_card_open_button(self, html):
        assert "bg-accent-500/20" in html
        assert "text-accent-400" in html


# ═══════════════════════════════════════════════
# 20. Dynamic Rendering Correctness (API → DOM)
# ═══════════════════════════════════════════════

class TestDynamicRendering:
    """Verify API data flows correctly into rendered DOM elements."""

    def test_agents_render_in_status_bar(self, client, sample_project):
        """After project creation, agents should be loadable."""
        agents = client.get("/api/agents").json()
        assert len(agents) >= 5
        names = {a["name"] for a in agents}
        for role in ["analyst", "architect", "developer", "tester", "release"]:
            assert role in names

    def test_rooms_render_with_agent_count(self, client, sample_project):
        """Rooms sidebar shows agent count badge."""
        projects = client.get("/api/projects").json()
        assert len(projects) >= 1
        for p in projects:
            assert "id" in p
            assert "name" in p

    def test_messages_render_with_agent_name(self, client, sample_project):
        """Messages include agent attribution."""
        cid = sample_project["chatroom_id"]
        client.post(f"/api/chatrooms/{cid}/messages", json={
            "content": "rendering test",
            "message_type": "user"
        })
        msgs = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(m["content"] == "rendering test" for m in msgs)

    def test_pipeline_stages_render_in_order(self, client):
        """Pipeline detail returns stages in correct order."""
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

    def test_config_renders_global_and_agent(self, client):
        """Config endpoint returns both global and per-agent configs."""
        data = client.get("/api/config").json()
        assert "global_llm" in data
        assert "agent_llm_configs" in data
        agent_configs = data["agent_llm_configs"]
        assert len(agent_configs) >= 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
