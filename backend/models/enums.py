# -*- coding: utf-8 -*-
"""Centralised runtime enumerations for event types and run kinds.

Import from here instead of hardcoding strings throughout the codebase.
"""

from __future__ import annotations

import enum


class EventType(str, enum.Enum):
    """All ``TaskRunEvent.event_type`` values used in the runtime.

    Inherits from ``str`` so existing DB columns (String) and JSON
    serialisation work transparently — ``EventType.X == "x"`` is True.
    """

    # ── Orchestration lifecycle ────────────────────────────────────────────
    ORCHESTRATION_STARTED = "orchestration_started"
    SCHEDULER_PLAN_CREATED = "scheduler_plan_created"
    SCHEDULER_RECOVERY_STATE_REBUILT = "scheduler_recovery_state_rebuilt"

    # ── Scheduler step events ─────────────────────────────────────────────
    SCHEDULER_STEP_DISPATCHED = "scheduler_step_dispatched"
    SCHEDULER_STEP_COMPLETED = "scheduler_step_completed"
    SCHEDULER_STEP_RESUMED = "scheduler_step_resumed"
    SCHEDULER_STEP_FAILED = "scheduler_step_failed"
    SCHEDULER_STEP_CANCELLED = "scheduler_step_cancelled"

    # ── Agent turn lifecycle ──────────────────────────────────────────────
    AGENT_TURN_STARTED = "agent_turn_started"
    AGENT_TURN_COMPLETED = "agent_turn_completed"
    AGENT_TURN_RESUMED = "agent_turn_resumed"

    # ── LLM request/response ─────────────────────────────────────────────
    LLM_REQUEST_CREATED = "llm_request_created"
    LLM_RESPONSE_STARTED = "llm_response_started"
    LLM_RESPONSE_COMPLETED = "llm_response_completed"
    LLM_CALL = "llm_call"

    # ── Tool execution ───────────────────────────────────────────────────
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_BLOCKED = "tool_call_blocked"
    TOOL_ROUND_RECORDED = "tool_round_recorded"
    TOOL_CALL = "tool_call"

    # ── Approval queue ───────────────────────────────────────────────────
    APPROVAL_QUEUE_ITEM_CREATED = "approval_queue_item_created"
    APPROVAL_QUEUE_ITEM_RESOLVED = "approval_queue_item_resolved"
    APPROVAL_QUEUE_ITEM_FOLLOWUP_TRIGGERED = "approval_queue_item_followup_triggered"
    APPROVAL_QUEUE_ITEM_FOLLOWUP_INTERRUPTED = "approval_queue_item_followup_interrupted"
    APPROVAL_QUEUE_ITEM_FOLLOWUP_FAILED = "approval_queue_item_followup_failed"

    # ── Delegated tasks ──────────────────────────────────────────────────
    DELEGATED_TASK_DISPATCHED = "delegated_task_dispatched"
    DELEGATED_TASK_RESULT_REPORTED = "delegated_task_result_reported"

    # ── Run-shell tracking ───────────────────────────────────────────────
    RUN_SHELL_CONTINUATION_CLAIMED = "run_shell_continuation_claimed"
    TRACKED_RUN_SHELL_COMPLETED = "tracked_run_shell_completed"
    TRACKED_RUN_SHELL_FOLLOWUP_QUEUED = "tracked_run_shell_followup_queued"
    TRACKED_RUN_SHELL_FOLLOWUP_FAILED = "tracked_run_shell_followup_failed"

    # ── TaskRun lifecycle ────────────────────────────────────────────────
    TASK_RUN_CREATED = "task_run_created"
    TASK_RUN_FAILED = "task_run_failed"
    TASK_RUN_CANCELLED = "task_run_cancelled"
    TASK_RUN_INTERRUPTED = "task_run_interrupted"
    TASK_RUN_SUBAGENT_CANCELLED = "task_run_subagent_cancelled"
    TASK_RUN_MANUAL_RESUME_REQUESTED = "task_run_manual_resume_requested"
    TASK_RUN_RECOVERY_STARTED = "task_run_recovery_started"
    TASK_RUN_RECOVERY_COMPLETED = "task_run_recovery_completed"
    TASK_RUN_RECOVERY_FAILED = "task_run_recovery_failed"
    TASK_RUN_WAITING_FOR_DELEGATED_WORK = "task_run_waiting_for_delegated_work"

    # ── Handoff ──────────────────────────────────────────────────────────
    HANDOFF_CREATED = "handoff_created"

    # ── Policy & context ─────────────────────────────────────────────────
    POLICY_DECISION_RECORDED = "policy_decision_recorded"
    CONTEXT_BUDGET_EVENT = "context_budget_event"
    CONTEXT_COMPACTION = "context_compaction"
    USER_MESSAGE_SAVED = "user_message_saved"

    # ── Runtime mode / agent selection ────────────────────────────────────
    RUNTIME_MODE_SELECTED = "runtime_mode_selected"
    TARGET_AGENT_SELECTED = "target_agent_selected"
    TARGET_AGENTS_SELECTED = "target_agents_selected"
    SUBAGENT_HANDLE_CLOSED = "subagent_handle_closed"

    # ── Testing ──────────────────────────────────────────────────────────
    TEST_RUNNER_RESULT_CLASSIFIED = "test_runner_result_classified"
    TEST_REPORT_PRODUCED = "test_report_produced"

    # ── Pipeline engine events ───────────────────────────────────────────
    GATE_APPROVED = "gate_approved"
    GATE_REJECTED = "gate_rejected"
    GATE_AUTO_APPROVED = "gate_auto_approved"
    GATE_BLOCKED = "gate_blocked"
    BOSS_INSTRUCTION = "boss_instruction"
    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    STAGE_RETRY = "stage_retry"
    TIMEOUT = "timeout"
    ERROR = "error"


class RunKind(str, enum.Enum):
    """All ``TaskRun.run_kind`` values used in the runtime.

    Inherits from ``str`` for transparent DB/JSON compatibility.
    """

    CHAT_TURN = "chat_turn"
    CHAT_TURN_STREAM = "chat_turn_stream"
    STANDALONE_ASSISTANT = "standalone_assistant"
    STANDALONE_ASSISTANT_STREAM = "standalone_assistant_stream"
    PROJECT_SINGLE_AGENT = "project_single_agent"
    PROJECT_SINGLE_AGENT_STREAM = "project_single_agent_stream"
    MULTI_AGENT_ORCHESTRATION = "multi_agent_orchestration"
    MULTI_AGENT_ORCHESTRATION_STREAM = "multi_agent_orchestration_stream"
