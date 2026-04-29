# -*- coding: utf-8 -*-
"""Shared contract models for single-agent session orchestration layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from services.stream_transport import SerializePayload


@dataclass(frozen=True)
class UnifiedSingleAgentSessionOutcome:
    final_content: str | None = None
    chunk: str | None = None
    payload: dict[str, Any] | None = None
    error_text: str | None = None


@dataclass(frozen=True)
class UnifiedSingleAgentSessionSpec:
    iterate: Callable[[], AsyncIterator[UnifiedSingleAgentSessionOutcome]]


@dataclass(frozen=True)
class ManagedSingleAgentSessionCallbacks:
    finalize_success: Callable[[str], Awaitable[Any]]
    finalize_failure: Callable[[Exception], Awaitable[Any] | Any]


@dataclass(frozen=True)
class ManagedSingleAgentStreamTransport:
    serialize_payload: SerializePayload


@dataclass(frozen=True)
class ManagedSingleAgentSessionSpec:
    session: UnifiedSingleAgentSessionSpec
    callbacks: ManagedSingleAgentSessionCallbacks
    stream_transport: ManagedSingleAgentStreamTransport | None = None
