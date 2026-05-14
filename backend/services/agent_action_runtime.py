"""Shared execution helpers for high-level agent collaboration actions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from agents.identity import normalize_agent_type
from config import settings
from services.agent_lifecycle_runtime import (
    delegate_runtime_collaboration_task,
    get_runtime_chatroom_collaborators,
    get_runtime_collaboration_task_status_text,
    get_runtime_invitable_agents,
    invite_runtime_agent_to_chatroom,
    resolve_runtime_query_target,
    send_runtime_collaboration_broadcast,
    send_runtime_collaboration_direct_message,
)
from services.context_builder import (
    assemble_messages,
    build_history_summary_fragment,
    build_base_system_prompt,
    build_operating_developer_context,
    build_recent_history,
    build_runtime_user_fragments,
    build_stage_developer_context,
)
from services.task_state import build_task_state, build_task_state_fragments
from skills import load_skill_registry
from services.chat_prompt_builder import build_chat_context_selector


def _open_db_session() -> tuple[Any, Any]:
    from models.database import get_db

    db = next(get_db())
    return db, getattr(db, "close", lambda: None)


def _available_collaborator_names(coordinator: Any | None) -> list[str]:
    if coordinator is None:
        return []
    return [collaborator.agent_name for collaborator in coordinator.collaborators.values()]


def _delegate_target_not_found(target_agent_name: str, coordinator: Any | None) -> str:
    normalized_target = normalize_agent_type(target_agent_name)
    return (
        f"[Delegate Task] Error: Agent '{normalized_target}' not found. "
        f"Available agents: {_available_collaborator_names(coordinator)}"
    )


def _direct_target_not_found(target_agent_name: str) -> str:
    normalized_target = normalize_agent_type(target_agent_name)
    return f"[Direct Message] Error: Agent '{normalized_target}' not found"


def _query_target_not_found(target_agent_type: str, available: list[str]) -> str:
    return (
        f"[consult_agent] Error: Agent '{target_agent_type}' not found in this room. "
        f"Agents in this room: {available}"
    )


def _query_target_not_in_room(target_agent_type: str) -> str:
    return (
        f"[consult_agent] Error: Agent '{target_agent_type}' is not in this room. "
        f"Use @mention to invite them first."
    )


async def run_delegate_agent_action(
    *,
    coordinator: Any | None,
    target_agent_name: str,
    task_title: str,
    task_description: str,
    context: str,
    current_agent_id: int,
    current_agent_name: str,
    chatroom_id: int,
    store_runtime_card_fn: Callable[[int, dict[str, Any]], Awaitable[Any]] | None = None,
    send_message_fn: Callable[..., Awaitable[Any]] | None = None,
    publish_saved_chat_message_fn: Callable[..., Awaitable[Any]] | None = None,
    trigger_agent_response_fn: Callable[..., Awaitable[Any]] | None = None,
    create_task_fn: Callable[[Awaitable[Any]], Any] | None = None,
) -> str:
    """Run one delegated collaboration action and return the tool-facing status text."""

    if coordinator is None:
        return _delegate_target_not_found(target_agent_name, None)

    db, close_db = _open_db_session()
    try:
        try:
            _, result_text = await delegate_runtime_collaboration_task(
                db=db,
                target_agent_name=target_agent_name,
                task_title=task_title,
                task_description=task_description,
                chatroom_id=chatroom_id,
                created_by_agent_id=current_agent_id,
                current_agent_name=current_agent_name,
                context=context,
                start_execution=True,
                store_runtime_card_fn=store_runtime_card_fn,
                send_message_fn=send_message_fn,
                publish_saved_chat_message_fn=publish_saved_chat_message_fn,
                trigger_agent_response_fn=trigger_agent_response_fn,
                create_task_fn=create_task_fn or asyncio.create_task,
                coordinator=coordinator,
            )
            return result_text
        except Exception:
            return _delegate_target_not_found(target_agent_name, coordinator)
    finally:
        close_db()


async def run_broadcast_agent_action(
    *,
    coordinator: Any | None,
    message: str,
    current_agent_id: int,
    current_agent_name: str,
    chatroom_id: int,
) -> str:
    """Run one broadcast action and return the tool-facing status text."""

    if coordinator is None:
        return "[Broadcast] Error: Collaboration coordinator not available"

    return await send_runtime_collaboration_broadcast(
        from_agent_id=current_agent_id,
        from_agent_name=current_agent_name,
        chatroom_id=chatroom_id,
        content=message,
        coordinator=coordinator,
    )


def run_check_task_status_action(*, coordinator: Any | None, task_id: str) -> str:
    """Return the tool-facing status text for one delegated task."""

    if coordinator is None:
        return "[Check Task] Error: Collaboration coordinator not available"

    result_text = get_runtime_collaboration_task_status_text(task_id, coordinator=coordinator)
    if not result_text:
        return f"[Check Task] Error: Task '{task_id}' not found"
    return result_text


def run_list_collaborators_action(*, coordinator: Any | None, chatroom_id: int) -> str:
    """Return a collaborator listing plus selection guidance for one chatroom."""

    if coordinator is None:
        return "[List Collaborators] Error: Collaboration coordinator not available"

    db, close_db = _open_db_session()
    try:
        summary = get_runtime_chatroom_collaborators(
            db=db,
            chatroom_id=chatroom_id,
            coordinator=coordinator,
        )
    finally:
        close_db()

    collaborators = summary.get("collaborators") or []
    if not collaborators:
        return "[List Collaborators] No other agents available in this chatroom"

    room_label = "chatroom" if summary.get("source") == "coordinator" else "this room"
    result = f"[List Collaborators] {len(collaborators)} agent(s) in {room_label}:\n"
    for collaborator in collaborators:
        if summary.get("source") == "coordinator":
            result += (
                f"  - **{collaborator['agent_name']}** (ID: {collaborator['agent_id']}): "
                f"{collaborator['status']}, {collaborator['pending_tasks']} pending tasks\n"
            )
        else:
            result += (
                f"  - **{collaborator['agent_name']}** "
                f"(role: {collaborator['role']}, tools: {collaborator['tools']})\n"
            )

    result += (
        "\nSelection guide:\n"
        "- Use delegate_task for tracked async work.\n"
        "- Use consult_agent for an immediate expert answer.\n"
        "- Use send_direct_message for notification-only delivery.\n"
        "- Use @agent_name in normal chat when you want a lightweight live handoff."
    )
    return result


async def run_direct_message_agent_action(
    *,
    coordinator: Any | None,
    target_agent_name: str,
    message: str,
    current_agent_id: int,
    current_agent_name: str,
    chatroom_id: int,
) -> str:
    """Run one direct-message action and return the tool-facing status text."""

    if coordinator is None:
        return "[Direct Message] Error: Collaboration coordinator not available"

    db, close_db = _open_db_session()
    try:
        try:
            return await send_runtime_collaboration_direct_message(
                db=db,
                target_agent_name=target_agent_name,
                from_agent_id=current_agent_id,
                from_agent_name=current_agent_name,
                chatroom_id=chatroom_id,
                content=message,
                coordinator=coordinator,
            )
        except Exception:
            return _direct_target_not_found(target_agent_name)
    finally:
        close_db()


def run_list_agents_action(*, chatroom_id: int) -> str:
    """Return agents that can be invited into one chatroom."""

    db, close_db = _open_db_session()
    try:
        summary = get_runtime_invitable_agents(db=db, chatroom_id=chatroom_id)
    finally:
        close_db()

    if summary.get("error"):
        return f"[Directory] Error: {summary['error']}"
    external_agents = summary.get("agents") or []
    if not external_agents:
        return "[Directory] All system agents are already in this room."

    result = f"[Directory] {len(external_agents)} agent(s) available to invite:\n"
    for agent in external_agents:
        result += (
            f"  - **{agent['agent_name']}** "
            f"({agent['display_name']}, role: {agent['role']})\n"
        )
    result += "\nUse invite_agent(agent_name) to add one to this room."
    return result


def run_invite_agent_action(*, agent_name: str, chatroom_id: int) -> str:
    """Invite one agent into a chatroom and return the tool-facing status text."""

    db, close_db = _open_db_session()
    try:
        result = invite_runtime_agent_to_chatroom(
            db=db,
            chatroom_id=chatroom_id,
            agent_name=agent_name,
        )
        return str(result["detail"])
    finally:
        close_db()


def run_consult_agent_preflight_action(
    *,
    target_agent_name: str,
    current_agent_name: str,
    chatroom_id: int,
) -> dict[str, Any]:
    """Resolve one synchronous consultation target and return either an error or the ready runtime state."""

    target_agent_type = normalize_agent_type(target_agent_name)
    current_agent_type = normalize_agent_type(current_agent_name)
    if not target_agent_type:
        return {"ok": False, "error": "[consult_agent] Error: target_agent is required."}
    if target_agent_type == current_agent_type:
        return {
            "ok": False,
            "error": f"[consult_agent] Error: Cannot consult yourself ({target_agent_type}).",
        }

    db, close_db = _open_db_session()
    try:
        target_state = resolve_runtime_query_target(
            db,
            target_agent_name=target_agent_type,
            chatroom_id=chatroom_id,
        )
    finally:
        close_db()

    target_db_agent = target_state["target_db_agent"]
    chatroom = target_state["chatroom"]
    if not target_db_agent:
        return {
            "ok": False,
            "error": _query_target_not_found(target_agent_type, target_state["room_agent_names"]),
        }

    if chatroom and chatroom.project_id and not target_state["is_assigned"]:
        return {"ok": False, "error": _query_target_not_in_room(target_agent_type)}

    return {
        "ok": True,
        "target_agent_type": target_agent_type,
        "current_agent_type": current_agent_type,
        "target_state": target_state,
    }


def run_consult_agent_response_action(
    *,
    db: Any,
    chatroom: Any,
    question: str,
    response: str | None,
    current_agent_type: str,
    target_agent_type: str,
    target_agent_role: str,
    consult_step_id: str | None = None,
) -> str:
    """Finalize one synchronous consultation response, including runtime side effects and formatting."""

    if not response:
        return f"[consult_agent] Agent '{target_agent_type}' returned an empty response."

    try:
        if chatroom and getattr(chatroom, "project_id", None):
            from models.database import Pipeline as PipelineModel, PipelineMessage, PipelineRun

            active_run = (
                db.query(PipelineRun)
                .join(PipelineModel, PipelineRun.pipeline_id == PipelineModel.id)
                .filter(PipelineRun.status == "running")
                .first()
            )
            if active_run:
                db.add(
                    PipelineMessage(
                        run_id=active_run.id,
                        message_type="AGENT_QUESTION",
                        from_agent=current_agent_type,
                        to_agent=target_agent_type,
                        content=f"Q: {question[:500]}\nA: {response[:500]}",
                    )
                )
                db.commit()
    except Exception:
        pass

    step_suffix = f"\n[consult_step_id] {consult_step_id}" if consult_step_id else ""
    return f"[Response from {target_agent_type} ({target_agent_role})]:\n{response}{step_suffix}"


async def build_consult_agent_prompt_state(
    *,
    db: Any,
    target_db_agent: Any,
    chatroom: Any,
    current_agent_name: str,
    question: str,
    include_context: bool,
    recent_messages_loader: Callable[[int, int], Awaitable[list[Any]]] | None = None,
) -> dict[str, Any]:
    """Prepare shared prompt-state inputs for one synchronous consult-agent turn."""

    from models.database import Memory, Project

    project = None
    if include_context and chatroom and getattr(chatroom, "project_id", None):
        project = db.query(Project).filter(Project.id == chatroom.project_id).first()

    own_memories = (
        db.query(Memory)
        .filter(Memory.agent_id == target_db_agent.id)
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
        .limit(5)
        .all()
    )

    recent: list[Any] = []
    chatroom_id = getattr(chatroom, "id", 0) or 0
    if chatroom_id and recent_messages_loader is not None:
        recent = await recent_messages_loader(chatroom_id, 6)

    history_messages = build_recent_history(recent, limit=4)
    history_summary = build_history_summary_fragment(recent, keep_last=4)
    query_input = f"[Query from {current_agent_name}]: {question}"
    current_input_messages: list[dict[str, Any]] = []
    if not (
        history_messages
        and history_messages[-1].get("role") == "user"
        and str(history_messages[-1].get("content") or "").strip() == query_input
    ):
        current_input_messages.append({"role": "user", "content": query_input})

    runtime_note = (
        "## Consultation Context\n"
        f"- Queried by agent: {current_agent_name}\n"
        "- Tools are disabled for this synchronous consultation.\n"
        "- Answer directly from your role, memory, and the shared room context."
    )

    task_fragments = build_task_state_fragments(
        build_task_state(
            project=project if include_context else None,
            current_request=query_input,
        )
    )

    return {
        "project": project,
        "own_memories": own_memories,
        "history_messages": history_messages,
        "history_summary": history_summary,
        "current_input_messages": current_input_messages,
        "query_input": query_input,
        "runtime_note": runtime_note,
        "task_fragments": task_fragments,
    }


def build_consult_agent_prompt_profile(
    *,
    target_db_agent: Any,
    runtime_note: str,
    own_memories: list[Any],
    project: Any,
    chatroom: Any,
    history_summary: Any,
    task_fragments: list[Any],
    include_context: bool,
    skill_id_resolver: Callable[[Any], list[str]],
    memory_line_resolver: Callable[[list[Any]], list[str]],
) -> dict[str, Any]:
    """Build developer and user fragments for one synchronous consult-agent turn."""

    developer_fragments = [
        build_operating_developer_context(
            agent_name=getattr(target_db_agent, "name", "") or "unknown",
            agent_role=getattr(target_db_agent, "role", ""),
        ),
        build_stage_developer_context(
            tools=[],
            skills_config=load_skill_registry(settings.SKILLS_DIR),
            agent_skills=skill_id_resolver(target_db_agent),
            tool_guidance=(
                "Tools are disabled for this query. "
                "Respond directly from your expertise and the visible context."
            ),
        ),
    ]
    user_fragments = [
        *task_fragments,
        *([history_summary] if history_summary is not None else []),
        *build_runtime_user_fragments(
            project=project if include_context else None,
            chatroom=chatroom if include_context else None,
            runtime_context=runtime_note,
            memories=memory_line_resolver(own_memories),
        ),
    ]
    return {
        "developer_fragments": developer_fragments,
        "user_fragments": user_fragments,
    }


def build_consult_agent_messages(
    *,
    target_db_agent: Any,
    model_id: str,
    history_messages: list[dict[str, Any]],
    current_input_messages: list[dict[str, Any]],
    developer_fragments: list[Any],
    user_fragments: list[Any],
    fallback_name_resolver: Callable[[Any], str],
) -> list[dict[str, Any]]:
    """Assemble final messages for one synchronous consult-agent turn."""

    base_system_prompt = build_base_system_prompt(
        target_db_agent,
        fallback_name=fallback_name_resolver(target_db_agent),
        fallback_role=getattr(target_db_agent, "role", "assistant"),
    )
    return assemble_messages(
        base_system_prompt=base_system_prompt,
        developer_fragments=developer_fragments,
        user_fragments=user_fragments,
        history_messages=history_messages,
        current_input_messages=current_input_messages,
        selector=build_chat_context_selector(
            profile="consult_agent",
            agent_name=fallback_name_resolver(target_db_agent),
            model_id=model_id,
            base_system_prompt=base_system_prompt,
            history_messages=history_messages,
            current_input_messages=current_input_messages,
        ),
    ).to_messages()
