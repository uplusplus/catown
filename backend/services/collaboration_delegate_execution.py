"""Shared runtime helpers for delegated collaboration task execution."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable


logger = logging.getLogger("catown.collaboration_delegate_execution")


def mark_delegated_task_run_interrupted(
    *,
    client_turn_id: str,
    agent_name: str,
    summary: str,
) -> None:
    """Mark the delegated task run failed when background execution is interrupted."""

    try:
        from models.database import SessionLocal, TaskRun
        from services.run_ledger import append_task_event, complete_task_run
    except Exception:
        return

    db = SessionLocal()
    try:
        task_run = (
            db.query(TaskRun)
            .filter(TaskRun.client_turn_id == client_turn_id)
            .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            .first()
        )
        if task_run is None or (task_run.status or "").strip().lower() != "running":
            return
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            agent_name=agent_name,
            summary=summary,
            payload={
                "reason": "delegated_task_interrupted",
                "client_turn_id": client_turn_id,
            },
        )
        complete_task_run(db, task_run, status="failed", summary=summary)
    except Exception:
        db.rollback()
    finally:
        db.close()


def delegate_task_card_content(
    task_title: str,
    task_description: str,
    context: str,
    task_id: str,
) -> str:
    """Render the runtime card content for one delegated task."""

    parts = [f"**Task: {task_title}**", task_description.strip()]
    if context.strip():
        parts.append(f"Context: {context.strip()}")
    parts.append(f"Task ID: {task_id}")
    return "\n\n".join(parts)


async def publish_delegate_task_card(
    *,
    chatroom_id: int,
    from_agent: str,
    to_agent: str,
    content: str,
    client_turn_id: str,
    store_runtime_card_fn: Callable[[int, dict[str, Any]], Awaitable[Any]],
) -> None:
    """Publish the delegated task runtime card, ignoring card-store failures."""

    try:
        await store_runtime_card_fn(
            chatroom_id,
            {
                "type": "agent_message",
                "source": "chatroom",
                "from_agent": from_agent,
                "to_agent": to_agent,
                "content": content,
                "client_turn_id": client_turn_id,
            },
        )
    except Exception:
        pass


async def run_delegated_task_in_chat(
    *,
    task: Any,
    coordinator: Any,
    target_agent_type: str,
    task_description: str,
    context: str,
    current_agent_name: str,
    client_turn_id: str,
    task_metadata: dict[str, Any],
    send_message_fn: Callable[..., Awaitable[Any]],
    publish_saved_chat_message_fn: Callable[..., Awaitable[Any]],
    trigger_agent_response_fn: Callable[..., Awaitable[Any]],
    mark_interrupted_fn: Callable[..., None],
) -> None:
    """Run the delegated task through the chat-visible execution path."""

    from agents.collaboration import TaskStatus
    from models.database import Message, SessionLocal

    db = SessionLocal()
    try:
        task.status = TaskStatus.IN_PROGRESS
        coordinator.task_registry[task.id] = task

        instruction = (
            f"@{target_agent_type} [Delegated task from {current_agent_name}] {task_description.strip()}\n\n"
            f"Delegation context:\n{context.strip() or '(none)'}"
        )
        delegated_msg = await send_message_fn(
            chatroom_id=task.chatroom_id,
            agent_id=task.created_by_agent_id
            if getattr(task, "created_by_agent_id", 0) and getattr(task, "created_by_agent_id", 0) > 0
            else None,
            content=instruction,
            message_type="text",
            metadata={
                "client_turn_id": client_turn_id,
                "delegated_task": task_metadata,
            },
            agent_name=current_agent_name,
        )
        await publish_saved_chat_message_fn(
            db,
            task.chatroom_id,
            message_id=delegated_msg.id,
            content=delegated_msg.content,
            agent_name=delegated_msg.agent_name,
            message_type=delegated_msg.message_type,
            created_at=delegated_msg.created_at,
            metadata={
                "client_turn_id": client_turn_id,
                "delegated_task": task_metadata,
            },
        )

        trigger_result = await trigger_agent_response_fn(
            task.chatroom_id,
            instruction,
            client_turn_id=client_turn_id,
            extra_context=f"Delegated by {current_agent_name}. {context}".strip(),
        )
        completed = True
        awaiting_tool_approval = False
        if isinstance(trigger_result, dict):
            completed = bool(trigger_result.get("completed"))
            awaiting_tool_approval = bool(trigger_result.get("awaiting_tool_approval"))
        if not completed:
            task.status = TaskStatus.IN_PROGRESS
            if awaiting_tool_approval:
                task.result = "Waiting for approval."
            coordinator.task_registry[task.id] = task
            return

        task.status = TaskStatus.COMPLETED
        delegated_result = None
        try:
            final_messages = (
                db.query(Message)
                .filter(Message.chatroom_id == task.chatroom_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .all()
            )
            for message in final_messages:
                try:
                    metadata = json.loads(getattr(message, "metadata_json", "") or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                if (metadata.get("client_turn_id") or "") != client_turn_id:
                    continue
                if getattr(message, "message_type", "") == "text" and getattr(message, "agent_id", None):
                    delegated_result = (getattr(message, "content", "") or "").strip()
                    if delegated_result:
                        break
        except Exception:
            delegated_result = None
        task.result = delegated_result or "Completed in chat window. See delegated turn output."
    except asyncio.CancelledError:
        interruption_summary = "Delegated execution interrupted during server reload or shutdown."
        logger.warning(
            "Delegated task cancelled before finalization: task_id=%s target=%s client_turn_id=%s",
            task.id,
            target_agent_type,
            client_turn_id,
        )
        mark_interrupted_fn(
            client_turn_id=client_turn_id,
            agent_name=target_agent_type,
            summary=interruption_summary,
        )
        task.status = TaskStatus.FAILED
        task.result = interruption_summary
        task.completed_at = datetime.now()
    except Exception as exc:
        logger.exception("Delegated task failed: task_id=%s target=%s error=%s", task.id, target_agent_type, exc)
        task.status = TaskStatus.FAILED
        task.result = f"Delegated execution failed: {exc}"
    finally:
        task.completed_at = getattr(task, "completed_at", None) if task.status != "completed" else task.completed_at
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED} and task.completed_at is None:
            task.completed_at = datetime.now()
        coordinator.task_registry[task.id] = task
        db.close()


async def kick_off_delegated_task_execution(
    *,
    task: Any,
    coordinator: Any,
    target_agent_type: str,
    task_title: str,
    task_description: str,
    context: str,
    current_agent_name: str,
    task_metadata: dict[str, Any],
    store_runtime_card_fn: Callable[[int, dict[str, Any]], Awaitable[Any]],
    send_message_fn: Callable[..., Awaitable[Any]],
    publish_saved_chat_message_fn: Callable[..., Awaitable[Any]],
    trigger_agent_response_fn: Callable[..., Awaitable[Any]],
    create_task_fn: Callable[[Awaitable[Any]], Any],
    mark_interrupted_fn: Callable[..., None],
) -> str:
    """Publish the delegated task card and spawn the delegated execution coroutine."""

    client_turn_id = f"delegate-{task.id}"
    await publish_delegate_task_card(
        chatroom_id=task.chatroom_id,
        from_agent=current_agent_name,
        to_agent=target_agent_type,
        content=delegate_task_card_content(task_title, task_description, context, task.id),
        client_turn_id=client_turn_id,
        store_runtime_card_fn=store_runtime_card_fn,
    )
    create_task_fn(
        run_delegated_task_in_chat(
            task=task,
            coordinator=coordinator,
            target_agent_type=target_agent_type,
            task_description=task_description,
            context=context,
            current_agent_name=current_agent_name,
            client_turn_id=client_turn_id,
            task_metadata=task_metadata,
            send_message_fn=send_message_fn,
            publish_saved_chat_message_fn=publish_saved_chat_message_fn,
            trigger_agent_response_fn=trigger_agent_response_fn,
            mark_interrupted_fn=mark_interrupted_fn,
        )
    )
    return client_turn_id
