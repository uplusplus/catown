import os
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.chat_prompt_builder import (  # noqa: E402
    build_chat_context_selector,
    list_selector_profiles,
    selector_profile_config,
    team_member_lines,
)
from services.context_builder import (  # noqa: E402
    ContextFragment,
    ContextScope,
    ContextSelector,
    ContextVisibility,
    assemble_messages,
    build_base_system_prompt,
    build_boss_instruction_context,
    build_history_summary_fragment,
    build_operating_developer_context,
    build_recent_history,
    build_runtime_user_context,
    build_runtime_user_fragments,
    build_stage_developer_context,
    build_turn_state_developer_fragments,
    build_turn_state_user_fragments,
)
from services.turn_state import TurnContextState, build_tool_result_record, build_turn_state_from_checkpoint_snapshot  # noqa: E402
from services.task_state import build_task_state, build_task_state_fragments  # noqa: E402


def test_base_system_prompt_only_contains_stable_agent_identity():
    agent_config = {
        "name": "Builder",
        "soul": {
            "identity": "Builds practical software.",
            "values": ["Keep changes small"],
            "style": "Direct and calm",
        },
        "role": {
            "responsibilities": ["Implement features"],
            "rules": ["Run focused tests"],
        },
    }

    system_prompt = build_base_system_prompt(agent_config)

    assert "Builder" in system_prompt
    assert "Builds practical software" in system_prompt
    assert "Keep changes small" in system_prompt
    assert "Run focused tests" in system_prompt
    assert "Stage Instructions" not in system_prompt
    assert "BOSS Instructions" not in system_prompt
    assert "Inter-Agent Messages" not in system_prompt


def test_base_system_prompt_uses_structured_db_agent_config_not_legacy_prompt():
    db_agent = SimpleNamespace(
        name="Developer",
        role="legacy role string",
        soul='{"identity": "Legacy soul field"}',
        config=json.dumps(
            {
                "soul": {
                    "identity": "Structured identity",
                    "values": ["Structured value"],
                    "style": "Structured style",
                },
                "role": {
                    "title": "Structured role",
                    "responsibilities": ["Own implementation"],
                    "rules": ["Keep runtime context out of system"],
                },
            },
            ensure_ascii=False,
        ),
        system_prompt="OLD MONOLITHIC PROMPT",
    )

    system_prompt = build_base_system_prompt(db_agent)

    assert "Structured identity" in system_prompt
    assert "Structured value" in system_prompt
    assert "Own implementation" in system_prompt
    assert "Keep runtime context out of system" in system_prompt
    assert "OLD MONOLITHIC PROMPT" not in system_prompt
    assert "Legacy soul field" not in system_prompt


def test_team_member_lines_include_runtime_contract():
    agent = SimpleNamespace(
        name="Valet",
        agent_type="valet",
        role="assistant",
        config=json.dumps(
            {
                "metadata": {
                    "runtime_contract": {
                        "mode": "coordinator",
                        "owns": ["coordination"],
                        "must_dispatch_specialized_work": True,
                        "dispatch_tools": ["delegate_task"],
                        "status_tools": ["check_task_status"],
                        "completion_rule": "Wait for owner result.",
                    }
                }
            },
            ensure_ascii=False,
        ),
    )

    lines = team_member_lines([agent])

    assert len(lines) == 1
    assert "runtime contract" in lines[0]
    assert "mode=coordinator" in lines[0]
    assert "must_dispatch_specialized_work=true" in lines[0]
    assert "dispatch_tools=delegate_task" in lines[0]


def test_assembly_orders_context_layers_before_history_and_current_input():
    stage = SimpleNamespace(
        name="implementation",
        display_name="Implementation",
        context_prompt="Make the requested code change.",
        expected_artifacts=["tests"],
        active_skills=["code"],
    )
    stage_context = build_stage_developer_context(stage_cfg=stage, tools=["read_file"])
    boss_context = build_boss_instruction_context(["Prefer the smaller patch."])
    user_context = build_runtime_user_context(
        runtime_context="Project status: active",
        inter_agent_messages=[{"from_agent": "Planner", "content": "Use the existing service."}],
    )

    messages = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[stage_context, build_operating_developer_context(agent_name="Builder"), boss_context],
        user_fragments=[user_context],
        history_messages=[{"role": "assistant", "content": "Older reply"}],
        current_input_messages=[{"role": "user", "content": "Current request"}],
    ).to_messages()

    assert [message["role"] for message in messages] == [
        "system",
        "developer",
        "developer",
        "developer",
        "user",
        "assistant",
        "user",
    ]
    assert "Operating Contract" in messages[1]["content"]
    assert "Stage Instructions" in messages[2]["content"]
    assert "BOSS Instructions" in messages[3]["content"]
    assert "Inter-Agent Messages" in messages[4]["content"]


def test_skill_hint_is_always_injected_and_guide_requires_active_skill():
    skills_config = {
        "code": {"levels": {"hint": "Code hint", "guide": "Code guide"}},
        "debug": {"levels": {"hint": "Debug hint", "guide": "Debug guide"}},
    }

    fragment = build_stage_developer_context(
        active_skills=["code"],
        skills_config=skills_config,
        agent_skills=["code", "debug"],
    )

    assert fragment is not None
    assert "Code hint" in fragment.content
    assert "Debug hint" in fragment.content
    assert "Code guide" in fragment.content
    assert "Debug guide" not in fragment.content


def test_stage_developer_context_includes_output_header_contract():
    fragment = build_stage_developer_context(tools=["write_file"])

    assert fragment is not None
    assert "## Output Header Contract" in fragment.content
    assert "Purpose" in fragment.content
    assert "Overview" in fragment.content
    assert "Author" in fragment.content
    assert "Created At" in fragment.content
    assert "Modification Log" in fragment.content


def test_boss_and_inter_agent_context_land_in_separate_roles():
    boss_context = build_boss_instruction_context(["Ship the API path first."])
    user_context = build_runtime_user_context(
        inter_agent_messages=[{"from_agent": "Researcher", "content": "The provider supports tools."}]
    )

    messages = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[boss_context],
        user_fragments=[user_context],
    ).to_messages()

    assert messages[1]["role"] == "developer"
    assert "Ship the API path first" in messages[1]["content"]
    assert messages[2]["role"] == "user"
    assert "The provider supports tools" in messages[2]["content"]


def test_empty_context_still_produces_usable_messages():
    messages = assemble_messages(
        base_system_prompt="",
        developer_fragments=[build_stage_developer_context()],
        user_fragments=[build_runtime_user_context()],
        history_messages=[],
        current_input_messages=[{"role": "user", "content": "Hello"}],
    ).to_messages()

    assert messages == [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]


def test_developer_role_can_fallback_into_system_message():
    stage_context = build_stage_developer_context(tools=["search"])

    messages = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[stage_context],
        developer_role_supported=False,
    ).to_messages()

    assert [message["role"] for message in messages] == ["system"]
    assert "Developer Context" in messages[0]["content"]
    assert "search" in messages[0]["content"]


def test_recent_history_preserves_existing_window_semantics():
    recent_messages = [
        SimpleNamespace(agent_name=None, message_type="user", content="old user"),
        SimpleNamespace(agent_name="Builder", message_type="text", content="builder reply"),
        SimpleNamespace(agent_name="Reviewer", message_type="text", content="reviewer reply"),
    ]

    all_history = build_recent_history(recent_messages, limit=3, prefix_assistant_name=True)
    target_history = build_recent_history(
        recent_messages,
        limit=3,
        visibility="target",
        target_agent_name="Builder",
    )

    assert all_history == [
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "[Builder]: builder reply"},
        {"role": "assistant", "content": "[Reviewer]: reviewer reply"},
    ]
    assert target_history == [
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "builder reply"},
    ]


def test_recent_history_does_not_invent_tool_messages_without_saved_tool_results():
    recent_messages = [
        SimpleNamespace(agent_name=None, message_type="user", content="delete the file"),
        SimpleNamespace(agent_name="analyst", message_type="text", content="Delete approved and completed."),
    ]

    history = build_recent_history(recent_messages, limit=4)

    assert history == [
        {"role": "user", "content": "delete the file"},
        {"role": "assistant", "content": "Delete approved and completed."},
    ]


def test_turn_state_checkpoint_protocol_tail_restores_tool_pair_without_history_tool_result():
    checkpoint_snapshot = {
        "turn_local_state": {
            "protocol_tail_messages": [
                {
                    "role": "assistant",
                    "content": "Delete the dangerous file next.",
                    "tool_calls": [
                        {
                            "id": "call_delete_file_replay",
                            "type": "function",
                            "function": {
                                "name": "delete_file",
                                "arguments": "{\"file_path\": \"danger.txt\"}",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_delete_file_replay",
                    "name": "delete_file",
                    "content": "Deleted danger.txt",
                },
            ],
            "prior_round_summaries": [],
        }
    }

    turn_state = build_turn_state_from_checkpoint_snapshot(checkpoint_snapshot)

    assert turn_state.protocol_messages() == [
        {
            "role": "assistant",
            "content": "Delete the dangerous file next.",
            "tool_calls": [
                {
                    "id": "call_delete_file_replay",
                    "type": "function",
                    "function": {
                        "name": "delete_file",
                        "arguments": "{\"file_path\": \"danger.txt\"}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_delete_file_replay",
            "name": "delete_file",
            "content": "Deleted danger.txt",
        },
    ]


def test_history_summary_fragment_compacts_only_older_messages():
    recent_messages = [
        SimpleNamespace(agent_name=None, message_type="user", content="first user"),
        SimpleNamespace(agent_name="Planner", message_type="text", content="plan reply"),
        SimpleNamespace(agent_name=None, message_type="user", content="second user"),
        SimpleNamespace(agent_name="Builder", message_type="text", content="recent builder reply"),
    ]

    fragment = build_history_summary_fragment(
        recent_messages,
        keep_last=2,
        prefix_assistant_name=True,
    )
    kept_history = build_recent_history(
        recent_messages,
        limit=2,
        prefix_assistant_name=True,
    )

    assert fragment is not None
    assert fragment.source == "history_summary"
    assert "first user" in fragment.content
    assert "[Planner]: plan reply" in fragment.content
    assert "recent builder reply" not in fragment.content
    assert kept_history == [
        {"role": "user", "content": "second user"},
        {"role": "assistant", "content": "[Builder]: recent builder reply"},
    ]


def test_runtime_user_fragments_are_structured_and_prioritized():
    fragments = build_runtime_user_fragments(
        runtime_context="Project status: active",
        project=SimpleNamespace(
            id=7,
            name="Catown",
            status="active",
            current_focus="Unify chat prompt assembly",
            blocking_reason="Need to keep old fallback paths aligned",
            latest_summary="Shared builder exists but task memory is still shallow.",
        ),
        chatroom=SimpleNamespace(id=11, title="Main Chat", session_type="project-bound"),
        team_members=["- **Builder** (role: implementation)"],
        memories=["- [2026-04-24 10:00] [importance=9] Keep the builder path unified."],
        inter_agent_messages=[{"from_agent": "Planner", "content": "Use the shared context builder."}],
        extra_context="Previous agent summary",
    )

    assert [fragment.source for fragment in fragments] == [
        "project_chat_overview",
        "runtime_context",
        "team_members",
        "inter_agent_messages",
        "memory_context",
        "previous_agent_work",
    ]
    assert fragments[0].visibility == "global"
    assert "## Chat Routing" in fragments[0].content
    assert "`@agent_name`" in fragments[0].content
    assert "last non-empty paragraph" in fragments[0].content
    assert "## Chat Lineage" in fragments[0].content
    assert fragments[3].scope == "shared_fact"
    assert "Current focus" not in fragments[0].content
    assert "Blocking reason" not in fragments[0].content
    assert "Latest summary" not in fragments[0].content


def test_operating_contract_requires_handoff_mentions_at_start_of_final_message():
    fragment = build_operating_developer_context(agent_name="Valet")

    assert "last non-empty paragraph" in fragment.content
    assert "Mentions outside that final paragraph" in fragment.content


def test_runtime_user_fragments_can_split_standalone_note_and_source_chat():
    fragments = build_runtime_user_fragments(
        chatroom=SimpleNamespace(
            id=11,
            title="Sub Chat",
            session_type="project-bound",
            source_chatroom_id=9,
            is_visible_in_chat_list=False,
        ),
        project=SimpleNamespace(id=7, name="Catown", default_chatroom_id=9),
        source_chatroom=SimpleNamespace(id=9, title="Main Chat"),
        standalone_note="Reply directly and stay concise.",
    )

    assert [fragment.source for fragment in fragments] == [
        "standalone_note",
        "project_chat_overview",
    ]
    assert fragments[0].content.startswith("## Session Instructions")
    assert "Source chat: #9 Main Chat" in fragments[-1].content


def test_context_selector_can_limit_runtime_fragments():
    selector = ContextSelector(max_fragments=2)
    fragments = build_runtime_user_fragments(
        runtime_context="Runtime",
        project=SimpleNamespace(id=1, name="Catown"),
        chatroom=SimpleNamespace(id=2, title="Chat"),
    )

    selected = selector.select_fragments(fragments, role="user")
    messages = assemble_messages(
        base_system_prompt="base identity",
        user_fragments=fragments,
        selector=selector,
    ).to_messages()

    assert [fragment.source for fragment in selected] == [
        "project_chat_overview",
        "runtime_context",
    ]
    assert messages == [
        {"role": "system", "content": "base identity"},
        {
            "role": "user",
            "content": (
                "## Current Project\n"
                "- Project ID: 1\n"
                "- Name: Catown\n\n"
                "## Current Chat\n"
                "- Chat ID: 2\n"
                "- Title: Chat\n\n"
                "## Chat Routing\n"
                "- Messages are shared conversation events.\n"
                "- Agent-to-agent: start the last non-empty paragraph with `@agent_name` mentions to trigger routing.\n"
                "- Use chat mentions for notifications/handoffs; use tracked task tools for work that needs tracking.\n\n"
                "## Chat Lineage\n"
                "- Chat role: project-linked chat"
            ),
        },
        {"role": "user", "content": "Runtime"},
    ]


def test_task_state_fragments_capture_request_goal_and_validation():
    task_state = build_task_state(
        project=SimpleNamespace(
            current_focus="Refactor chat/query context assembly",
            blocking_reason="Do not touch pipeline paths yet",
            latest_summary="Shared chat builder is already in place.",
        ),
        user_message="Continue phase 2 on chat and query paths only.",
    )

    fragments = build_task_state_fragments(task_state)

    assert [fragment.source for fragment in fragments] == [
        "task_state",
        "task_validation",
    ]
    assert "Current request" in fragments[0].content
    assert "Active goal" in fragments[0].content
    assert "Current blocker" in fragments[0].content
    assert "Working summary" in fragments[0].content
    assert "advances the current request" in fragments[1].content
    assert "Do not claim the blocker is resolved" in fragments[1].content


def test_task_state_fragments_out_rank_generic_project_status_under_budget():
    task_fragments = build_task_state_fragments(
        build_task_state(
            project=SimpleNamespace(
                current_focus="Refactor chat/query context assembly",
                blocking_reason="Do not touch pipeline paths yet",
                latest_summary="Shared chat builder is already in place.",
            ),
            user_message="Continue phase 2 on chat and query paths only.",
        )
    )
    runtime_fragments = build_runtime_user_fragments(
        project=SimpleNamespace(
            id=7,
            name="Catown",
            status="active",
            current_stage="delivery",
            execution_mode="autopilot",
            health_status="healthy",
        ),
        chatroom=SimpleNamespace(id=11, title="Main Chat", session_type="project-bound"),
    )

    selector = ContextSelector(max_fragments=2)
    selected = selector.select_fragments([*task_fragments, *runtime_fragments], role="user")

    assert [fragment.source for fragment in selected] == [
        "task_state",
        "task_validation",
    ]


def test_context_selector_can_filter_by_scope_and_visibility():
    fragments = [
        ContextFragment(
            role="user",
            content="global run",
            scope=ContextScope.RUN,
            visibility=ContextVisibility.GLOBAL,
            source="run_global",
            priority=10,
        ),
        ContextFragment(
            role="user",
            content="private note",
            scope=ContextScope.AGENT_PRIVATE,
            visibility=ContextVisibility.PRIVATE,
            source="private_note",
            priority=5,
        ),
        ContextFragment(
            role="user",
            content="turn handoff",
            scope=ContextScope.TURN,
            visibility=ContextVisibility.AGENT,
            source="turn_handoff",
            priority=20,
        ),
    ]

    selector = ContextSelector(
        allowed_visibilities=frozenset({ContextVisibility.GLOBAL, ContextVisibility.AGENT}),
        allowed_scopes=frozenset({ContextScope.RUN, ContextScope.TURN}),
    )

    selected = selector.select_fragments(fragments, role="user")

    assert [fragment.source for fragment in selected] == [
        "run_global",
        "turn_handoff",
    ]


def test_context_selector_enforces_total_token_budget_across_roles():
    developer_fragment = ContextFragment(
        role="developer",
        content="Developer rules\n" + ("A" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="developer_rules",
        priority=10,
    )
    user_fragment = ContextFragment(
        role="user",
        content="Project state\n" + ("B" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.GLOBAL,
        source="project_state",
        priority=20,
    )

    selector = ContextSelector(max_tokens=160)
    messages = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[developer_fragment],
        user_fragments=[user_fragment],
        selector=selector,
    ).to_messages()

    assert [message["role"] for message in messages] == ["system", "developer", "user"]
    assert "[truncated for token budget]" not in messages[1]["content"]
    assert "[truncated for token budget]" in messages[2]["content"]


def test_context_selector_can_enforce_role_specific_token_budgets():
    developer_fragment = ContextFragment(
        role="developer",
        content="Developer rules\n" + ("A" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="developer_rules",
        priority=10,
    )
    user_fragment = ContextFragment(
        role="user",
        content="Project state\n" + ("B" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.GLOBAL,
        source="project_state",
        priority=20,
    )

    assembly = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[developer_fragment],
        user_fragments=[user_fragment],
        selector=ContextSelector(
            max_tokens=320,
            max_tokens_by_role={
                "developer": 220,
                "user": 96,
            },
        ),
    )

    messages = assembly.to_messages()
    diagnostics = assembly.selector_diagnostics

    assert [message["role"] for message in messages] == ["system", "developer", "user"]
    assert "[truncated for token budget]" not in messages[1]["content"]
    assert "[truncated for token budget]" in messages[2]["content"]
    assert diagnostics["selector"]["max_tokens"] == 320
    assert diagnostics["selector"]["max_tokens_by_role"] == {"developer": 220, "user": 96}
    assert diagnostics["developer"]["truncated_count"] == 0
    assert diagnostics["user"]["truncated_count"] == 1


def test_context_selector_reports_scope_usage_diagnostics():
    developer_fragment = ContextFragment(
        role="developer",
        content="Developer rules\n" + ("A" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="developer_rules",
        priority=10,
    )
    user_fragment = ContextFragment(
        role="user",
        content="Shared facts\n" + ("B" * 240),
        scope=ContextScope.SHARED_FACT,
        visibility=ContextVisibility.GLOBAL,
        source="shared_facts",
        priority=20,
    )

    assembly = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[developer_fragment],
        user_fragments=[user_fragment],
        selector=ContextSelector(
            max_tokens=260,
            max_tokens_by_scope={
                ContextScope.RUN: 180,
                ContextScope.SHARED_FACT: 96,
            },
        ),
    )

    diagnostics = assembly.selector_diagnostics
    by_scope = diagnostics["summary"]["by_scope"]

    assert diagnostics["selector"]["max_tokens_by_scope"] == {
        ContextScope.RUN: 180,
        ContextScope.SHARED_FACT: 96,
    }
    assert by_scope[ContextScope.RUN]["candidate_count"] == 1
    assert by_scope[ContextScope.RUN]["selected_count"] == 1
    assert by_scope[ContextScope.RUN]["candidate_tokens"] >= by_scope[ContextScope.RUN]["selected_tokens"] > 0
    assert by_scope[ContextScope.SHARED_FACT]["candidate_count"] == 1
    assert by_scope[ContextScope.SHARED_FACT]["selected_count"] == 1
    assert by_scope[ContextScope.SHARED_FACT]["candidate_tokens"] >= by_scope[ContextScope.SHARED_FACT]["selected_tokens"] > 0
    assert by_scope[ContextScope.SHARED_FACT]["selected_tokens"] <= 96


def test_assembly_exposes_selector_compaction_diagnostics():
    developer_fragment = ContextFragment(
        role="developer",
        content="Developer rules\n" + ("A" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="developer_rules",
        priority=10,
    )
    user_fragment = ContextFragment(
        role="user",
        content="Project state\n" + ("B" * 400),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.GLOBAL,
        source="project_state",
        priority=20,
    )

    assembly = assemble_messages(
        base_system_prompt="base identity",
        developer_fragments=[developer_fragment],
        user_fragments=[user_fragment],
        selector=ContextSelector(max_tokens=160),
    )

    diagnostics = assembly.selector_diagnostics
    assert diagnostics["selection_changed"] is True
    assert diagnostics["event_kind"] == "selection_truncation"
    assert diagnostics["semantic_compaction"] is False
    assert diagnostics["context_pressure_kind"] in {"prompt_budget_pressure", "model_window_pressure"}
    assert diagnostics["developer"]["selected_count"] == 1
    assert diagnostics["user"]["truncated_count"] == 1
    assert diagnostics["summary"]["truncated_count"] == 1
    assert diagnostics["selector"]["max_tokens"] == 160
    assert diagnostics["prompt"]["token_categories"]["runtime_fragments"]["tokens"] > 0


def test_context_selector_can_derive_budget_from_context_window():
    selector = ContextSelector.for_context_window(
        context_window=1000,
        base_system_prompt="identity",
        history_messages=[{"role": "user", "content": "hello"}],
        current_input_messages=[{"role": "user", "content": "ship it"}],
    )

    assert selector.max_tokens is not None
    assert 100 <= selector.max_tokens < 1000


def test_selector_profiles_are_registered_and_distinct():
    profiles = list_selector_profiles()

    assert profiles == ["chat_interactive", "fallback_chat", "consult_agent"]
    assert selector_profile_config("chat_interactive")["max_fragments"] > selector_profile_config("consult_agent")["max_fragments"]
    assert selector_profile_config("fallback_chat")["max_fragments"] < selector_profile_config("chat_interactive")["max_fragments"]


def test_chat_context_selector_applies_profile_fragment_caps_without_context_window():
    chat_selector = build_chat_context_selector(
        profile="chat_interactive",
        agent_name="unknown",
        model_id="",
        base_system_prompt="identity",
    )
    fallback_selector = build_chat_context_selector(
        profile="fallback_chat",
        agent_name="unknown",
        model_id="",
        base_system_prompt="identity",
    )
    query_selector = build_chat_context_selector(
        profile="consult_agent",
        agent_name="unknown",
        model_id="",
        base_system_prompt="identity",
    )

    assert chat_selector.max_fragments == 20
    assert fallback_selector.max_fragments == 15
    assert query_selector.max_fragments == 16
    assert chat_selector.max_tokens == 8000
    assert fallback_selector.max_tokens == 5000
    assert query_selector.max_tokens == 4500
    assert chat_selector.max_tokens_by_role == {"developer": 2500, "user": 5500}
    assert fallback_selector.max_tokens_by_role == {"developer": 1800, "user": 3200}
    assert query_selector.max_tokens_by_role == {"developer": 1800, "user": 2700}
    assert chat_selector.max_tokens_by_scope["run"] == 4000
    assert fallback_selector.max_tokens_by_scope["stage"] == 800
    assert query_selector.max_tokens_by_scope["shared_fact"] == 400


def test_chat_context_selector_uses_openai_reference_window_for_known_gpt_model():
    selector = build_chat_context_selector(
        profile="chat_interactive",
        agent_name="unknown",
        model_id="gpt-4.1",
        base_system_prompt="identity",
    )

    assert selector.context_window == 1_047_576
    assert selector.input_window == 1_014_808
    assert selector.reserved_completion_tokens == 32_768
    assert selector.max_tokens is not None
    assert selector.max_tokens >= 30_000


def test_chat_context_selector_materializes_large_window_budget_for_gpt_55():
    selector = build_chat_context_selector(
        profile="chat_interactive",
        agent_name="unknown",
        model_id="gpt-5.5",
        base_system_prompt="identity",
    )

    assert selector.context_window == 1_050_000
    assert selector.input_window == 922_000
    assert selector.reserved_completion_tokens == 128_000
    assert selector.max_tokens is not None
    assert selector.max_tokens >= 25_000


def test_first_turn_large_window_context_does_not_semantically_compact():
    selector = build_chat_context_selector(
        profile="chat_interactive",
        agent_name="unknown",
        model_id="gpt-5.5",
        base_system_prompt="identity",
        current_input_messages=[{"role": "user", "content": "Start the project."}],
    )
    fragment = ContextFragment(
        role="user",
        content="Project state is ready.",
        scope=ContextScope.RUN,
        visibility=ContextVisibility.GLOBAL,
        source="project_state",
        priority=20,
    )

    assembly = assemble_messages(
        base_system_prompt="identity",
        user_fragments=[fragment],
        current_input_messages=[{"role": "user", "content": "Start the project."}],
        selector=selector,
    )

    diagnostics = assembly.selector_diagnostics
    assert diagnostics["selection_changed"] is False
    assert diagnostics["semantic_compaction"] is False
    assert diagnostics["event_kind"] == "selection_pass"
    assert diagnostics["context_pressure_kind"] == "none"
    assert diagnostics["prompt"]["token_categories"]["runtime_fragments"]["tokens"] > 0


def test_assembly_reports_tool_schema_budget():
    assembly = assemble_messages(
        base_system_prompt="identity",
        current_input_messages=[{"role": "user", "content": "run tests"}],
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "description": "Run a command in the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            }
        ],
    )

    prompt = assembly.selector_diagnostics["prompt"]
    schema_budget = prompt["tool_schema_budget"]
    assert assembly.selector_diagnostics["event_kind"] == "tool_schema_budget"
    assert assembly.selector_diagnostics["context_pressure_kind"] == "tool_schema_budget"
    assert prompt["token_categories"]["tool_schema"]["tokens"] == schema_budget["tokens"]
    assert schema_budget["tool_count"] == 1
    assert schema_budget["schema_count"] == 1
    assert schema_budget["tokens"] > 0
    assert schema_budget["by_tool"][0]["tool_name"] == "run_shell"
    assert schema_budget["by_tool"][0]["tokens"] > 0


def test_assembly_reports_tool_schema_filter_savings():
    read_schema = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
        },
    }
    browser_schema = {
        "type": "function",
        "function": {
            "name": "browser",
            "description": "Control a browser and return page state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    }

    assembly = assemble_messages(
        base_system_prompt="identity",
        current_input_messages=[{"role": "user", "content": "read the file"}],
        tool_schemas=[read_schema],
        tool_schemas_before_filter=[read_schema, browser_schema],
        tool_schema_filter={
            "profile_name": "code_debug",
            "original_tool_count": 2,
            "active_tool_count": 1,
            "excluded_tool_count": 1,
            "active_tools": ["read_file"],
            "excluded_tools": ["browser"],
        },
    )

    schema_budget = assembly.selector_diagnostics["prompt"]["tool_schema_budget"]
    assert assembly.selector_diagnostics["event_kind"] == "tool_schema_budget"
    assert schema_budget["tool_count"] == 1
    assert schema_budget["original_tool_count"] == 2
    assert schema_budget["estimated_saved_tokens"] > 0
    assert schema_budget["estimated_savings_pct"] > 0
    assert schema_budget["filter"]["profile_name"] == "code_debug"
    assert schema_budget["excluded_by_tool"][0]["tool_name"] == "browser"


def test_context_selector_default_completion_reserve_scales_for_large_windows():
    selector = ContextSelector.for_context_window(
        context_window=400_000,
        base_system_prompt="identity",
        max_tokens=None,
    )

    assert selector.max_tokens is not None
    assert selector.max_tokens < 360_000
    assert selector.reserved_completion_tokens == 50_000
    assert selector.input_window == 350_000


def test_context_selector_reports_usage_band_in_diagnostics():
    fragment = ContextFragment(
        role="user",
        content="A" * 500,
        scope=ContextScope.TURN,
        visibility=ContextVisibility.GLOBAL,
        source="turn_context",
        priority=10,
    )
    selector = ContextSelector(
        max_tokens=200,
        context_window=1000,
        input_window=200,
        static_tokens=80,
    )

    assembly = assemble_messages(
        base_system_prompt="identity",
        user_fragments=[fragment],
        selector=selector,
    )

    usage_band = assembly.selector_diagnostics["selector"]["usage_band"]
    assert usage_band["band"] in {"yellow", "orange", "red"}
    assert usage_band["input_window"] == 200


def test_selector_profile_ratio_budget_materializes_from_model_context():
    from services.chat_prompt_builder import materialize_selector_profile_config
    from services.model_context import ModelContextMetadata

    config = materialize_selector_profile_config(
        {
            "max_tokens_cap_ratio": 0.5,
            "max_tokens_by_role_ratio": {"developer": 0.2, "user": 0.8},
            "max_tokens_by_scope_ratio": {"turn": 0.3, "run": 0.4},
        },
        ModelContextMetadata(
            model_id="demo",
            context_window=100_000,
            input_window=80_000,
            output_reserve=20_000,
        ),
    )

    assert config["max_tokens_cap"] == 40_000
    assert config["max_tokens_by_role"] == {"developer": 16_000, "user": 64_000}
    assert config["max_tokens_by_scope"] == {"turn": 24_000, "run": 32_000}
    assert config["reserved_completion_tokens"] == 20_000


def test_turn_state_fragments_keep_recent_protocol_and_summarize_older_tool_rounds():
    turn_state = TurnContextState(
        previous_agent_work="Previous agent handoff",
        boss_instructions=["Prefer the smallest patch."],
        max_protocol_rounds=1,
    )
    turn_state.record_tool_round(
        assistant_content="Inspect the repository layout first.",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_files", "arguments": "{\"path\": \".\"}"},
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_1",
                tool_name="list_files",
                arguments="{\"path\": \".\"}",
                result="README.md\nbackend/\nfrontend/",
                success=True,
            )
        ],
    )
    turn_state.record_tool_round(
        assistant_content="Open the API route file next.",
        tool_calls=[
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{\"file_path\": \"backend/routes/api.py\"}"},
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_2",
                tool_name="read_file",
                arguments="{\"file_path\": \"backend/routes/api.py\"}",
                result="async def send_message(...",
                success=True,
            )
        ],
    )

    developer_fragments = build_turn_state_developer_fragments(turn_state)
    user_fragments = build_turn_state_user_fragments(turn_state)
    protocol_messages = turn_state.protocol_messages()

    assert developer_fragments[0].source == "boss_instruction"
    assert any(fragment.source == "tool_round_summaries" for fragment in user_fragments)
    assert any("Previous agent handoff" in fragment.content for fragment in user_fragments)
    assert len(protocol_messages) == 2
    assert protocol_messages[0]["role"] == "assistant"
    assert protocol_messages[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert all(
        "list_files" not in json.dumps(message, ensure_ascii=False)
        for message in protocol_messages
    )


def test_turn_state_protocol_message_summarizes_long_tool_result_with_reference():
    turn_state = TurnContextState(max_protocol_rounds=1)
    long_result = "\n".join(f"line {index}: {'x' * 80}" for index in range(120))
    turn_state.record_tool_round(
        assistant_content="Run the test suite.",
        tool_calls=[
            {
                "id": "call_test",
                "type": "function",
                "function": {"name": "run_shell", "arguments": "{\"command\": \"pytest\"}"},
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_test",
                tool_name="run_shell",
                arguments="{\"command\": \"pytest\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "run_shell",
                    "result": long_result,
                    "success": False,
                    "status": "failed",
                    "metadata": {
                        "output_filter": {"tee_path": "C:/tmp/catown-tee.log"},
                        "tracked_process": {
                            "token": "tracked-1",
                            "log_path": "C:/tmp/run-shell.log",
                        },
                    },
                },
                success=False,
            )
        ],
    )

    protocol_messages = turn_state.protocol_messages()
    tool_message = protocol_messages[-1]

    assert tool_message["role"] == "tool"
    assert len(tool_message["content"]) < 1600
    assert "[Tool Result Summary]" in tool_message["content"]
    assert "[truncated for context budget]" in tool_message["content"]
    assert "Full output tee: C:/tmp/catown-tee.log" in tool_message["content"]
    assert "Tracked process log: C:/tmp/run-shell.log" in tool_message["content"]
    assert "Tracked process token: tracked-1" in tool_message["content"]
    assert "line 40:" not in tool_message["content"]
    assert "line 119:" in tool_message["content"]

    context_budget = turn_state.tool_rounds[0].tool_results[0].metadata["context_budget"]
    assert context_budget["prompt_truncated"] is True
    assert context_budget["stored_result_chars"] < context_budget["original_result_chars"]


def test_turn_state_protocol_message_summarizes_long_consult_agent_result_with_reference():
    turn_state = TurnContextState(max_protocol_rounds=1)
    long_result = "\n".join(f"consult line {index}: {'x' * 80}" for index in range(120))
    long_result = f"[Response from analyst (Analyst)]:\n{long_result}\n[consult_step_id] consult-analyst-1"
    turn_state.record_tool_round(
        assistant_content="Ask analyst for a focused read.",
        tool_calls=[
            {
                "id": "call_consult",
                "type": "function",
                "function": {
                    "name": "consult_agent",
                    "arguments": "{\"target_agent\": \"analyst\", \"question\": \"What is the risk?\"}",
                },
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_consult",
                tool_name="consult_agent",
                arguments="{\"target_agent\": \"analyst\", \"question\": \"What is the risk?\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "consult_agent",
                    "result": long_result,
                    "success": True,
                    "status": "succeeded",
                    "metadata": {
                        "consult_agent": {
                            "consult_step_id": "consult-analyst-1",
                            "task_run_id": 42,
                            "client_turn_id": "turn-42",
                        }
                    },
                },
            )
        ],
    )

    tool_message = turn_state.protocol_messages()[-1]

    assert tool_message["role"] == "tool"
    assert len(tool_message["content"]) < 1700
    assert "[Tool Result Summary]" in tool_message["content"]
    assert "Consult step: consult-analyst-1" in tool_message["content"]
    assert "Consult task run: 42" in tool_message["content"]
    assert "Consult client turn: turn-42" in tool_message["content"]
    assert "consult line 40:" not in tool_message["content"]
    assert "[consult_step_id] consult-analyst-1" in tool_message["content"]

    context_budget = turn_state.tool_rounds[0].tool_results[0].metadata["context_budget"]
    assert context_budget["full_output_refs"]["consult_step_id"] == "consult-analyst-1"
    assert context_budget["prompt_truncated"] is True


def test_turn_state_protocol_message_summarizes_long_tool_result_with_artifact_reference():
    turn_state = TurnContextState(max_protocol_rounds=1)
    long_result = "\n".join(f"browser line {index}: {'x' * 80}" for index in range(120))
    turn_state.record_tool_round(
        assistant_content="Read browser text.",
        tool_calls=[
            {
                "id": "call_browser",
                "type": "function",
                "function": {"name": "browser", "arguments": "{\"action\": \"get_text\"}"},
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_browser",
                tool_name="browser",
                arguments="{\"action\": \"get_text\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "browser",
                    "result": long_result,
                    "success": True,
                    "status": "succeeded",
                    "metadata": {
                        "tool_output_artifact": {
                            "path": "C:/tmp/browser-output.txt",
                            "sha256": "abc123",
                            "chars": len(long_result),
                            "tool_name": "browser",
                        }
                    },
                },
            )
        ],
    )

    tool_message = turn_state.protocol_messages()[-1]

    assert tool_message["role"] == "tool"
    assert len(tool_message["content"]) < 1700
    assert "[Tool Result Summary]" in tool_message["content"]
    assert "[truncated for context budget]" in tool_message["content"]
    assert "Tool output artifact: C:/tmp/browser-output.txt" in tool_message["content"]
    assert "Tool output sha256: abc123" in tool_message["content"]
    assert "browser line 40:" not in tool_message["content"]
    assert "browser line 119:" in tool_message["content"]

    context_budget = turn_state.tool_rounds[0].tool_results[0].metadata["context_budget"]
    assert context_budget["full_output_refs"]["tool_output_artifact_path"] == "C:/tmp/browser-output.txt"
    assert context_budget["full_output_refs"]["tool_output_artifact_sha256"] == "abc123"
    assert context_budget["prompt_truncated"] is True


def test_turn_state_protocol_message_preserves_test_runner_signals_in_long_summary():
    turn_state = TurnContextState(max_protocol_rounds=1)
    long_result = "\n".join(
        [
            "collected 3 items",
            "tests/test_widget.py::test_render FAILED",
            "tests/test_widget.py::test_save PASSED",
            "tests/test_widget.py::test_load PASSED",
            "=================================== FAILURES ===================================",
            "FAILED tests/test_widget.py::test_render - AssertionError: expected visible button",
            "=========================== short test summary info ============================",
            "FAILED tests/test_widget.py::test_render - AssertionError: expected visible button",
            "1 failed, 2 passed in 0.42s",
            "Exit code: 1",
            *[f"pytest log line {index}: {'x' * 100}" for index in range(80)],
        ]
    )
    turn_state.record_tool_round(
        assistant_content="Run focused tests.",
        tool_calls=[
            {
                "id": "call_pytest",
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "arguments": "{\"command\": \"python -m pytest tests/test_widget.py\"}",
                },
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_pytest",
                tool_name="run_shell",
                arguments="{\"command\": \"python -m pytest tests/test_widget.py\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "run_shell",
                    "result": long_result,
                    "success": False,
                    "status": "failed",
                    "metadata": {"output_filter": {"tee_path": "C:/tmp/pytest.log"}},
                },
                success=False,
            )
        ],
    )

    tool_message = turn_state.protocol_messages()[-1]

    assert "[Tool Result Summary]" in tool_message["content"]
    assert "Key signals:" in tool_message["content"]
    assert "- Command: python -m pytest tests/test_widget.py" in tool_message["content"]
    assert "- Exit code: 1" in tool_message["content"]
    assert "- Test status: failed" in tool_message["content"]
    assert "- Counts: passed=2, failed=1" in tool_message["content"]
    assert "Failure summary:" in tool_message["content"]
    assert "- Failed tests: tests/test_widget.py::test_render" in tool_message["content"]
    assert "expected visible button" in tool_message["content"]
    assert "Full output tee: C:/tmp/pytest.log" in tool_message["content"]


def test_turn_state_protocol_message_preserves_browser_signals_in_long_summary():
    turn_state = TurnContextState(max_protocol_rounds=1)
    page_text = "\n".join(f"Page paragraph {index}: {'x' * 90}" for index in range(80))
    browser_result = json.dumps(
        {
            "success": True,
            "page": {
                "url": "https://example.test/dashboard",
                "title": "Example Dashboard",
            },
            "response": {
                "status": 200,
            },
            "screenshot_path": "C:/tmp/dashboard.png",
            "text": page_text,
        }
    )
    turn_state.record_tool_round(
        assistant_content="Read browser text.",
        tool_calls=[
            {
                "id": "call_browser",
                "type": "function",
                "function": {
                    "name": "browser",
                    "arguments": "{\"action\": \"get_text\", \"url\": \"https://example.test/dashboard\"}",
                },
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_browser",
                tool_name="browser",
                arguments="{\"action\": \"get_text\", \"url\": \"https://example.test/dashboard\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "browser",
                    "result": browser_result,
                    "success": True,
                    "status": "succeeded",
                    "metadata": {
                        "tool_output_artifact": {
                            "path": "C:/tmp/browser-output.txt",
                            "sha256": "abc123",
                            "chars": len(browser_result),
                            "tool_name": "browser",
                        }
                    },
                },
            )
        ],
    )

    tool_message = turn_state.protocol_messages()[-1]

    assert "[Tool Result Summary]" in tool_message["content"]
    assert "Key signals:" in tool_message["content"]
    assert "- Action: get_text" in tool_message["content"]
    assert "- URL: https://example.test/dashboard" in tool_message["content"]
    assert "- Title: Example Dashboard" in tool_message["content"]
    assert "- HTTP status: 200" in tool_message["content"]
    assert f"- Text chars: {len(page_text)}" in tool_message["content"]
    assert "- Output path: C:/tmp/dashboard.png" in tool_message["content"]
    assert "Tool output artifact: C:/tmp/browser-output.txt" in tool_message["content"]


def test_turn_state_protocol_message_preserves_search_results_in_long_summary():
    turn_state = TurnContextState(max_protocol_rounds=1)
    search_result = json.dumps(
        {
            "query": "catown context budget",
            "results": [
                {
                    "title": "Catown Context Budget ADR",
                    "url": "https://example.test/catown/context-budget",
                },
                {
                    "title": "Catown Tool Output Savings",
                    "url": "https://example.test/catown/tool-output",
                },
            ],
            "content": "\n".join(f"search blob line {index}: {'x' * 90}" for index in range(80)),
        }
    )
    turn_state.record_tool_round(
        assistant_content="Search for context budget references.",
        tool_calls=[
            {
                "id": "call_search",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": "{\"query\": \"catown context budget\"}",
                },
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_search",
                tool_name="web_search",
                arguments="{\"query\": \"catown context budget\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "web_search",
                    "result": search_result,
                    "success": True,
                    "status": "succeeded",
                    "metadata": {},
                },
            )
        ],
    )

    tool_message = turn_state.protocol_messages()[-1]

    assert "[Tool Result Summary]" in tool_message["content"]
    assert "Key signals:" in tool_message["content"]
    assert "- Query: catown context budget" in tool_message["content"]
    assert "- Top result: Catown Context Budget ADR - https://example.test/catown/context-budget" in tool_message["content"]
    assert "- Top result: Catown Tool Output Savings - https://example.test/catown/tool-output" in tool_message["content"]
    assert "search blob line 40:" not in tool_message["content"]


def test_assembly_reports_tool_output_budget_savings():
    turn_state = TurnContextState(max_protocol_rounds=1)
    long_result = "\n".join(f"result line {index}: {'x' * 120}" for index in range(100))
    turn_state.record_tool_round(
        assistant_content="Run the build.",
        tool_calls=[
            {
                "id": "call_build",
                "type": "function",
                "function": {"name": "run_shell", "arguments": "{\"command\": \"npm run build\"}"},
            }
        ],
        tool_results=[
            build_tool_result_record(
                tool_call_id="call_build",
                tool_name="run_shell",
                arguments="{\"command\": \"npm run build\"}",
                result={
                    "__catown_tool_result__": True,
                    "tool_name": "run_shell",
                    "result": long_result,
                    "success": True,
                    "status": "succeeded",
                    "metadata": {"output_filter": {"tee_path": "C:/tmp/build.log"}},
                },
            )
        ],
    )

    assembly = assemble_messages(
        base_system_prompt="base identity",
        current_input_messages=turn_state.protocol_messages(),
    )

    diagnostics = assembly.selector_diagnostics
    tool_budget = diagnostics["prompt"]["tool_output_budget"]
    assert diagnostics["selection_changed"] is False
    assert diagnostics["event_kind"] == "tool_output_budget"
    assert diagnostics["context_pressure_kind"] == "tool_output_budget"
    assert tool_budget["summarized_message_count"] == 1
    assert tool_budget["original_chars"] > tool_budget["stored_chars"]
    assert tool_budget["estimated_saved_tokens"] > 0
    assert tool_budget["by_tool"]["run_shell"]["summarized_message_count"] == 1
    assert tool_budget["by_tool"]["run_shell"]["original_chars"] > tool_budget["by_tool"]["run_shell"]["stored_chars"]
    assert tool_budget["by_tool"]["run_shell"]["estimated_saved_tokens"] > 0


def test_turn_state_can_be_seeded_from_checkpoint_snapshot():
    turn_state = build_turn_state_from_checkpoint_snapshot(
        {
            "turn_local_state": {
                "protocol_tail_messages": [
                    {
                        "role": "assistant",
                        "content": "Open the API route file next.",
                        "tool_calls": [
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{\"file_path\": \"backend/routes/api.py\"}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_2",
                        "name": "read_file",
                        "content": "async def send_message(...",
                    },
                ],
                "prior_round_summaries": [
                    {
                        "turn": 1,
                        "tool_names": ["list_files"],
                        "blocked_tool_count": 0,
                        "assistant_content": "Inspect the repository layout first.",
                    }
                ],
            }
        },
        previous_agent_work="Continue after replay.",
    )

    protocol_messages = turn_state.protocol_messages()
    summarized_lines = turn_state.summarized_tool_lines()

    assert turn_state.previous_agent_work == "Continue after replay."
    assert len(protocol_messages) == 2
    assert protocol_messages[0]["role"] == "assistant"
    assert protocol_messages[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert any("Prior round 1" in line for line in summarized_lines)
    assert any("list_files" in line for line in summarized_lines)
