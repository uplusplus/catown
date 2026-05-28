# -*- coding: utf-8 -*-
"""Utilities for assembling model-visible context messages."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Literal, Mapping, Optional

from services.output_header_policy import output_header_guidance_text


ContextRole = Literal["system", "developer", "user"]


class ContextScope:
    SESSION = "session"
    RUN = "run"
    STAGE = "stage"
    TURN = "turn"
    AGENT_PRIVATE = "agent_private"
    SHARED_FACT = "shared_fact"


class ContextVisibility:
    GLOBAL = "global"
    AGENT = "agent"
    ROLE = "role"
    STAGE = "stage"
    PRIVATE = "private"


@dataclass(frozen=True)
class ContextFragment:
    role: ContextRole
    content: str
    scope: str
    visibility: str
    source: str
    priority: int = 100

    def to_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ContextSelector:
    allowed_visibilities: frozenset[str] | None = None
    allowed_scopes: frozenset[str] | None = None
    max_fragments: int | None = None
    max_tokens: int | None = None
    context_window: int | None = None
    input_window: int | None = None
    reserved_completion_tokens: int | None = None
    prompt_overhead_tokens: int = 256
    static_tokens: int | None = None
    max_tokens_by_role: Mapping[str, int] | None = None
    max_tokens_by_scope: Mapping[str, int] | None = None
    truncate_to_budget: bool = True
    min_tokens_for_truncation: int = 48

    def __post_init__(self) -> None:
        if self.allowed_visibilities is not None and not isinstance(self.allowed_visibilities, frozenset):
            object.__setattr__(self, "allowed_visibilities", frozenset(self.allowed_visibilities))
        if self.allowed_scopes is not None and not isinstance(self.allowed_scopes, frozenset):
            object.__setattr__(self, "allowed_scopes", frozenset(self.allowed_scopes))
        if self.max_tokens_by_role is not None and not isinstance(self.max_tokens_by_role, dict):
            object.__setattr__(self, "max_tokens_by_role", dict(self.max_tokens_by_role))
        if self.max_tokens_by_scope is not None and not isinstance(self.max_tokens_by_scope, dict):
            object.__setattr__(self, "max_tokens_by_scope", dict(self.max_tokens_by_scope))
        if self.max_fragments is not None and self.max_fragments <= 0:
            object.__setattr__(self, "max_fragments", None)
        if self.max_tokens is not None and self.max_tokens <= 0:
            object.__setattr__(self, "max_tokens", None)
        for attr in ("context_window", "input_window", "reserved_completion_tokens", "static_tokens"):
            value = getattr(self, attr)
            if value is not None and value <= 0:
                object.__setattr__(self, attr, None)
        if self.prompt_overhead_tokens < 0:
            object.__setattr__(self, "prompt_overhead_tokens", 0)
        if self.max_tokens_by_role is not None:
            normalized_role_limits = {
                str(role): int(limit)
                for role, limit in self.max_tokens_by_role.items()
                if limit is not None and int(limit) > 0
            }
            object.__setattr__(self, "max_tokens_by_role", normalized_role_limits or None)
        if self.max_tokens_by_scope is not None:
            normalized_scope_limits = {
                str(scope): int(limit)
                for scope, limit in self.max_tokens_by_scope.items()
                if limit is not None and int(limit) > 0
            }
            object.__setattr__(self, "max_tokens_by_scope", normalized_scope_limits or None)
        if self.min_tokens_for_truncation < 1:
            object.__setattr__(self, "min_tokens_for_truncation", 1)

    @classmethod
    def for_context_window(
        cls,
        *,
        context_window: int | None,
        base_system_prompt: str = "",
        history_messages: Optional[Iterable[dict[str, Any]]] = None,
        current_input_messages: Optional[Iterable[dict[str, Any]]] = None,
        reserved_completion_tokens: int | None = None,
        prompt_overhead_tokens: int = 256,
        **kwargs: Any,
    ) -> "ContextSelector":
        configured_max_tokens = kwargs.pop("max_tokens", None)
        if context_window is None or context_window <= 0:
            return cls(max_tokens=configured_max_tokens, **kwargs)

        completion_reserve = reserved_completion_tokens or _default_completion_reserve(context_window)
        static_tokens = (
            estimate_text_tokens(base_system_prompt)
            + estimate_messages_tokens(history_messages)
            + estimate_messages_tokens(current_input_messages)
            + max(prompt_overhead_tokens, 0)
        )
        available_tokens = context_window - completion_reserve - static_tokens
        if available_tokens <= 0:
            available_tokens = max(128, context_window // 12)

        if configured_max_tokens is not None:
            available_tokens = min(available_tokens, configured_max_tokens)

        return cls(
            max_tokens=available_tokens,
            context_window=context_window,
            input_window=max(context_window - completion_reserve, 1),
            reserved_completion_tokens=completion_reserve,
            prompt_overhead_tokens=max(prompt_overhead_tokens, 0),
            static_tokens=static_tokens,
            **kwargs,
        )

    def select_fragments(
        self,
        fragments: Optional[Iterable[Optional[ContextFragment]]],
        *,
        role: ContextRole,
    ) -> list[ContextFragment]:
        filtered = self._filter_fragments(fragments, role=role)
        return self._select_from_candidates(filtered)

    def select_for_assembly(
        self,
        *,
        developer_fragments: Optional[Iterable[Optional[ContextFragment]]] = None,
        user_fragments: Optional[Iterable[Optional[ContextFragment]]] = None,
    ) -> tuple[list[ContextFragment], list[ContextFragment]]:
        developer, user, _ = self.select_for_assembly_with_report(
            developer_fragments=developer_fragments,
            user_fragments=user_fragments,
        )
        return developer, user

    def select_for_assembly_with_report(
        self,
        *,
        developer_fragments: Optional[Iterable[Optional[ContextFragment]]] = None,
        user_fragments: Optional[Iterable[Optional[ContextFragment]]] = None,
    ) -> tuple[list[ContextFragment], list[ContextFragment], dict[str, Any]]:
        candidates: list[tuple[int, ContextFragment]] = []
        for fragment in self._filter_fragments(developer_fragments, role="developer"):
            candidates.append((0, fragment))
        for fragment in self._filter_fragments(user_fragments, role="user"):
            candidates.append((1, fragment))

        selected_pairs, diagnostics = self._select_ranked_candidates_with_report(candidates)
        developer = [fragment for role_order, fragment in selected_pairs if role_order == 0]
        user = [fragment for role_order, fragment in selected_pairs if role_order == 1]
        developer.sort(key=lambda fragment: _fragment_sort_key(fragment, role_order=0))
        user.sort(key=lambda fragment: _fragment_sort_key(fragment, role_order=1))
        return developer, user, diagnostics

    def select_messages(
        self,
        fragments: Optional[Iterable[Optional[ContextFragment]]],
        *,
        role: ContextRole,
    ) -> list[dict[str, str]]:
        return [fragment.to_message() for fragment in self.select_fragments(fragments, role=role)]

    def _filter_fragments(
        self,
        fragments: Optional[Iterable[Optional[ContextFragment]]],
        *,
        role: ContextRole,
    ) -> list[ContextFragment]:
        selected: list[ContextFragment] = []
        for fragment in fragments or []:
            if fragment is None or fragment.role != role or not fragment.content.strip():
                continue
            if (
                self.allowed_visibilities is not None
                and fragment.visibility not in self.allowed_visibilities
            ):
                continue
            if self.allowed_scopes is not None and fragment.scope not in self.allowed_scopes:
                continue
            selected.append(fragment)
        return selected

    def _select_from_candidates(self, fragments: Iterable[ContextFragment]) -> list[ContextFragment]:
        ranked = [(0, fragment) for fragment in fragments]
        selected = self._select_ranked_candidates(ranked)
        return [fragment for _, fragment in selected]

    def _select_ranked_candidates(
        self,
        candidates: Iterable[tuple[int, ContextFragment]],
    ) -> list[tuple[int, ContextFragment]]:
        selected, _ = self._select_ranked_candidates_with_report(candidates)
        return selected

    def _select_ranked_candidates_with_report(
        self,
        candidates: Iterable[tuple[int, ContextFragment]],
    ) -> tuple[list[tuple[int, ContextFragment]], dict[str, Any]]:
        ranked = sorted(
            candidates,
            key=lambda item: _fragment_sort_key(item[1], role_order=item[0]),
        )
        role_reports = {
            0: SelectorRoleReport(role="developer"),
            1: SelectorRoleReport(role="user"),
        }
        role_token_usage = {
            0: 0,
            1: 0,
        }
        scope_token_usage: dict[str, int] = {}
        scope_reports: dict[str, SelectorScopeReport] = {}
        for role_order, fragment in ranked:
            role_reports.setdefault(role_order, SelectorRoleReport(role=f"role_{role_order}")).record_candidate(fragment)
            scope_reports.setdefault(fragment.scope, SelectorScopeReport(scope=fragment.scope)).record_candidate(fragment)
        if self.max_tokens is None and self.max_fragments is None:
            for role_order, fragment in ranked:
                role_reports[role_order].record_selected(fragment)
                scope_reports[fragment.scope].record_selected(fragment)
            return ranked, _selector_diagnostics_payload(
                selector=self,
                role_reports=role_reports,
                scope_reports=scope_reports,
                compaction_applied=False,
            )

        selected: list[tuple[int, ContextFragment]] = []
        used_tokens = 0
        for role_order, fragment in ranked:
            if self.max_fragments is not None and len(selected) >= self.max_fragments:
                role_reports[role_order].record_dropped(fragment)
                continue

            fragment_tokens = estimate_text_tokens(fragment.content)
            role_name = role_reports[role_order].role
            scope_name = fragment.scope
            role_max_tokens = (
                self.max_tokens_by_role.get(role_name)
                if self.max_tokens_by_role is not None
                else None
            )
            scope_max_tokens = (
                self.max_tokens_by_scope.get(scope_name)
                if self.max_tokens_by_scope is not None
                else None
            )
            scope_used_tokens = scope_token_usage.get(scope_name, 0)
            within_total_budget = self.max_tokens is None or used_tokens + fragment_tokens <= self.max_tokens
            within_role_budget = role_max_tokens is None or role_token_usage[role_order] + fragment_tokens <= role_max_tokens
            within_scope_budget = scope_max_tokens is None or scope_used_tokens + fragment_tokens <= scope_max_tokens
            if within_total_budget and within_role_budget and within_scope_budget:
                selected.append((role_order, fragment))
                used_tokens += fragment_tokens
                role_token_usage[role_order] += fragment_tokens
                scope_token_usage[scope_name] = scope_used_tokens + fragment_tokens
                role_reports[role_order].record_selected(fragment)
                scope_reports[scope_name].record_selected(fragment)
                continue

            if not self.truncate_to_budget:
                role_reports[role_order].record_dropped(fragment)
                continue

            remaining_total_tokens = self.max_tokens - used_tokens if self.max_tokens is not None else None
            remaining_role_tokens = (
                role_max_tokens - role_token_usage[role_order]
                if role_max_tokens is not None
                else None
            )
            remaining_scope_tokens = (
                scope_max_tokens - scope_used_tokens
                if scope_max_tokens is not None
                else None
            )
            remaining_tokens_candidates = [
                limit for limit in (remaining_total_tokens, remaining_role_tokens, remaining_scope_tokens) if limit is not None
            ]
            remaining_tokens = min(remaining_tokens_candidates) if remaining_tokens_candidates else None
            if remaining_tokens is None or remaining_tokens < self.min_tokens_for_truncation:
                role_reports[role_order].record_dropped(fragment)
                continue

            truncated_content = _truncate_text_to_token_budget(
                fragment.content,
                remaining_tokens,
            )
            if not truncated_content:
                role_reports[role_order].record_dropped(fragment)
                continue

            truncated_fragment = replace(fragment, content=truncated_content)
            selected.append((role_order, truncated_fragment))
            truncated_tokens = estimate_text_tokens(truncated_content)
            used_tokens += truncated_tokens
            role_token_usage[role_order] += truncated_tokens
            scope_token_usage[scope_name] = scope_used_tokens + truncated_tokens
            role_reports[role_order].record_selected(truncated_fragment)
            scope_reports[scope_name].record_selected(truncated_fragment)
            role_reports[role_order].record_truncated(fragment, truncated_fragment)

        return selected, _selector_diagnostics_payload(
            selector=self,
            role_reports=role_reports,
            scope_reports=scope_reports,
            compaction_applied=any(report.compaction_applied for report in role_reports.values()),
        )


@dataclass
class PromptAssembly:
    system_message: dict[str, str]
    developer_messages: list[dict[str, str]] = field(default_factory=list)
    user_context_messages: list[dict[str, str]] = field(default_factory=list)
    history_messages: list[dict[str, Any]] = field(default_factory=list)
    current_input_messages: list[dict[str, Any]] = field(default_factory=list)
    selector_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system_message.get("content"):
            messages.append(self.system_message)
        messages.extend(self.developer_messages)
        messages.extend(self.user_context_messages)
        messages.extend(self.history_messages)
        messages.extend(self.current_input_messages)
        return messages


def build_base_system_prompt(agent_config: Any, fallback_name: str = "Agent", fallback_role: str = "assistant") -> str:
    """Build stable agent identity instructions from an agent config or DB row."""

    data = _agent_data(agent_config)
    name = str(data.get("name") or fallback_name or "Agent")
    role = data.get("role") or fallback_role
    soul = data.get("soul") or {}

    parts: list[str] = []
    identity = str(soul.get("identity") or "").strip()
    values = soul.get("values") or []
    style = str(soul.get("style") or "").strip()

    if identity:
        parts.append(f"You are {name}. {identity}")
    else:
        role_text = _role_title(role) or fallback_role or "assistant"
        parts.append(f"You are {name}, a {role_text}.")

    if values:
        parts.append("Your principles:\n" + "\n".join(f"- {value}" for value in values))
    if style:
        parts.append(f"Communication style: {style}")

    responsibilities, rules = _role_lists(role)
    if responsibilities:
        parts.append("## Responsibilities\n" + "\n".join(f"- {item}" for item in responsibilities))
    if rules:
        parts.append("## Rules\n" + "\n".join(f"- {item}" for item in rules))

    return "\n\n".join(part for part in parts if part.strip())


def build_stage_developer_context(
    *,
    stage_cfg: Any = None,
    active_skills: Optional[Iterable[str]] = None,
    tools: Optional[Iterable[str]] = None,
    skills_config: Optional[Mapping[str, Mapping[str, Any]]] = None,
    agent_skills: Optional[Iterable[str]] = None,
    tool_guidance: str = "",
) -> Optional[ContextFragment]:
    parts: list[str] = []

    if stage_cfg is not None:
        display_name = getattr(stage_cfg, "display_name", None) or getattr(stage_cfg, "name", "")
        stage_name = getattr(stage_cfg, "name", "")
        if display_name or stage_name:
            parts.append(f"## Stage\n- Name: {display_name} ({stage_name})")
        context_prompt = getattr(stage_cfg, "context_prompt", "") or ""
        if context_prompt:
            parts.append(f"## Stage Instructions\n{context_prompt}")
        expected_artifacts = list(getattr(stage_cfg, "expected_artifacts", []) or [])
        if expected_artifacts:
            parts.append("## Expected Artifacts\n" + "\n".join(f"- {item}" for item in expected_artifacts))

    skill_text = _skill_context(
        active_skills=active_skills,
        skills_config=skills_config,
        agent_skills=agent_skills,
    )
    if skill_text:
        parts.append(skill_text)

    tool_names = [name for name in (tools or []) if name]
    if tool_names or tool_guidance.strip():
        tool_parts = []
        if tool_names:
            tool_parts.append("Available tools: " + ", ".join(tool_names))
        if tool_guidance.strip():
            tool_parts.append(tool_guidance.strip())
        parts.append("## Tool Guidance\n" + "\n".join(tool_parts))

    if parts:
        parts.append(output_header_guidance_text())

    if not parts:
        return None
    return ContextFragment(
        role="developer",
        content="\n\n".join(parts),
        scope=ContextScope.STAGE if stage_cfg is not None else ContextScope.TURN,
        visibility=ContextVisibility.AGENT,
        source="stage_developer_context",
        priority=40,
    )


def build_operating_developer_context(*, agent_name: str = "", agent_role: str = "") -> ContextFragment:
    identity = ""
    if agent_name or agent_role:
        identity = f"\n- Active agent: {agent_name or 'unknown'}"
        if agent_role:
            identity += f" ({agent_role})"
    return ContextFragment(
        role="developer",
        content=(
            "## Operating Contract\n"
            "You are running inside Catown's layered context orchestration model."
            f"{identity}\n"
            "- Treat `system` as stable identity and long-term rules only.\n"
            "- Treat `developer` messages as current operating rules, stage contracts, skill hints, and tool policy.\n"
            "- Treat `user` context as project state, shared facts, teammate handoffs, and the current task surface.\n"
            "- Keep attention on the latest user request and the highest-priority developer constraints; do not overfit stale history.\n"
            "- Respect context visibility: use shared facts for coordination, but do not leak agent-private reasoning unless explicitly asked.\n"
            "- When a tool is useful, call it instead of pretending to know external or workspace state.\n"
            "- If you intend to hand off or notify another agent via chat mention, start the last non-empty paragraph of the final chat message with one or more `@agent_name` mentions.\n"
            "- Mentions outside that final paragraph are treated as normal text and do not trigger routing.\n"
            "- If context conflicts, prefer newer turn/stage developer instructions over older history and state the assumption briefly."
        ),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="operating_contract",
        priority=10,
    )


def build_boss_instruction_context(instructions: Iterable[str]) -> Optional[ContextFragment]:
    items = [str(item).strip() for item in instructions if str(item).strip()]
    if not items:
        return None
    return ContextFragment(
        role="developer",
        content="## BOSS Instructions\n" + "\n".join(f"- {item}" for item in items),
        scope=ContextScope.TURN,
        visibility=ContextVisibility.AGENT,
        source="boss_instruction",
        priority=50,
    )


def build_turn_state_developer_fragments(turn_state: Any = None) -> list[ContextFragment]:
    if turn_state is None:
        return []
    boss_fragment = build_boss_instruction_context(getattr(turn_state, "boss_instructions", []) or [])
    return [boss_fragment] if boss_fragment is not None else []


def build_turn_state_user_fragments(turn_state: Any = None) -> list[ContextFragment]:
    if turn_state is None:
        return []

    fragments: list[ContextFragment] = []
    inter_agent_messages = getattr(turn_state, "inter_agent_messages", None) or []
    if inter_agent_messages:
        lines = []
        for item in inter_agent_messages:
            if not isinstance(item, Mapping):
                continue
            sender = item.get("from_agent") or item.get("from") or "unknown"
            content = item.get("content") or ""
            if content:
                lines.append(f"- From {sender}: {content}")
        if lines:
            fragments.append(
                ContextFragment(
                    role="user",
                    content="## Inter-Agent Messages\n" + "\n".join(lines),
                    scope=ContextScope.SHARED_FACT,
                    visibility=ContextVisibility.AGENT,
                    source="turn_inter_agent_messages",
                    priority=45,
                )
            )

    previous_work = _clean_text(getattr(turn_state, "previous_agent_work", ""))
    if previous_work:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Previous Agent Work\n" + previous_work[:1500],
                scope=ContextScope.TURN,
                visibility=ContextVisibility.AGENT,
                source="turn_previous_agent_work",
                priority=55,
            )
        )

    summarized_tool_lines = list(getattr(turn_state, "summarized_tool_lines", lambda: [])() or [])
    if summarized_tool_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Tool Work So Far\n" + "\n".join(summarized_tool_lines),
                scope=ContextScope.TURN,
                visibility=ContextVisibility.AGENT,
                source="tool_round_summaries",
                priority=48,
            )
        )

    return fragments


def build_runtime_user_fragments(
    *,
    runtime_context: str = "",
    project: Any = None,
    chatroom: Any = None,
    source_chatroom: Any = None,
    run: Any = None,
    standalone_note: str = "",
    team_members: Optional[Iterable[str]] = None,
    memories: Optional[Iterable[str]] = None,
    inter_agent_messages: Optional[Iterable[Mapping[str, Any]]] = None,
    extra_context: str = "",
) -> list[ContextFragment]:
    fragments: list[ContextFragment] = []
    runtime_text = _clean_text(runtime_context)
    note_text = _clean_text(standalone_note)
    previous_work_text = _clean_text(extra_context)

    if note_text:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Session Instructions\n" + note_text,
                scope=ContextScope.TURN,
                visibility=ContextVisibility.AGENT,
                source="standalone_note",
                priority=15,
            )
        )

    # Merged: project + chatroom + routing + lineage → 1 fragment (was 6)
    overview_fragment = _build_project_chat_overview_fragment(project, chatroom, source_chatroom)
    if overview_fragment is not None:
        fragments.append(overview_fragment)

    run_text = _run_context(run)
    if run_text:
        fragments.append(
            ContextFragment(
                role="user",
                content=run_text,
                scope=ContextScope.RUN,
                visibility=ContextVisibility.AGENT,
                source="run_context",
                priority=30,
            )
        )

    if runtime_text:
        fragments.append(
            ContextFragment(
                role="user",
                content=runtime_text,
                scope=ContextScope.RUN,
                visibility=ContextVisibility.AGENT,
                source="runtime_context",
                priority=35,
            )
        )

    team_lines = [line for line in (team_members or []) if line]
    if team_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Team Members\n" + "\n".join(team_lines),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.AGENT,
                source="team_members",
                priority=40,
            )
        )

    messages = list(inter_agent_messages or [])
    if messages:
        lines = []
        for item in messages:
            sender = item.get("from_agent") or item.get("from") or "unknown"
            content = item.get("content") or ""
            if content:
                lines.append(f"- From {sender}: {content}")
        if lines:
            fragments.append(
                ContextFragment(
                    role="user",
                    content="## Inter-Agent Messages\n" + "\n".join(lines),
                    scope=ContextScope.SHARED_FACT,
                    visibility=ContextVisibility.AGENT,
                    source="inter_agent_messages",
                    priority=45,
                )
            )

    memory_lines = [line for line in (memories or []) if line]
    if memory_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Relevant Memories\n" + "\n".join(memory_lines),
                scope=ContextScope.AGENT_PRIVATE,
                visibility=ContextVisibility.AGENT,
                source="memory_context",
                priority=50,
            )
        )

    if previous_work_text:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Previous Agent Work\n" + previous_work_text[:1500],
                scope=ContextScope.TURN,
                visibility=ContextVisibility.AGENT,
                source="previous_agent_work",
                priority=55,
            )
        )

    return fragments


def build_runtime_user_context(
    *,
    runtime_context: str = "",
    project: Any = None,
    chatroom: Any = None,
    source_chatroom: Any = None,
    run: Any = None,
    standalone_note: str = "",
    team_members: Optional[Iterable[str]] = None,
    memories: Optional[Iterable[str]] = None,
    inter_agent_messages: Optional[Iterable[Mapping[str, Any]]] = None,
    extra_context: str = "",
) -> Optional[ContextFragment]:
    fragments = build_runtime_user_fragments(
        runtime_context=runtime_context,
        project=project,
        chatroom=chatroom,
        source_chatroom=source_chatroom,
        run=run,
        standalone_note=standalone_note,
        team_members=team_members,
        memories=memories,
        inter_agent_messages=inter_agent_messages,
        extra_context=extra_context,
    )
    if not fragments:
        return None
    return ContextFragment(
        role="user",
        content="\n\n".join(fragment.content for fragment in fragments),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="runtime_user_context",
        priority=60,
    )


def build_stage_summaries_fragment(
    completed_stages: list[Any],
) -> Optional[ContextFragment]:
    """Build a fragment containing summaries of completed pipeline stages.

    ADR-028 Phase 4: Cross-stage summary compression.
    Downstream agents see compact stage summaries instead of full conversation history.
    """
    summaries: list[str] = []
    for stage in completed_stages:
        output = _clean_text(getattr(stage, "output_summary", None))
        stage_name = getattr(stage, "display_name", None) or getattr(stage, "stage_name", "unknown")
        agent_name = getattr(stage, "agent_name", "unknown")
        if output:
            summaries.append(f"### {stage_name} (by {agent_name})\n{output}")

    if not summaries:
        return None

    return ContextFragment(
        role="user",
        content="## Pipeline Stage Summaries\n\n" + "\n\n".join(summaries),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.AGENT,
        source="stage_summaries",
        priority=35,
    )


async def summarize_for_context(text: str, max_tokens: int = 200) -> str:
    """Use a lightweight LLM to compress text for context injection.

    ADR-028 Phase 5: LLM-assisted summarization.
    Falls back to simple truncation on error.
    """
    if not text or not text.strip():
        return ""
    try:
        from llm.client import get_default_llm_client
        client = get_default_llm_client()
        response = await client.chat(
            [
                {"role": "system", "content": "Summarize the following text concisely. Keep key decisions, artifacts, and metrics."},
                {"role": "user", "content": text},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return response.strip() if response else text[:max_tokens * 4]
    except Exception:
        # Fallback: simple truncation
        char_limit = max_tokens * 4
        if len(text) > char_limit:
            return text[:char_limit] + "..."
        return text


def build_recent_history(
    recent_messages: Iterable[Any],
    *,
    limit: int,
    visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
    summarize_threshold: int | None = None,
) -> list[dict[str, Any]]:
    """Build recent history messages with optional progressive compression.

    ADR-028 Phase 2: Three-tier history compression.
    - Last `limit` messages: fully preserved
    - `limit` to `summarize_threshold`: compressed to 1-line summaries
    - Older: excluded (handled by build_history_summary_fragment)
    """
    all_messages = list(recent_messages or [])
    if summarize_threshold and summarize_threshold > limit:
        # Three-tier: full → compressed → excluded
        full_start = max(0, len(all_messages) - limit)
        compress_start = max(0, len(all_messages) - summarize_threshold)
        messages_to_process = all_messages[compress_start:]  # include compressed tier
    else:
        # Two-tier: full → excluded (original behavior)
        full_start = max(0, len(all_messages) - limit)
        messages_to_process = all_messages[full_start:]

    output: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages_to_process):
        is_full_tier = idx >= (len(messages_to_process) - limit)
        agent_name = _message_agent_name(msg)
        content = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        if not content:
            continue
        message_type = getattr(msg, "message_type", "") if not isinstance(msg, dict) else msg.get("message_type", "")
        metadata = _message_metadata(msg)

        # Compressed tier: compress to 1-line summary (ADR-028 Phase 2)
        if not is_full_tier:
            compact = _trim_context_value(content, limit=120)
            if message_type in {"tool_result", "tool"}:
                output.append({"role": "tool", "content": f"[tool result: {compact}]"})
            elif message_type == "user" or not agent_name:
                output.append({"role": "user", "content": compact})
            elif visibility != "target" or not target_agent_name or agent_name == target_agent_name:
                speaker = f"[{agent_name}]" if prefix_assistant_name else agent_name
                output.append({"role": "assistant", "content": f"{speaker}: {compact}"})
            continue

        # Full tier: preserve complete message
        if message_type in {"tool_result", "tool"}:
            tool_call_id = metadata.get("tool_call_id")
            tool_message = {"role": "tool", "content": content}
            if isinstance(tool_call_id, str) and tool_call_id.strip():
                tool_message["tool_call_id"] = tool_call_id.strip()
            output.append(tool_message)
            continue
        if message_type == "user" or not agent_name:
            output.append({"role": "user", "content": content})
            continue
        if visibility == "target" and target_agent_name and agent_name != target_agent_name:
            continue
        assistant_content = f"[{agent_name}]: {content}" if prefix_assistant_name else content
        output.append({"role": "assistant", "content": assistant_content})
    return output


def build_history_summary_fragment(
    recent_messages: Iterable[Any],
    *,
    keep_last: int,
    visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
    max_items: int = 8,
) -> Optional[ContextFragment]:
    all_messages = list(recent_messages or [])
    if len(all_messages) <= keep_last:
        return None

    older_messages = all_messages[:-keep_last]
    summary_lines: list[str] = []
    for msg in older_messages[-max_items:]:
        agent_name = _message_agent_name(msg)
        content = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        if not content:
            continue
        compact_content = _trim_context_value(content, limit=180)
        message_type = getattr(msg, "message_type", "") if not isinstance(msg, dict) else msg.get("message_type", "")
        if message_type == "user" or not agent_name:
            summary_lines.append(f"- User: {compact_content}")
            continue
        if visibility == "target" and target_agent_name and agent_name != target_agent_name:
            continue
        speaker = f"[{agent_name}]" if prefix_assistant_name else agent_name
        summary_lines.append(f"- {speaker}: {compact_content}")

    if not summary_lines:
        return None

    if len(older_messages) > max_items:
        overflow = len(older_messages) - max_items
        summary_lines.insert(0, f"- {overflow} earlier message(s) omitted from this summary.")

    return ContextFragment(
        role="user",
        content="## Earlier Conversation Summary\n" + "\n".join(summary_lines),
        scope=ContextScope.TURN,
        visibility=ContextVisibility.AGENT,
        source="history_summary",
        priority=42,
    )


def assemble_messages(
    *,
    base_system_prompt: str,
    developer_fragments: Optional[Iterable[Optional[ContextFragment]]] = None,
    user_fragments: Optional[Iterable[Optional[ContextFragment]]] = None,
    history_messages: Optional[Iterable[dict[str, Any]]] = None,
    current_input_messages: Optional[Iterable[dict[str, Any]]] = None,
    selector: Optional[ContextSelector] = None,
    developer_role_supported: bool = True,
) -> PromptAssembly:
    selector = selector or ContextSelector()
    selected_developer, selected_user, selector_diagnostics = selector.select_for_assembly_with_report(
        developer_fragments=developer_fragments,
        user_fragments=user_fragments,
    )
    developer = [fragment.to_message() for fragment in selected_developer]
    user_context = [fragment.to_message() for fragment in selected_user]
    system_content = base_system_prompt or "You are a helpful assistant."

    if not developer_role_supported and developer:
        developer_text = "\n\n".join(message["content"] for message in developer if message.get("content"))
        if developer_text:
            system_content = f"{system_content}\n\n## Developer Context\n{developer_text}"
        developer = []

    selector_diagnostics["prompt"] = _prompt_diagnostics_payload(
        system_message={"role": "system", "content": system_content},
        developer_fragments=selected_developer,
        user_fragments=selected_user,
        history_messages=history_messages,
        current_input_messages=current_input_messages,
    )

    return PromptAssembly(
        system_message={"role": "system", "content": system_content},
        developer_messages=developer,
        user_context_messages=user_context,
        history_messages=list(history_messages or []),
        current_input_messages=list(current_input_messages or []),
        selector_diagnostics=selector_diagnostics,
    )


@dataclass
class SelectorRoleReport:
    role: str
    candidate_count: int = 0
    selected_count: int = 0
    dropped_count: int = 0
    truncated_count: int = 0
    candidate_tokens: int = 0
    selected_tokens: int = 0
    dropped_sources: list[str] = field(default_factory=list)
    truncated_sources: list[str] = field(default_factory=list)

    @property
    def compaction_applied(self) -> bool:
        return self.dropped_count > 0 or self.truncated_count > 0

    def record_candidate(self, fragment: ContextFragment) -> None:
        self.candidate_count += 1
        self.candidate_tokens += estimate_text_tokens(fragment.content)

    def record_selected(self, fragment: ContextFragment) -> None:
        self.selected_count += 1
        self.selected_tokens += estimate_text_tokens(fragment.content)

    def record_dropped(self, fragment: ContextFragment) -> None:
        self.dropped_count += 1
        if fragment.source not in self.dropped_sources:
            self.dropped_sources.append(fragment.source)

    def record_truncated(self, original: ContextFragment, truncated: ContextFragment) -> None:
        if truncated.content == original.content:
            return
        self.truncated_count += 1
        if original.source not in self.truncated_sources:
            self.truncated_sources.append(original.source)


@dataclass
class SelectorScopeReport:
    scope: str
    candidate_count: int = 0
    selected_count: int = 0
    candidate_tokens: int = 0
    selected_tokens: int = 0

    def record_candidate(self, fragment: ContextFragment) -> None:
        self.candidate_count += 1
        self.candidate_tokens += estimate_text_tokens(fragment.content)

    def record_selected(self, fragment: ContextFragment) -> None:
        self.selected_count += 1
        self.selected_tokens += estimate_text_tokens(fragment.content)


def _selector_diagnostics_payload(
    *,
    selector: ContextSelector,
    role_reports: Mapping[int, SelectorRoleReport],
    scope_reports: Mapping[str, SelectorScopeReport],
    compaction_applied: bool,
) -> dict[str, Any]:
    developer_report = role_reports.get(0, SelectorRoleReport(role="developer"))
    user_report = role_reports.get(1, SelectorRoleReport(role="user"))
    ordered_scope_reports = {
        scope: asdict(report)
        for scope, report in sorted(scope_reports.items(), key=lambda item: (_scope_rank(item[0]), item[0]))
    }
    return {
        "compacted": compaction_applied,
        "reasons": _selector_compaction_reasons(
            selector=selector,
            developer_report=developer_report,
            user_report=user_report,
            scope_reports=ordered_scope_reports,
        ),
        "selector": {
            "max_fragments": selector.max_fragments,
            "max_tokens": selector.max_tokens,
            "context_window": selector.context_window,
            "input_window": selector.input_window,
            "reserved_completion_tokens": selector.reserved_completion_tokens,
            "prompt_overhead_tokens": selector.prompt_overhead_tokens,
            "static_tokens": selector.static_tokens,
            "usage_band": _selector_usage_band(
                selected_tokens=developer_report.selected_tokens + user_report.selected_tokens,
                selector=selector,
            ),
            "max_tokens_by_role": dict(selector.max_tokens_by_role or {}),
            "max_tokens_by_scope": dict(selector.max_tokens_by_scope or {}),
            "truncate_to_budget": selector.truncate_to_budget,
        },
        "developer": asdict(developer_report),
        "user": asdict(user_report),
        "summary": {
            "candidate_count": developer_report.candidate_count + user_report.candidate_count,
            "selected_count": developer_report.selected_count + user_report.selected_count,
            "dropped_count": developer_report.dropped_count + user_report.dropped_count,
            "truncated_count": developer_report.truncated_count + user_report.truncated_count,
            "candidate_tokens": developer_report.candidate_tokens + user_report.candidate_tokens,
            "selected_tokens": developer_report.selected_tokens + user_report.selected_tokens,
            "by_scope": ordered_scope_reports,
        },
    }


def _selector_compaction_reasons(
    *,
    selector: ContextSelector,
    developer_report: SelectorRoleReport,
    user_report: SelectorRoleReport,
    scope_reports: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    total_candidate_count = developer_report.candidate_count + user_report.candidate_count
    total_selected_count = developer_report.selected_count + user_report.selected_count
    total_candidate_tokens = developer_report.candidate_tokens + user_report.candidate_tokens
    total_selected_tokens = developer_report.selected_tokens + user_report.selected_tokens

    if selector.max_fragments is not None and total_candidate_count > selector.max_fragments:
        reasons.append(
            {
                "kind": "max_fragments",
                "limit": selector.max_fragments,
                "candidate": total_candidate_count,
                "selected": total_selected_count,
            }
        )

    if selector.max_tokens is not None and total_candidate_tokens > selector.max_tokens:
        reasons.append(
            {
                "kind": "max_tokens",
                "limit": selector.max_tokens,
                "candidate": total_candidate_tokens,
                "selected": total_selected_tokens,
            }
        )

    if selector.max_tokens_by_role:
        role_reports = {"developer": developer_report, "user": user_report}
        for role, limit in selector.max_tokens_by_role.items():
            report = role_reports.get(role)
            if report is not None and report.candidate_tokens > limit:
                reasons.append(
                    {
                        "kind": "role_tokens",
                        "role": role,
                        "limit": limit,
                        "candidate": report.candidate_tokens,
                        "selected": report.selected_tokens,
                    }
                )

    if selector.max_tokens_by_scope:
        for scope, limit in selector.max_tokens_by_scope.items():
            report = scope_reports.get(scope)
            if isinstance(report, dict) and int(report.get("candidate_tokens") or 0) > limit:
                reasons.append(
                    {
                        "kind": "scope_tokens",
                        "scope": scope,
                        "limit": limit,
                        "candidate": int(report.get("candidate_tokens") or 0),
                        "selected": int(report.get("selected_tokens") or 0),
                    }
                )

    if developer_report.truncated_count or user_report.truncated_count:
        reasons.append(
            {
                "kind": "truncated",
                "count": developer_report.truncated_count + user_report.truncated_count,
                "sources": [*developer_report.truncated_sources, *user_report.truncated_sources],
            }
        )
    if developer_report.dropped_count or user_report.dropped_count:
        reasons.append(
            {
                "kind": "dropped",
                "count": developer_report.dropped_count + user_report.dropped_count,
                "sources": [*developer_report.dropped_sources, *user_report.dropped_sources],
            }
        )
    return reasons


def _selector_usage_band(*, selected_tokens: int, selector: ContextSelector) -> dict[str, Any]:
    input_window = selector.input_window or selector.context_window
    if not input_window:
        return {}
    total_prompt_tokens = selected_tokens + int(selector.static_tokens or 0)
    ratio = total_prompt_tokens / max(input_window, 1)
    if ratio > 0.82:
        band = "red"
    elif ratio > 0.70:
        band = "orange"
    elif ratio > 0.55:
        band = "yellow"
    else:
        band = "green"
    return {
        "band": band,
        "ratio": round(ratio, 6),
        "prompt_tokens": total_prompt_tokens,
        "input_window": input_window,
    }


def _prompt_diagnostics_payload(
    *,
    system_message: dict[str, Any],
    developer_fragments: Iterable[ContextFragment],
    user_fragments: Iterable[ContextFragment],
    history_messages: Optional[Iterable[dict[str, Any]]] = None,
    current_input_messages: Optional[Iterable[dict[str, Any]]] = None,
) -> dict[str, Any]:
    history_list = list(history_messages or [])
    current_input_list = list(current_input_messages or [])
    developer_list = list(developer_fragments or [])
    user_list = list(user_fragments or [])
    developer_messages = [fragment.to_message() for fragment in developer_list]
    user_messages = [fragment.to_message() for fragment in user_list]
    all_messages = [
        system_message,
        *developer_messages,
        *user_messages,
        *history_list,
        *current_input_list,
    ]

    components = {
        "system": _message_collection_size([system_message]),
        "developer": _fragment_collection_size(developer_list),
        "user_context": _fragment_collection_size(user_list),
        "history": _message_collection_size(history_list),
        "current_input": _message_collection_size(current_input_list),
    }
    fragments = [
        _fragment_size_payload(fragment, selected=True)
        for fragment in [*developer_list, *user_list]
    ]
    return {
        "total": _message_collection_size(all_messages),
        "components": components,
        "fragments": fragments,
        "message_count": len(all_messages),
    }


def _fragment_collection_size(fragments: Iterable[ContextFragment]) -> dict[str, Any]:
    fragment_list = list(fragments or [])
    content = "\n\n".join(fragment.content for fragment in fragment_list)
    return {
        **_text_size_payload(content),
        "fragment_count": len(fragment_list),
    }


def _message_collection_size(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    message_list = list(messages or [])
    return {
        "bytes": _json_bytes(message_list),
        "tokens": estimate_messages_tokens(message_list),
        "message_count": len(message_list),
    }


def _fragment_size_payload(fragment: ContextFragment, *, selected: bool) -> dict[str, Any]:
    return {
        "role": fragment.role,
        "scope": fragment.scope,
        "visibility": fragment.visibility,
        "source": fragment.source,
        "priority": fragment.priority,
        "selected": selected,
        **_text_size_payload(fragment.content),
    }


def _text_size_payload(value: Any) -> dict[str, int]:
    text = _clean_text(value)
    return {
        "chars": len(text),
        "bytes": len(text.encode("utf-8")),
        "tokens": estimate_text_tokens(text),
    }


def _json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except TypeError:
        return len(str(value).encode("utf-8"))


def _scope_rank(scope: str) -> int:
    return {
        ContextScope.SESSION: 10,
        ContextScope.RUN: 20,
        ContextScope.STAGE: 30,
        ContextScope.SHARED_FACT: 35,
        ContextScope.TURN: 40,
        ContextScope.AGENT_PRIVATE: 50,
    }.get(scope, 999)


def _visibility_rank(visibility: str) -> int:
    return {
        ContextVisibility.GLOBAL: 10,
        ContextVisibility.ROLE: 20,
        ContextVisibility.AGENT: 30,
        ContextVisibility.STAGE: 40,
        ContextVisibility.PRIVATE: 50,
    }.get(visibility, 999)


def _fragment_sort_key(fragment: ContextFragment, *, role_order: int = 0) -> tuple[int, int, int, int, str]:
    return (
        fragment.priority,
        role_order,
        _scope_rank(fragment.scope),
        _visibility_rank(fragment.visibility),
        fragment.source,
    )


def _agent_data(agent_config: Any) -> dict[str, Any]:
    if isinstance(agent_config, Mapping):
        data = dict(agent_config)
        config = data.get("config")
        if isinstance(config, str):
            try:
                config = json.loads(config or "{}")
            except (TypeError, json.JSONDecodeError):
                config = {}
        if isinstance(config, Mapping):
            data.setdefault("role", config.get("role") or data.get("role"))
            data.setdefault("soul", config.get("soul") or data.get("soul"))
        if isinstance(data.get("soul"), str):
            try:
                data["soul"] = json.loads(data.get("soul") or "{}")
            except (TypeError, json.JSONDecodeError):
                data["soul"] = {}
        return data
    data: dict[str, Any] = {
        "name": getattr(agent_config, "name", ""),
        "role": getattr(agent_config, "role", ""),
        "soul": getattr(agent_config, "soul", {}),
    }
    config = getattr(agent_config, "config", None)
    if isinstance(config, str):
        try:
            config = json.loads(config or "{}")
        except (TypeError, json.JSONDecodeError):
            config = {}
    if isinstance(config, Mapping):
        data["role"] = config.get("role") or data["role"]
        data["soul"] = config.get("soul") or data["soul"]
    if isinstance(data["soul"], str):
        try:
            data["soul"] = json.loads(data["soul"] or "{}")
        except (TypeError, json.JSONDecodeError):
            data["soul"] = {}
    return data


def _role_title(role: Any) -> str:
    if isinstance(role, Mapping):
        return str(role.get("title") or "")
    title = getattr(role, "title", None)
    if title and not isinstance(role, str):
        return str(title)
    return str(role or "")


def _role_lists(role: Any) -> tuple[list[str], list[str]]:
    if isinstance(role, Mapping):
        return list(role.get("responsibilities") or []), list(role.get("rules") or [])
    return list(getattr(role, "responsibilities", []) or []), list(getattr(role, "rules", []) or [])


def _skill_context(
    *,
    active_skills: Optional[Iterable[str]],
    skills_config: Optional[Mapping[str, Mapping[str, Any]]],
    agent_skills: Optional[Iterable[str]],
) -> str:
    if not skills_config or not agent_skills:
        return ""
    agent_skill_names = [str(name) for name in agent_skills]
    active = {str(name) for name in (active_skills or [])}

    hints = []
    guides = []
    for skill_name in agent_skill_names:
        skill = skills_config.get(skill_name, {})
        levels = skill.get("levels", {}) if isinstance(skill, Mapping) else {}
        hint = str(levels.get("hint") or "").strip()
        if hint:
            hints.append(hint)
        if skill_name in active:
            guide = str(levels.get("guide") or "").strip()
            if guide:
                guides.append(guide)

    parts = []
    if hints:
        parts.append(
            "## Available Skills\n"
            + "\n".join(f"- {hint}" for hint in hints)
            + "\n\nFull skill docs live at `.catown/skills/<skill-id>/SKILL.md`; read them when deeper detail is needed."
        )
    if guides:
        parts.append("## Active Skill Guides\n" + "\n\n".join(guides))
    return "\n\n".join(parts)


def _project_context_fragments(project: Any) -> list[ContextFragment]:
    if project is None:
        return []

    fragments: list[ContextFragment] = []
    identity_lines = _context_lines(
        project,
        [
            ("Project ID", "id", 120),
            ("Name", "name", 240),
            ("Description", "description", 800),
            ("Workspace path", "workspace_path", 260),
        ],
    )
    if identity_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Current Project\n" + "\n".join(identity_lines),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.GLOBAL,
                source="project_context",
                priority=20,
            )
        )

    strategy_lines = _context_lines(
        project,
        [
            ("Vision", "one_line_vision", 320),
            ("Primary outcome", "primary_outcome", 320),
        ],
    )
    if strategy_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Project Strategy\n" + "\n".join(strategy_lines),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.GLOBAL,
                source="project_strategy",
                priority=22,
            )
        )

    status_lines = _context_lines(
        project,
        [
            ("Status", "status", 160),
            ("Current stage", "current_stage", 200),
            ("Execution mode", "execution_mode", 160),
            ("Health status", "health_status", 160),
        ],
    )
    if status_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Project Delivery State\n" + "\n".join(status_lines),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.AGENT,
                source="project_status",
                priority=24,
            )
        )

    return fragments


def _chatroom_context_fragments(chatroom: Any, *, project: Any = None, source_chatroom: Any = None) -> list[ContextFragment]:
    if chatroom is None:
        return []

    fragments: list[ContextFragment] = []
    chat_lines = _context_lines(
        chatroom,
        [
            ("Chat ID", "id", 120),
            ("Title", "title", 400),
            ("Session type", "session_type", 120),
            ("Message visibility", "message_visibility", 120),
        ],
    )
    visible_in_list = getattr(chatroom, "is_visible_in_chat_list", None)
    if visible_in_list is not None:
        chat_lines.append(f"- Visible in chat list: {'yes' if visible_in_list else 'no'}")
    if chat_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Current Chat\n" + "\n".join(chat_lines),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.GLOBAL,
                source="chatroom_context",
                priority=25,
            )
        )
        fragments.append(
            ContextFragment(
                role="user",
                content=(
                    "## Chat Routing\n"
                    "- Messages in this chat are shared conversation events.\n"
                    "- Agents can send a lightweight message to another agent by starting the last non-empty paragraph of the final chat message with one or more `@agent_name` mentions.\n"
                    "- Mentions outside that final paragraph are treated as normal text, not routing instructions.\n"
                    "- Use chat mentions for agent-to-agent notifications or context handoffs; use tracked task tools only when the work needs task tracking."
                ),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.GLOBAL,
                source="chat_routing",
                priority=26,
            )
        )

    lineage_lines: list[str] = []
    chat_role = _chat_role(chatroom, project)
    if chat_role:
        lineage_lines.append(f"- Chat role: {chat_role}")
    if source_chatroom is not None:
        source_id = getattr(source_chatroom, "id", None)
        source_title = getattr(source_chatroom, "title", None)
        if source_id or source_title:
            source_label = f"#{source_id} {source_title or 'New Chat'}".strip()
            lineage_lines.append(f"- Source chat: {source_label}")
    if lineage_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Chat Lineage\n" + "\n".join(lineage_lines),
                scope=ContextScope.RUN,
                visibility=ContextVisibility.AGENT,
                source="chatroom_lineage",
                priority=27,
            )
        )

    return fragments


def _build_project_chat_overview_fragment(
    project: Any,
    chatroom: Any,
    source_chatroom: Any = None,
) -> Optional[ContextFragment]:
    """Merge project context + chatroom context + routing + lineage into one fragment."""

    parts: list[str] = []

    # Project identity + strategy + status
    if project is not None:
        identity_lines = _context_lines(
            project,
            [
                ("Project ID", "id", 120),
                ("Name", "name", 240),
                ("Description", "description", 800),
                ("Workspace path", "workspace_path", 260),
            ],
        )
        if identity_lines:
            parts.append("## Current Project\n" + "\n".join(identity_lines))

        strategy_lines = _context_lines(
            project,
            [
                ("Vision", "one_line_vision", 320),
                ("Primary outcome", "primary_outcome", 320),
            ],
        )
        if strategy_lines:
            parts.append("## Project Strategy\n" + "\n".join(strategy_lines))

        status_lines = _context_lines(
            project,
            [
                ("Status", "status", 160),
                ("Current stage", "current_stage", 200),
                ("Execution mode", "execution_mode", 160),
                ("Health status", "health_status", 160),
            ],
        )
        if status_lines:
            parts.append("## Project Delivery State\n" + "\n".join(status_lines))

    # Chatroom identity
    if chatroom is not None:
        chat_lines = _context_lines(
            chatroom,
            [
                ("Chat ID", "id", 120),
                ("Title", "title", 400),
                ("Session type", "session_type", 120),
                ("Message visibility", "message_visibility", 120),
            ],
        )
        visible_in_list = getattr(chatroom, "is_visible_in_chat_list", None)
        if visible_in_list is not None:
            chat_lines.append(f"- Visible in chat list: {'yes' if visible_in_list else 'no'}")
        if chat_lines:
            parts.append("## Current Chat\n" + "\n".join(chat_lines))

        # Chat routing (compact)
        parts.append(
            "## Chat Routing\n"
            "- Messages are shared conversation events.\n"
            "- Agent-to-agent: start the last non-empty paragraph with `@agent_name` mentions to trigger routing.\n"
            "- Use chat mentions for notifications/handoffs; use tracked task tools for work that needs tracking."
        )

        # Chat lineage
        lineage_lines: list[str] = []
        chat_role = _chat_role(chatroom, project)
        if chat_role:
            lineage_lines.append(f"- Chat role: {chat_role}")
        if source_chatroom is not None:
            source_id = getattr(source_chatroom, "id", None)
            source_title = getattr(source_chatroom, "title", None)
            if source_id or source_title:
                source_label = f"#{source_id} {source_title or 'New Chat'}".strip()
                lineage_lines.append(f"- Source chat: {source_label}")
        if lineage_lines:
            parts.append("## Chat Lineage\n" + "\n".join(lineage_lines))

    if not parts:
        return None

    return ContextFragment(
        role="user",
        content="\n\n".join(parts),
        scope=ContextScope.RUN,
        visibility=ContextVisibility.GLOBAL,
        source="project_chat_overview",
        priority=20,
    )


def _run_context(run: Any) -> str:
    if run is None:
        return ""
    lines = []
    for label, attr in [
        ("Run ID", "id"),
        ("Workspace", "workspace_path"),
        ("Input requirement", "input_requirement"),
    ]:
        value = getattr(run, attr, None)
        if value:
            lines.append(f"- {label}: {str(value)[:1200]}")
    return "## Current Pipeline Run\n" + "\n".join(lines) if lines else ""


def _message_agent_name(message: Any) -> Optional[str]:
    if isinstance(message, Mapping):
        value = message.get("agent_name")
    else:
        value = getattr(message, "agent_name", None)
    return value if isinstance(value, str) and value else None


def _message_metadata(message: Any) -> dict[str, Any]:
    if isinstance(message, Mapping):
        value = message.get("metadata")
        if isinstance(value, Mapping):
            return dict(value)
        return {}
    value = getattr(message, "metadata", None)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(message, "metadata_json"):
        try:
            parsed = json.loads(getattr(message, "metadata_json") or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _context_lines(subject: Any, fields: Iterable[tuple[str, str, int]]) -> list[str]:
    lines: list[str] = []
    for label, attr, limit in fields:
        value = getattr(subject, attr, None)
        text = _trim_context_value(value, limit=limit)
        if text:
            lines.append(f"- {label}: {text}")
    return lines


def _chat_role(chatroom: Any, project: Any) -> str:
    if chatroom is None:
        return ""
    if project is None:
        return "standalone chat"
    default_chatroom_id = getattr(project, "default_chatroom_id", None)
    chatroom_id = getattr(chatroom, "id", None)
    source_chatroom_id = getattr(chatroom, "source_chatroom_id", None)
    if default_chatroom_id and chatroom_id == default_chatroom_id:
        return "project main chat"
    if default_chatroom_id and source_chatroom_id == default_chatroom_id:
        return "project subchat"
    return "project-linked chat"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _trim_context_value(value: Any, *, limit: int = 600) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def estimate_text_tokens(value: Any) -> int:
    text = _clean_text(value)
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4) + non_ascii_chars)


def estimate_messages_tokens(messages: Optional[Iterable[dict[str, Any]]]) -> int:
    total = 0
    for message in messages or []:
        if not isinstance(message, Mapping):
            continue
        total += 6
        total += estimate_text_tokens(message.get("role"))
        content = message.get("content")
        if isinstance(content, list):
            for chunk in content:
                if not isinstance(chunk, Mapping):
                    total += estimate_text_tokens(chunk)
                    continue
                total += estimate_text_tokens(chunk.get("type"))
                total += estimate_text_tokens(chunk.get("text"))
        else:
            total += estimate_text_tokens(content)
        total += estimate_text_tokens(message.get("name"))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            try:
                total += estimate_text_tokens(json.dumps(tool_calls, ensure_ascii=False))
            except TypeError:
                total += estimate_text_tokens(tool_calls)
    return total


def _default_completion_reserve(context_window: int) -> int:
    return max(1024, min(128000, context_window // 8))


def _truncate_text_to_token_budget(text: str, max_tokens: int) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    if estimate_text_tokens(cleaned) <= max_tokens:
        return cleaned

    marker = "\n[truncated for token budget]"
    marker_tokens = estimate_text_tokens(marker)
    if max_tokens <= marker_tokens:
        return ""

    target_tokens = max_tokens - marker_tokens
    lines = cleaned.splitlines()
    kept_lines: list[str] = []
    used_tokens = 0
    for line in lines:
        candidate = line.rstrip()
        if not candidate:
            continue
        line_tokens = estimate_text_tokens(candidate) + (1 if kept_lines else 0)
        if used_tokens + line_tokens > target_tokens:
            break
        kept_lines.append(candidate)
        used_tokens += line_tokens

    trimmed = "\n".join(kept_lines).strip()
    if not trimmed:
        full_tokens = estimate_text_tokens(cleaned)
        if full_tokens <= 0:
            return ""
        ratio = target_tokens / full_tokens
        char_budget = max(24, int(len(cleaned) * ratio))
        trimmed = cleaned[:char_budget].rstrip()

    if not trimmed:
        return ""
    return f"{trimmed}{marker}"
