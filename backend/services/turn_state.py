# -*- coding: utf-8 -*-
"""Per-turn runtime state used to rebuild model messages each loop."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from services.tool_governance import classify_tool_result


_TOOL_RESULT_TRUNCATE_THRESHOLD = 2000
_TOOL_RESULT_PROMPT_HEAD_CHARS = 700
_TOOL_RESULT_PROMPT_TAIL_CHARS = 500
_TOOL_RESULT_TRUNCATE_MARKER = "\n[truncated for context budget]"


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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, str]:
        content = self.prompt_visible_result()
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": content,
            "name": self.tool_name,
        }

    def prompt_visible_result(self) -> str:
        """Return the bounded form of a tool result that is safe to feed back to the model."""

        metadata = self.metadata if isinstance(self.metadata, Mapping) else {}
        context_payload = metadata.get("context_budget") if isinstance(metadata.get("context_budget"), Mapping) else {}
        prompt_result = context_payload.get("prompt_result")
        if isinstance(prompt_result, str) and prompt_result.strip():
            return prompt_result

        content = str(self.result or "")
        if len(content) <= _TOOL_RESULT_TRUNCATE_THRESHOLD:
            return content

        reference_lines = _tool_result_reference_lines(metadata)
        signal_lines = _tool_result_signal_lines(
            tool_name=self.tool_name,
            arguments_text=self.arguments,
            result_text=content,
        )
        parts = [
            f"[Tool Result Summary] {self.tool_name} returned {len(content)} characters; showing a bounded head/tail excerpt.",
        ]
        if signal_lines:
            parts.extend(["", "Key signals:", *signal_lines])
        parts.extend(
            [
                "",
                "Head:",
                content[:_TOOL_RESULT_PROMPT_HEAD_CHARS].rstrip(),
                "",
                "Tail:",
                content[-_TOOL_RESULT_PROMPT_TAIL_CHARS:].lstrip(),
            ]
        )
        if reference_lines:
            parts.extend(["", *reference_lines])
        parts.append(_TOOL_RESULT_TRUNCATE_MARKER.strip())
        return "\n".join(part for part in parts if part is not None)


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
    continuation_protocol_messages: list[dict[str, Any]] = field(default_factory=list)
    continuation_summaries: list[str] = field(default_factory=list)
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
        messages: list[dict[str, Any]] = [dict(message) for message in self.continuation_protocol_messages]
        if self.max_protocol_rounds <= 0:
            return messages
        rounds = self.tool_rounds[-self.max_protocol_rounds :]
        for round_record in rounds:
            messages.extend(round_record.protocol_messages())
        return messages

    def summarized_tool_lines(self) -> list[str]:
        if not self.tool_rounds and not self.continuation_summaries:
            return []

        summary_rounds = self.tool_rounds[:-self.max_protocol_rounds] if self.max_protocol_rounds > 0 else self.tool_rounds
        lines: list[str] = list(self.continuation_summaries)
        for index, round_record in enumerate(summary_rounds, start=1):
            assistant_preview = _compact_text(round_record.assistant_content, limit=140)
            if assistant_preview:
                lines.append(f"- Round {index} intent: {assistant_preview}")
            for result in round_record.tool_results:
                arg_preview = _compact_jsonish(result.arguments, limit=120)
                result_preview = _compact_text(result.prompt_visible_result(), limit=220)
                status = result.status or ("ok" if result.success else "error")
                lines.append(
                    f"- {result.tool_name}({arg_preview}) [{status}] -> {result_preview}"
                )
        return lines

    def seed_continuation_state(
        self,
        *,
        protocol_messages: Iterable[dict[str, Any]] | None = None,
        prior_round_summaries: Iterable[str] | None = None,
    ) -> None:
        self.continuation_protocol_messages = [
            dict(message)
            for message in (protocol_messages or [])
            if isinstance(message, Mapping) and message
        ]
        self.continuation_summaries = [
            str(summary or "").strip()
            for summary in (prior_round_summaries or [])
            if str(summary or "").strip()
        ]

    def reset(self) -> None:
        """Clear all accumulated turn-local state, restoring a fresh instance.

        Useful when reusing a ``TurnContextState`` across multiple loop
        iterations to prevent unbounded memory growth.
        """
        self.previous_agent_work = ""
        self.boss_instructions.clear()
        self.inter_agent_messages.clear()
        self.tool_rounds.clear()
        self.continuation_protocol_messages.clear()
        self.continuation_summaries.clear()

    def compact(self, *, keep_last_n_rounds: int = 1) -> None:
        """Retain only the most recent *keep_last_n_rounds* tool rounds.

        Older rounds are summarised into ``continuation_summaries`` so the
        model still has context, but the full protocol messages are dropped.
        This bounds memory growth in long-running multi-turn loops.

        Parameters
        ----------
        keep_last_n_rounds:
            How many of the most recent tool rounds to keep in full.
            Defaults to 1 (the same as ``max_protocol_rounds``).
        """
        if keep_last_n_rounds < 0:
            keep_last_n_rounds = 0

        if not self.tool_rounds:
            return

        excess = self.tool_rounds[:-keep_last_n_rounds] if keep_last_n_rounds > 0 else list(self.tool_rounds)
        if not excess:
            return

        # Build lightweight summaries for the rounds we are about to drop
        for index, round_record in enumerate(excess, start=1):
            assistant_preview = _compact_text(round_record.assistant_content, limit=140)
            if assistant_preview:
                self.continuation_summaries.append(f"- Round {index} intent: {assistant_preview}")
            for result in round_record.tool_results:
                arg_preview = _compact_jsonish(result.arguments, limit=120)
                result_preview = _compact_text(result.prompt_visible_result(), limit=220)
                status = result.status or ("ok" if result.success else "error")
                self.continuation_summaries.append(
                    f"- {result.tool_name}({arg_preview}) [{status}] -> {result_preview}"
                )

        # Keep only the tail of tool_rounds
        if keep_last_n_rounds > 0:
            self.tool_rounds = self.tool_rounds[-keep_last_n_rounds:]
        else:
            self.tool_rounds.clear()


def build_turn_state_from_checkpoint_snapshot(
    checkpoint_snapshot: Any,
    *,
    previous_agent_work: str = "",
    max_protocol_rounds: int = 1,
) -> TurnContextState:
    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, Mapping) else {}
    turn_local_state = snapshot.get("turn_local_state") if isinstance(snapshot.get("turn_local_state"), Mapping) else {}
    protocol_tail_messages = turn_local_state.get("protocol_tail_messages")
    if not isinstance(protocol_tail_messages, list):
        protocol_tail_messages = []

    raw_prior_round_summaries = turn_local_state.get("prior_round_summaries")
    formatted_prior_round_summaries: list[str] = []
    if isinstance(raw_prior_round_summaries, list):
        for index, item in enumerate(raw_prior_round_summaries, start=1):
            if not isinstance(item, Mapping):
                continue
            turn = item.get("turn")
            assistant_content = _compact_text(item.get("assistant_content"), limit=140)
            tool_names = item.get("tool_names") if isinstance(item.get("tool_names"), list) else []
            blocked_tool_count = item.get("blocked_tool_count")
            parts = [f"- Prior round {turn or index}"]
            if tool_names:
                parts.append(f"tools={', '.join(str(name) for name in tool_names if str(name).strip())}")
            if blocked_tool_count not in (None, 0):
                parts.append(f"blocked={blocked_tool_count}")
            if assistant_content:
                parts.append(f"intent: {assistant_content}")
            formatted_prior_round_summaries.append(" · ".join(parts))

    turn_state = TurnContextState(
        previous_agent_work=previous_agent_work or "",
        max_protocol_rounds=max_protocol_rounds,
    )
    turn_state.seed_continuation_state(
        protocol_messages=protocol_tail_messages,
        prior_round_summaries=formatted_prior_round_summaries,
    )
    return turn_state


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
    arguments_text = arguments if isinstance(arguments, str) else _safe_json(arguments)

    if isinstance(result, Mapping) and result.get("__catown_tool_result__") is True:
        prompt_source_text = str(result.get("result") or "(no output)")
        result_text = prompt_source_text
        if max_result_chars > 0 and len(result_text) > max_result_chars:
            result_text = result_text[:max_result_chars]
        classification = {
            "status": str(result.get("status") or ("succeeded" if result.get("success") else "failed")),
            "success": bool(result.get("success")),
            "blocked": bool(result.get("blocked")),
            "blocked_kind": result.get("blocked_kind"),
            "blocked_reason": result.get("blocked_reason"),
        }
        metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
        resolved_tool_name = str(result.get("tool_name") or tool_name or "tool")
    else:
        prompt_source_text = str(result or "(no output)")
        result_text = prompt_source_text
        if max_result_chars > 0 and len(result_text) > max_result_chars:
            result_text = result_text[:max_result_chars]
        classification = classify_tool_result(
            str(tool_name or "tool"),
            result_text,
            success=success,
        )
        metadata = {}
        resolved_tool_name = str(tool_name or "tool")

    metadata = dict(metadata)
    metadata["context_budget"] = _tool_result_context_budget_metadata(
        tool_name=resolved_tool_name,
        arguments_text=arguments_text,
        result_text=result_text,
        prompt_source_text=prompt_source_text,
        metadata=metadata,
    )

    return ToolResultRecord(
        tool_call_id=str(tool_call_id or ""),
        tool_name=resolved_tool_name,
        arguments=arguments_text,
        result=result_text,
        success=bool(classification.get("success")),
        status=str(classification.get("status") or "succeeded"),
        blocked=bool(classification.get("blocked")),
        blocked_kind=classification.get("blocked_kind"),
        blocked_reason=classification.get("blocked_reason"),
        metadata=metadata,
    )


def _tool_result_context_budget_metadata(
    *,
    tool_name: str,
    arguments_text: str,
    result_text: str,
    prompt_source_text: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    prompt_result = _build_prompt_visible_tool_result(
        tool_name=tool_name,
        arguments_text=arguments_text,
        result_text=result_text,
        metadata=metadata,
        prompt_source_text=prompt_source_text,
    )
    return {
        "original_result_chars": len(prompt_source_text),
        "stored_result_chars": len(result_text),
        "prompt_result_chars": len(prompt_result),
        "prompt_truncated": prompt_result != result_text,
        "prompt_result": prompt_result,
        "full_output_refs": _tool_result_refs(metadata),
    }


def _build_prompt_visible_tool_result(
    *,
    tool_name: str,
    arguments_text: str,
    result_text: str,
    metadata: Mapping[str, Any],
    prompt_source_text: str,
) -> str:
    if len(prompt_source_text) <= _TOOL_RESULT_TRUNCATE_THRESHOLD:
        return result_text

    reference_lines = _tool_result_reference_lines(metadata)
    signal_lines = _tool_result_signal_lines(
        tool_name=tool_name,
        arguments_text=arguments_text,
        result_text=prompt_source_text,
    )
    parts = [
        (
            f"[Tool Result Summary] {tool_name} returned {len(prompt_source_text)} characters "
            f"({len(result_text)} stored); showing a bounded head/tail excerpt."
        ),
    ]
    if signal_lines:
        parts.extend(["", "Key signals:", *signal_lines])
    parts.extend(
        [
            "",
            "Head:",
            prompt_source_text[:_TOOL_RESULT_PROMPT_HEAD_CHARS].rstrip(),
            "",
            "Tail:",
            prompt_source_text[-_TOOL_RESULT_PROMPT_TAIL_CHARS:].lstrip(),
        ]
    )
    if reference_lines:
        parts.extend(["", *reference_lines])
    parts.append(_TOOL_RESULT_TRUNCATE_MARKER.strip())
    return "\n".join(part for part in parts if part is not None)


def _tool_result_signal_lines(
    *,
    tool_name: str,
    arguments_text: str,
    result_text: str,
) -> list[str]:
    normalized_tool = str(tool_name or "tool").strip().lower()
    args = _safe_json_object(arguments_text)
    lines: list[str] = []

    if normalized_tool == "run_shell":
        lines.extend(_run_shell_signal_lines(args, result_text))
    if normalized_tool == "browser":
        lines.extend(_browser_signal_lines(args, result_text))
    if normalized_tool in {"web_search", "web_fetch", "search_files"} or "search" in normalized_tool:
        lines.extend(_search_signal_lines(normalized_tool, args, result_text))

    return _dedupe_signal_lines(lines, limit=8)


def _run_shell_signal_lines(args: Mapping[str, Any], result_text: str) -> list[str]:
    lines: list[str] = []
    command = _optional_text(args.get("command"))
    if command:
        lines.append(f"- Command: {_compact_text(command, limit=180)}")
    exit_code = _extract_exit_code(args, result_text)
    if exit_code is not None:
        lines.append(f"- Exit code: {exit_code}")

    lowered_command = command.lower()
    lowered_result = result_text.lower()
    looks_like_pytest = "pytest" in lowered_command or "short test summary info" in lowered_result or "collected " in lowered_result
    if looks_like_pytest:
        try:
            from services.test_runner_contract import extract_pytest_counts, extract_pytest_failure_summary

            counts = extract_pytest_counts(result_text)
            failed = counts.get("failed") or 0
            errors = counts.get("errors") or 0
            if errors:
                test_status = "errored"
            elif failed:
                test_status = "failed"
            elif counts.get("passed") is not None:
                test_status = "passed"
            else:
                test_status = "unknown"
            lines.append(f"- Test status: {test_status}")
            count_parts = [
                f"{name}={value}"
                for name in ("passed", "failed", "skipped", "errors")
                if (value := counts.get(name)) is not None
            ]
            if count_parts:
                lines.append(f"- Counts: {', '.join(count_parts)}")
            if test_status in {"failed", "errored"}:
                summary = _compact_text(extract_pytest_failure_summary(result_text), limit=260)
                if summary:
                    lines.append(f"- Failure summary: {summary}")
                failed_targets = _pytest_failed_targets(result_text, limit=3)
                if failed_targets:
                    lines.append(f"- Failed tests: {' | '.join(failed_targets)}")
        except Exception:
            pass

    diagnostic_lines = _matching_diagnostic_lines(result_text, limit=3)
    if diagnostic_lines:
        lines.append(f"- Diagnostic lines: {' | '.join(diagnostic_lines)}")
    return lines


def _browser_signal_lines(args: Mapping[str, Any], result_text: str) -> list[str]:
    payload = _safe_json_object(result_text)
    page = payload.get("page") if isinstance(payload.get("page"), Mapping) else {}
    response = payload.get("response") if isinstance(payload.get("response"), Mapping) else {}
    lines: list[str] = []
    action = _optional_text(args.get("action") or payload.get("action"))
    if action:
        lines.append(f"- Action: {_compact_text(action, limit=80)}")
    selector = _optional_text(args.get("selector") or payload.get("selector"))
    if selector:
        lines.append(f"- Selector: {_compact_text(selector, limit=120)}")
    url = _optional_text(
        args.get("url")
        or payload.get("url")
        or payload.get("current_url")
        or page.get("url")
        or response.get("url")
    )
    if url:
        lines.append(f"- URL: {_compact_text(url, limit=180)}")
    title = _optional_text(payload.get("title") or page.get("title"))
    if title:
        lines.append(f"- Title: {_compact_text(title, limit=160)}")
    status = payload.get("status") or payload.get("status_code") or response.get("status") or response.get("status_code")
    if status is not None:
        lines.append(f"- HTTP status: {status}")
    error = _optional_text(payload.get("error"))
    if error:
        lines.append(f"- Error: {_compact_text(error, limit=180)}")
    for field_name in ("text", "content", "result"):
        value = payload.get(field_name)
        if isinstance(value, str) and value:
            lines.append(f"- {field_name.title()} chars: {len(value)}")
            break
    path = _optional_text(payload.get("path") or payload.get("output_path") or payload.get("screenshot_path"))
    if path:
        lines.append(f"- Output path: {_compact_text(path, limit=180)}")
    return lines


def _search_signal_lines(tool_name: str, args: Mapping[str, Any], result_text: str) -> list[str]:
    payload = _safe_json_object(result_text)
    lines: list[str] = []
    query = _optional_text(args.get("query") or args.get("search_term") or payload.get("query"))
    if query:
        lines.append(f"- Query: {_compact_text(query, limit=180)}")
    url = _optional_text(args.get("url") or payload.get("url"))
    if url:
        lines.append(f"- URL: {_compact_text(url, limit=180)}")

    if tool_name == "web_fetch":
        first_line = next((line.strip() for line in result_text.splitlines() if line.strip()), "")
        if first_line.startswith("[WebFetch]"):
            lines.append(f"- Fetch target: {_compact_text(first_line.removeprefix('[WebFetch]').strip(), limit=180)}")
        lines.append(f"- Fetched text chars: {len(result_text)}")
        return lines

    related_count = len(re.findall(r"^\s*\d+\.\s+", result_text, flags=re.MULTILINE))
    if related_count:
        lines.append(f"- Related results: {related_count}")
    top_results = _search_top_result_lines(payload, result_text, limit=3)
    if top_results:
        lines.extend(top_results)
    source_match = re.search(r"^Source:\s*(?P<source>\S+)", result_text, flags=re.MULTILINE)
    if source_match:
        lines.append(f"- Source: {_compact_text(source_match.group('source'), limit=180)}")
    first_line = next((line.strip() for line in result_text.splitlines() if line.strip()), "")
    if first_line and first_line.startswith("[Search Files]"):
        lines.append(f"- Search result: {_compact_text(first_line, limit=220)}")
    return lines


def _extract_exit_code(args: Mapping[str, Any], result_text: str) -> int | None:
    for key in ("exit_code", "returncode", "return_code"):
        value = args.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    match = re.search(r"\b(?:exit code|return code|returncode)\s*[:=]\s*(-?\d+)\b", result_text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _pytest_failed_targets(result_text: str, *, limit: int) -> list[str]:
    targets: list[str] = []
    for line in result_text.splitlines():
        match = re.match(r"^\s*(FAILED|ERROR)\s+(?P<target>\S+)", line)
        if not match:
            continue
        targets.append(_compact_text(match.group("target"), limit=180))
        if len(targets) >= limit:
            break
    return targets


def _search_top_result_lines(payload: Mapping[str, Any], result_text: str, *, limit: int) -> list[str]:
    result_items = payload.get("results") or payload.get("items") or payload.get("data")
    lines: list[str] = []
    if isinstance(result_items, list):
        for item in result_items:
            if not isinstance(item, Mapping):
                continue
            title = _optional_text(item.get("title") or item.get("name") or item.get("text"))
            url = _optional_text(item.get("url") or item.get("link") or item.get("source"))
            if not title and not url:
                continue
            result_line = title if title else url
            if title and url:
                result_line = f"{title} - {url}"
            lines.append(f"- Top result: {_compact_text(result_line, limit=220)}")
            if len(lines) >= limit:
                return lines
        if lines:
            return lines

    numbered_results = []
    for line in result_text.splitlines():
        match = re.match(r"^\s*\d+[.)]\s+(?P<title>.+?)\s*$", line)
        if match:
            numbered_results.append(_compact_text(match.group("title"), limit=220))
        if len(numbered_results) >= limit:
            break
    return [f"- Top result: {item}" for item in numbered_results[:limit]]


def _tool_result_reference_lines(metadata: Mapping[str, Any]) -> list[str]:
    refs = _tool_result_refs(metadata)
    lines: list[str] = []
    if refs.get("tee_path"):
        lines.append(f"Full output tee: {refs['tee_path']}")
    if refs.get("log_path"):
        lines.append(f"Tracked process log: {refs['log_path']}")
    if refs.get("redirected_log_path"):
        lines.append(f"Redirected output: {refs['redirected_log_path']}")
    if refs.get("token"):
        lines.append(f"Tracked process token: {refs['token']}")
    if refs.get("consult_step_id"):
        lines.append(f"Consult step: {refs['consult_step_id']}")
    if refs.get("consult_task_run_id"):
        lines.append(f"Consult task run: {refs['consult_task_run_id']}")
    if refs.get("consult_client_turn_id"):
        lines.append(f"Consult client turn: {refs['consult_client_turn_id']}")
    if refs.get("tool_output_artifact_path"):
        lines.append(f"Tool output artifact: {refs['tool_output_artifact_path']}")
    if refs.get("tool_output_artifact_sha256"):
        lines.append(f"Tool output sha256: {refs['tool_output_artifact_sha256']}")
    return lines


def _tool_result_refs(metadata: Mapping[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    output_filter = metadata.get("output_filter") if isinstance(metadata.get("output_filter"), Mapping) else {}
    if output_filter.get("tee_path"):
        refs["tee_path"] = str(output_filter.get("tee_path"))

    tracked_process = metadata.get("tracked_process") if isinstance(metadata.get("tracked_process"), Mapping) else {}
    for key in ("token", "log_path", "redirected_log_path"):
        value = tracked_process.get(key)
        if isinstance(value, str) and value.strip():
            refs[key] = value.strip()

    consult_agent = metadata.get("consult_agent") if isinstance(metadata.get("consult_agent"), Mapping) else {}
    if consult_agent.get("consult_step_id"):
        refs["consult_step_id"] = str(consult_agent.get("consult_step_id"))
    if consult_agent.get("task_run_id") is not None:
        refs["consult_task_run_id"] = str(consult_agent.get("task_run_id"))
    if consult_agent.get("client_turn_id"):
        refs["consult_client_turn_id"] = str(consult_agent.get("client_turn_id"))

    tool_output_artifact = (
        metadata.get("tool_output_artifact")
        if isinstance(metadata.get("tool_output_artifact"), Mapping)
        else {}
    )
    if tool_output_artifact.get("path"):
        refs["tool_output_artifact_path"] = str(tool_output_artifact.get("path"))
    if tool_output_artifact.get("sha256"):
        refs["tool_output_artifact_sha256"] = str(tool_output_artifact.get("sha256"))
    return refs


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _matching_diagnostic_lines(result_text: str, *, limit: int) -> list[str]:
    patterns = (
        r"\berror\b",
        r"\bfailed\b",
        r"\btraceback\b",
        r"\bexception\b",
        r"\bpanic\b",
    )
    lines: list[str] = []
    for line in result_text.splitlines():
        compacted = _compact_text(line, limit=220)
        if compacted and any(re.search(pattern, compacted, flags=re.IGNORECASE) for pattern in patterns):
            lines.append(compacted)
        if len(lines) >= limit:
            break
    return lines


def _dedupe_signal_lines(lines: Iterable[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        normalized = str(line or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
        if len(unique) >= limit:
            break
    return unique


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
