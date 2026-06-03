# -*- coding: utf-8 -*-
"""Tests for ADR-035 tool capability profile tuning."""

from services.tool_capability_profiles import select_tools_for_capability_profile


def test_interactive_chat_keeps_collaboration_listing_tools_conditional():
    tool_names = [
        "read_file",
        "delegate_task",
        "check_task_status",
        "list_agents",
        "list_collaborators",
    ]

    ordinary_selection = select_tools_for_capability_profile(
        tool_names,
        profile_name="interactive_chat",
        user_message="Help me summarize this project.",
        mode="chat_turn",
    )

    assert ordinary_selection.active_tools == [
        "read_file",
        "delegate_task",
        "check_task_status",
    ]
    assert ordinary_selection.excluded_tools == ["list_agents", "list_collaborators"]
    assert ordinary_selection.activated_groups == []

    collaboration_selection = select_tools_for_capability_profile(
        tool_names,
        profile_name="interactive_chat",
        user_message="Which agents or collaborators can help with this handoff?",
        mode="chat_turn",
    )

    assert collaboration_selection.active_tools == tool_names
    assert collaboration_selection.excluded_tools == []
    assert collaboration_selection.activated_groups == ["collaboration"]
