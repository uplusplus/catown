from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class LlmRuntimeContext:
    task_run_id: int | None = None
    chatroom_id: int | None = None


_ACTIVE_LLM_RUNTIME_CONTEXT: ContextVar[LlmRuntimeContext] = ContextVar(
    "catown_active_llm_runtime_context",
    default=LlmRuntimeContext(),
)


def get_active_llm_runtime_context() -> LlmRuntimeContext:
    return _ACTIVE_LLM_RUNTIME_CONTEXT.get()


def set_active_llm_runtime_context(*, task_run_id: int | None, chatroom_id: int | None) -> Token:
    return _ACTIVE_LLM_RUNTIME_CONTEXT.set(
        LlmRuntimeContext(
            task_run_id=task_run_id,
            chatroom_id=chatroom_id,
        )
    )


def reset_active_llm_runtime_context(token: Token) -> None:
    _ACTIVE_LLM_RUNTIME_CONTEXT.reset(token)


@contextmanager
def llm_runtime_context(*, task_run_id: int | None, chatroom_id: int | None) -> Iterator[None]:
    token = set_active_llm_runtime_context(
        task_run_id=task_run_id,
        chatroom_id=chatroom_id,
    )
    try:
        yield
    finally:
        reset_active_llm_runtime_context(token)
