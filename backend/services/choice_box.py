# -*- coding: utf-8 -*-
"""
Choice Box Service — interactive decision components in chat.

Allows agents to present structured choices to BOSS within the chat flow.
Supports: single choice, multi choice, confirm, edit.

Design follows PRD §4.6.4 and §8.3 (聊天交互选择框机制).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catown.choice_box")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ChoiceBoxType(str, Enum):
    SINGLE = "choice"       # Single selection
    MULTI = "multi_choice"  # Multiple selection
    CONFIRM = "confirm"     # Yes/No
    EDIT = "edit"           # Text editing


class ChoiceBoxStatus(str, Enum):
    PENDING = "pending"
    RESPONDED = "responded"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class ChoiceOption:
    """A single option in a choice box."""
    id: str
    label: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChoiceBox:
    """A choice box presented to BOSS in chat."""
    id: str
    type: ChoiceBoxType
    source_agent: str
    question: str
    options: List[ChoiceOption] = field(default_factory=list)
    context: str = ""
    multi: bool = False
    default_value: Optional[str] = None
    edit_placeholder: str = ""
    timeout_seconds: Optional[int] = None
    status: ChoiceBoxStatus = ChoiceBoxStatus.PENDING
    response_value: Optional[str] = None
    responded_at: Optional[str] = None
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.id:
            self.id = f"choice_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, Enum) else self.type
        d["status"] = self.status.value if isinstance(self.status, Enum) else self.status
        return d

    def to_message_content(self) -> str:
        """Format as a readable message for the chat."""
        parts = []
        if self.context:
            parts.append(f"📋 {self.context}")
        parts.append(f"❓ {self.question}")
        if self.options:
            for i, opt in enumerate(self.options, 1):
                parts.append(f"  {i}. {opt.label}")
                if opt.description:
                    parts.append(f"     {opt.description}")
        return "\n".join(parts)

    def to_metadata(self) -> Dict[str, Any]:
        """Format as metadata for storage in Message.metadata_json."""
        return {
            "choice_box": self.to_dict(),
        }


# ---------------------------------------------------------------------------
# Predefined choice boxes (convenience constructors)
# ---------------------------------------------------------------------------

def memory_decision_choice(
    source_agent: str,
    memory_content: str,
    context: str = "记忆持久化决策",
) -> ChoiceBox:
    """Standard choice box for memory persistence decisions (PRD §4.6.3)."""
    return ChoiceBox(
        id="",
        type=ChoiceBoxType.SINGLE,
        source_agent=source_agent,
        question="这条记忆应该：",
        context=context,
        options=[
            ChoiceOption("save_long", "💾 写入长期记忆", "作为通用模式，所有项目适用"),
            ChoiceOption("save_project", "📁 写入项目记忆", "仅限当前项目"),
            ChoiceOption("ignore", "🗑️ 忽略", "不需要持久化"),
            ChoiceOption("edit", "✏️ 编辑后保存", "修改内容后保存"),
        ],
        multi=False,
    )


def tool_authorization_choice(
    source_agent: str,
    tool_name: str,
    reason: str,
) -> ChoiceBox:
    """Standard choice box for tool temporary authorization (PRD §4.5)."""
    return ChoiceBox(
        id="",
        type=ChoiceBoxType.SINGLE,
        source_agent=source_agent,
        question=f"{source_agent} 请求临时使用 {tool_name}",
        context="🔐 工具临时授权",
        options=[
            ChoiceOption("allow_once", "✅ 本次允许", "单次调用授权，不改变白名单"),
            ChoiceOption("allow_stage", "🔄 本阶段允许", "在当前 Stage 内加入白名单"),
            ChoiceOption("reject", "❌ 拒绝", "Agent 将调整方案"),
        ],
        multi=False,
    )


def pipeline_approval_choice(
    stage_name: str,
    artifact_summary: str = "",
) -> ChoiceBox:
    """Standard choice box for pipeline stage approval."""
    return ChoiceBox(
        id="",
        type=ChoiceBoxType.SINGLE,
        source_agent="system",
        question=f"Stage [{stage_name}] 已完成",
        context="🚧 Pipeline 审批",
        options=[
            ChoiceOption("approve", "✅ Approve", "通过，进入下一阶段"),
            ChoiceOption("reject", "⏪ Reject", "打回重做"),
            ChoiceOption("view", "👁️ 查看产出物", "查看详情后再决定"),
        ],
        multi=False,
    )


def confirm_choice(
    source_agent: str,
    question: str,
    context: str = "",
) -> ChoiceBox:
    """Simple confirm/cancel choice."""
    return ChoiceBox(
        id="",
        type=ChoiceBoxType.CONFIRM,
        source_agent=source_agent,
        question=question,
        context=context,
        options=[
            ChoiceOption("confirm", "✅ 确认", ""),
            ChoiceOption("cancel", "❌ 取消", ""),
        ],
        multi=False,
    )


def edit_choice(
    source_agent: str,
    question: str,
    placeholder: str = "",
    context: str = "",
) -> ChoiceBox:
    """Text editing choice box."""
    return ChoiceBox(
        id="",
        type=ChoiceBoxType.EDIT,
        source_agent=source_agent,
        question=question,
        context=context,
        options=[],
        edit_placeholder=placeholder,
    )


# ---------------------------------------------------------------------------
# In-memory store (pending choice boxes)
# ---------------------------------------------------------------------------

class ChoiceBoxStore:
    """Manages pending choice boxes."""

    def __init__(self):
        self._pending: Dict[str, ChoiceBox] = {}

    def create(self, box: ChoiceBox) -> ChoiceBox:
        """Register a new choice box."""
        self._pending[box.id] = box
        logger.info("[ChoiceBox] Created %s: %s", box.id, box.question[:80])
        return box

    def get(self, box_id: str) -> Optional[ChoiceBox]:
        return self._pending.get(box_id)

    def respond(self, box_id: str, value: str) -> Optional[ChoiceBox]:
        """Record a response to a choice box."""
        box = self._pending.get(box_id)
        if not box:
            return None
        box.status = ChoiceBoxStatus.RESPONDED
        box.response_value = value
        box.responded_at = datetime.now().isoformat()
        logger.info("[ChoiceBox] Responded %s: %s", box_id, value)
        return box

    def cancel(self, box_id: str) -> Optional[ChoiceBox]:
        box = self._pending.get(box_id)
        if not box:
            return None
        box.status = ChoiceBoxStatus.CANCELLED
        return box

    def remove(self, box_id: str) -> Optional[ChoiceBox]:
        return self._pending.pop(box_id, None)

    @property
    def pending_boxes(self) -> List[ChoiceBox]:
        return [b for b in self._pending.values() if b.status == ChoiceBoxStatus.PENDING]

    def pending_for_chatroom(self, chatroom_id: int) -> List[ChoiceBox]:
        """Get pending boxes for a specific chatroom (future: filter by chatroom)."""
        return self.pending_boxes


# Global singleton
_store = ChoiceBoxStore()


def get_choice_box_store() -> ChoiceBoxStore:
    return _store
