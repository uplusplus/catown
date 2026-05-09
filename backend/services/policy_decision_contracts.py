# -*- coding: utf-8 -*-
"""Schema v1 for software-produced runtime policy decisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

PolicyDecisionType = Literal[
    "action_request_policy",
    "artifact_contract_policy",
    "evaluation_result_policy",
    "workflow_spec_policy",
    "runner_policy",
]

PolicyDecisionSubjectKind = Literal[
    "action_request",
    "artifact_contract",
    "evaluation_result",
    "workflow_spec",
    "runner_policy",
]

PolicyDecisionViolationSeverity = Literal["info", "warning", "error"]


class PolicyDecisionSubject(BaseModel):
    kind: PolicyDecisionSubjectKind
    id: str
    type: str | None = None


class PolicyDecisionViolation(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: PolicyDecisionViolationSeverity = "error"


class PolicyDecisionContract(BaseModel):
    kind: Literal["policy_decision"] = "policy_decision"
    version: Literal[1] = 1
    decision_id: str
    decision_type: PolicyDecisionType
    subject: PolicyDecisionSubject
    accepted: bool
    stage_name: str | None = None
    policy_source: str | None = None
    pipeline_name: str | None = None
    stage_count: int | None = None
    violations: list[PolicyDecisionViolation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


POLICY_DECISION_ADAPTER = TypeAdapter(PolicyDecisionContract)


def parse_policy_decision(payload: Any) -> PolicyDecisionContract:
    """Validate and parse one schema-v1 policy decision payload."""

    return POLICY_DECISION_ADAPTER.validate_python(payload)


def dump_policy_decision(decision: PolicyDecisionContract) -> dict[str, Any]:
    """Return the canonical JSON-compatible payload for one policy decision."""

    return decision.model_dump(mode="json")


def summarize_policy_decision(decision: PolicyDecisionContract | dict[str, Any]) -> dict[str, Any]:
    """Return a stable read-model summary for one policy decision."""

    parsed_decision = parse_policy_decision(decision) if isinstance(decision, dict) else decision
    violation_counts = {"info": 0, "warning": 0, "error": 0}
    for violation in parsed_decision.violations:
        violation_counts[violation.severity] = violation_counts.get(violation.severity, 0) + 1

    return {
        "decision_id": parsed_decision.decision_id,
        "decision_type": parsed_decision.decision_type,
        "subject_kind": parsed_decision.subject.kind,
        "subject_id": parsed_decision.subject.id,
        "subject_type": parsed_decision.subject.type,
        "accepted": parsed_decision.accepted,
        "stage_name": parsed_decision.stage_name,
        "policy_source": parsed_decision.policy_source,
        "pipeline_name": parsed_decision.pipeline_name,
        "violation_count": len(parsed_decision.violations),
        "error_count": violation_counts["error"],
        "warning_count": violation_counts["warning"],
        "info_count": violation_counts["info"],
    }


def build_policy_decision_event_payload(
    decision: PolicyDecisionContract | dict[str, Any],
    *,
    include_contract: bool = True,
) -> dict[str, Any]:
    """Build a run-ledger-friendly event payload for one policy decision."""

    parsed_decision = parse_policy_decision(decision) if isinstance(decision, dict) else decision
    summary = summarize_policy_decision(parsed_decision)
    payload: dict[str, Any] = {
        "event_kind": "policy_decision_recorded",
        "policy_decision_summary": summary,
        "accepted": summary["accepted"],
        "decision_type": summary["decision_type"],
        "subject_kind": summary["subject_kind"],
        "subject_id": summary["subject_id"],
        "stage_name": summary["stage_name"],
    }
    if include_contract:
        payload["policy_decision"] = dump_policy_decision(parsed_decision)
    return payload


def project_policy_decision(
    decision: Any,
    *,
    decision_id: str | None = None,
    decision_type: PolicyDecisionType | None = None,
    subject_kind: PolicyDecisionSubjectKind | None = None,
    subject_id: str | None = None,
    subject_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PolicyDecisionContract:
    """Project an existing service-level policy decision into schema-v1 form."""

    payload = _decision_payload(decision)
    inferred = _infer_decision_identity(
        payload,
        decision_type=decision_type,
        subject_kind=subject_kind,
        subject_id=subject_id,
        subject_type=subject_type,
    )
    payload_metadata = dict(payload.get("metadata") or {})
    extra_metadata = dict(metadata or {})

    return parse_policy_decision(
        {
            "kind": "policy_decision",
            "version": 1,
            "decision_id": decision_id
            or _default_decision_id(
                decision_type=inferred["decision_type"],
                subject_id=inferred["subject_id"],
            ),
            "decision_type": inferred["decision_type"],
            "subject": {
                "kind": inferred["subject_kind"],
                "id": inferred["subject_id"],
                "type": inferred["subject_type"],
            },
            "accepted": bool(payload.get("accepted", False)),
            "stage_name": _optional_text(payload.get("stage_name")),
            "policy_source": _optional_text(payload_metadata.get("policy_source")),
            "pipeline_name": _optional_text(payload_metadata.get("pipeline_name")),
            "stage_count": _int_or_none(payload_metadata.get("stage_count")),
            "violations": _normalize_violations(payload.get("violations")),
            "metadata": {
                **payload_metadata,
                **extra_metadata,
            },
        }
    )


def _decision_payload(decision: Any) -> dict[str, Any]:
    if isinstance(decision, dict):
        return dict(decision)
    to_payload = getattr(decision, "to_payload", None)
    if callable(to_payload):
        payload = to_payload()
        if isinstance(payload, dict):
            return dict(payload)
    raise ValueError("Policy decision projection requires a dict or to_payload() object.")


def _infer_decision_identity(
    payload: dict[str, Any],
    *,
    decision_type: PolicyDecisionType | None,
    subject_kind: PolicyDecisionSubjectKind | None,
    subject_id: str | None,
    subject_type: str | None,
) -> dict[str, str | None]:
    if subject_id and subject_kind and decision_type:
        return {
            "decision_type": decision_type,
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "subject_type": subject_type,
        }

    if payload.get("request_id"):
        return {
            "decision_type": decision_type or "action_request_policy",
            "subject_kind": subject_kind or "action_request",
            "subject_id": subject_id or _clean_text(payload.get("request_id")),
            "subject_type": subject_type or _optional_text(payload.get("request_type")),
        }
    if payload.get("artifact_id"):
        return {
            "decision_type": decision_type or "artifact_contract_policy",
            "subject_kind": subject_kind or "artifact_contract",
            "subject_id": subject_id or _clean_text(payload.get("artifact_id")),
            "subject_type": subject_type or _optional_text(payload.get("artifact_type")),
        }
    if payload.get("result_id"):
        return {
            "decision_type": decision_type or "evaluation_result_policy",
            "subject_kind": subject_kind or "evaluation_result",
            "subject_id": subject_id or _clean_text(payload.get("result_id")),
            "subject_type": subject_type,
        }

    if not decision_type or not subject_kind or not subject_id:
        raise ValueError(
            "Policy decision projection could not infer subject identity; "
            "provide decision_type, subject_kind, and subject_id."
        )
    return {
        "decision_type": decision_type,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "subject_type": subject_type,
    }


def _normalize_violations(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(value or []):
        violation = dict(item or {})
        normalized.append(
            {
                "code": _clean_text(violation.get("code")),
                "message": _clean_text(violation.get("message")),
                "field": _optional_text(violation.get("field")),
                "severity": _normalize_severity(violation.get("severity")),
            }
        )
    return normalized


def _normalize_severity(value: Any) -> str:
    severity = _clean_text(value).lower()
    if severity in {"info", "warning", "error"}:
        return severity
    return "error"


def _default_decision_id(*, decision_type: str, subject_id: str) -> str:
    return f"policy-decision-{_slug(decision_type)}-{_slug(subject_id)}"


def _slug(value: Any) -> str:
    return (
        _clean_text(value)
        .replace(":", "-")
        .replace("/", "-")
        .replace(" ", "-")
        .replace("_", "-")
        or "item"
    )


def _optional_text(value: Any) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
