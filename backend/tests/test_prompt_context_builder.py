import os
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.chat_prompt_builder import (  # noqa: E402
    build_chat_context_selector,
    list_selector_profiles,
    selector_profile_config,
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
from services.turn_state import TurnContextState, build_tool_result_record  # noqa: E402
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
        "project_context",
        "project_status",
        "chatroom_context",
        "chatroom_lineage",
        "runtime_context",
        "team_members",
        "inter_agent_messages",
        "memory_context",
        "previous_agent_work",
    ]
    assert fragments[0].visibility == "global"
    assert fragments[3].content.startswith("## Chat Lineage")
    assert fragments[6].scope == "shared_fact"
    assert "Current focus" not in fragments[1].content
    assert "Blocking reason" not in fragments[1].content
    assert "Latest summary" not in fragments[1].content


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
        "project_context",
        "chatroom_context",
        "chatroom_lineage",
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
        "project_context",
        "chatroom_context",
    ]
    assert messages == [
        {"role": "system", "content": "base identity"},
        {"role": "user", "content": "## Current Project\n- Project ID: 1\n- Name: Catown"},
        {"role": "user", "content": "## Current Chat\n- Chat ID: 2\n- Title: Chat"},
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

    assert profiles == ["chat_interactive", "fallback_chat", "query_agent"]
    assert selector_profile_config("chat_interactive")["max_fragments"] > selector_profile_config("query_agent")["max_fragments"]
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
        profile="query_agent",
        agent_name="unknown",
        model_id="",
        base_system_prompt="identity",
    )

    assert chat_selector.max_fragments == 12
    assert fallback_selector.max_fragments == 10
    assert query_selector.max_fragments == 11
    assert chat_selector.max_tokens == 3200
    assert fallback_selector.max_tokens == 2600
    assert query_selector.max_tokens == 2200


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
