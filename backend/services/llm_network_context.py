# -*- coding: utf-8 -*-
"""Per-call labels for LLM network audit records."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class LlmNetworkAuditContext:
    call_purpose: str | None = None
    purpose_label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


_ACTIVE_LLM_NETWORK_AUDIT_CONTEXT: ContextVar[LlmNetworkAuditContext] = ContextVar(
    "catown_active_llm_network_audit_context",
    default=LlmNetworkAuditContext(),
)


def _clean_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def get_active_llm_network_audit_context() -> LlmNetworkAuditContext:
    return _ACTIVE_LLM_NETWORK_AUDIT_CONTEXT.get()


def set_active_llm_network_audit_context(
    *,
    call_purpose: str | None = None,
    purpose_label: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Token:
    return _ACTIVE_LLM_NETWORK_AUDIT_CONTEXT.set(
        LlmNetworkAuditContext(
            call_purpose=_clean_optional_text(call_purpose),
            purpose_label=_clean_optional_text(purpose_label),
            metadata=dict(metadata or {}),
        )
    )


def reset_active_llm_network_audit_context(token: Token) -> None:
    _ACTIVE_LLM_NETWORK_AUDIT_CONTEXT.reset(token)


@contextmanager
def llm_network_audit_context(
    *,
    call_purpose: str | None = None,
    purpose_label: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    token = set_active_llm_network_audit_context(
        call_purpose=call_purpose,
        purpose_label=purpose_label,
        metadata=metadata,
    )
    try:
        yield
    finally:
        reset_active_llm_network_audit_context(token)
