# -*- coding: utf-8 -*-
"""Per-turn runtime state used to rebuild model messages each loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from services.tool_governance import classify_tool_result


@dataclass(frozen=True)
class ToolResultRecord:
    tool_call_id: str
    tool_name: str
    arguments: str
    result: str
    success: bool = True
    status: str = "succeeded"
    blocked: bool = False
    blocked_kind: str | None = None
    blocked_reason: str | None = None

    def to_message(self) -> dict[str, str]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.result,
            "name": self.tool_name,
        }


@dataclass(frozen=True)
class ToolRoundRecord:
    assistant_content: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[ToolResultRecord]

    def protocol_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "assistant",
                "content": self.assistant_content,
                "tool_calls": [dict(call) for call in self.tool_calls],
            }
        ]
        messages.extend(result.to_message() for result in self.tool_results)
        return messages


@dataclass
class TurnContextState:
    previous_agent_work: str = ""
    boss_instructions: list[str] = field(default_factory=list)
    inter_agent_messages: list[dict[str, Any]] = field(default_factory=list)
    tool_rounds: list[ToolRoundRecord] = field(default_factory=list)
    max_protocol_rounds: int = 1

    def add_boss_instructions(self, instructions: Iterable[str]) -> None:
        for item in instructions:
            text = str(item or "").strip()
            if text and text not in self.boss_instructions:
                self.boss_instructions.append(text)

    def add_inter_agent_messages(self, messages: Iterable[dict[str, Any]]) -> None:
        for item in messages:
            if isinstance(item, dict) and item:
                self.inter_agent_messages.append(dict(item))

    def record_tool_round(
        self,
        *,
        assistant_content: str,
        tool_calls: list[dict[str, Any]],
        tool_results: list[ToolResultRecord],
    ) -> None:
        self.tool_rounds.append(
            ToolRoundRecord(
                assistant_content=str(assistant_content or ""),
                tool_calls=[dict(call) for call in tool_calls],
                tool_results=list(tool_results),
            )
        )

    def protocol_messages(self) -> list[dict[str, Any]]:
        if self.max_protocol_rounds <= 0:
            return []
        rounds = self.tool_rounds[-self.max_protocol_rounds :]
        messages: list[dict[str, Any]] = []
        for round_record in rounds:
            messages.extend(round_record.protocol_messages())
        return messages

    def summarized_tool_lines(self) -> list[str]:
        if not self.tool_rounds:
            return []

        summary_rounds = self.tool_rounds[:-self.max_protocol_rounds] if self.max_protocol_rounds > 0 else self.tool_rounds
        lines: list[str] = []
        for index, round_record in enumerate(summary_rounds, start=1):
            assistant_preview = _compact_text(round_record.assistant_content, limit=140)
            if assistant_preview:
                lines.append(f"- Round {index} intent: {assistant_preview}")
            for result in round_record.tool_results:
                arg_preview = _compact_jsonish(result.arguments, limit=120)
                result_preview = _compact_text(result.result, limit=180)
                status = result.status or ("ok" if result.success else "error")
                lines.append(
                    f"- {result.tool_name}({arg_preview}) [{status}] -> {result_preview}"
                )
        return lines


def normalize_tool_call(tool_call: Any) -> dict[str, Any]:
    if hasattr(tool_call, "model_dump"):
        dumped = tool_call.model_dump()
        if isinstance(dumped, dict):
            return dumped

    if isinstance(tool_call, Mapping):
        function = tool_call.get("function") or {}
        if hasattr(function, "model_dump"):
            function = function.model_dump()
        if not isinstance(function, Mapping):
            function = {}
        arguments = function.get("arguments", tool_call.get("arguments", "{}"))
        arguments_text = arguments if isinstance(arguments, str) else _safe_json(arguments)
        return {
            "id": str(tool_call.get("id", "")),
            "type": str(tool_call.get("type") or "function"),
            "function": {
                "name": str(function.get("name") or tool_call.get("name") or "tool"),
                "arguments": arguments_text,
            },
        }

    function = getattr(tool_call, "function", None)
    function_name = getattr(function, "name", None) if function is not None else None
    function_args = getattr(function, "arguments", None) if function is not None else None
    return {
        "id": str(getattr(tool_call, "id", "")),
        "type": "function",
        "function": {
            "name": str(function_name or "tool"),
            "arguments": str(function_args or "{}"),
        },
    }


def build_tool_result_record(
    *,
    tool_call_id: Any,
    tool_name: str,
    arguments: Any,
    result: Any,
    success: bool = True,
    max_result_chars: int = 2000,
) -> ToolResultRecord:
    result_text = str(result or "(no output)")
    if max_result_chars > 0 and len(result_text) > max_result_chars:
        result_text = result_text[:max_result_chars]

    arguments_text = arguments if isinstance(arguments, str) else _safe_json(arguments)
    classification = classify_tool_result(
        str(tool_name or "tool"),
        result_text,
        success=success,
    )
    return ToolResultRecord(
        tool_call_id=str(tool_call_id or ""),
        tool_name=str(tool_name or "tool"),
        arguments=arguments_text,
        result=result_text,
        success=bool(classification.get("success")),
        status=str(classification.get("status") or "succeeded"),
        blocked=bool(classification.get("blocked")),
        blocked_kind=classification.get("blocked_kind"),
        blocked_reason=classification.get("blocked_reason"),
    )


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _compact_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _compact_jsonish(value: Any, *, limit: int) -> str:
    text = _compact_text(value, limit=limit)
    return text or "{}"
