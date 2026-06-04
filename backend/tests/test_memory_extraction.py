import pytest

from services.memory_extraction import (
    build_memory_extraction_messages,
    extract_agent_memories,
    parse_memory_extraction_response,
    persist_extracted_memories,
    schedule_agent_memory_extraction,
)
from services.llm_network_context import get_active_llm_network_audit_context, llm_network_audit_context


def test_build_memory_extraction_messages_uses_agent_type_label():
    messages = build_memory_extraction_messages(
        agent_type="Analyst",
        user_message="Summarize the issue",
        agent_response="The root cause is a missing callback contract.",
    )

    assert messages[0]["role"] == "system"
    assert "memory extraction system" in messages[0]["content"]
    assert "Agent Analyst:" in messages[1]["content"]


def test_parse_memory_extraction_response_strips_code_fence():
    parsed = parse_memory_extraction_response(
        """```json
        [{"content":"Remember this fact","type":"fact","importance":9}]
        ```"""
    )

    assert parsed == [
        {
            "content": "Remember this fact",
            "type": "fact",
            "importance": 9,
        }
    ]


def test_persist_extracted_memories_filters_and_clamps():
    class FakeMemory:
        def __init__(self, **kwargs):
            self.payload = kwargs

    class FakeDB:
        def __init__(self):
            self.added = []
            self.committed = False
            self.rolled_back = False

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    db = FakeDB()
    persisted = persist_extracted_memories(
        db,
        memory_cls=FakeMemory,
        agent_id=7,
        memories=[
            {"content": "short", "type": "fact", "importance": 9},
            {"content": "First durable memory", "type": "fact", "importance": 99},
            {"content": "Second durable memory", "type": "decision", "importance": -5},
            {"content": "Third durable memory", "type": "context", "importance": 4},
            {"content": "Fourth durable memory", "type": "preference", "importance": 6},
        ],
    )

    assert persisted == 2
    assert db.committed is True
    assert db.rolled_back is False
    assert [item.payload for item in db.added] == [
        {
            "agent_id": 7,
            "memory_type": "fact",
            "content": "First durable memory",
            "importance": 10,
        },
        {
            "agent_id": 7,
            "memory_type": "decision",
            "content": "Second durable memory",
            "importance": 1,
        },
    ]


def test_schedule_agent_memory_extraction_uses_scheduler():
    recorded = {}

    async def extract_memories(agent_id, agent_type, user_message, agent_response):
        recorded["payload"] = (agent_id, agent_type, user_message, agent_response)

    def schedule_task(coro):
        recorded["scheduled"] = coro
        return "task"

    task = schedule_agent_memory_extraction(
        extract_memories,
        agent_id=9,
        agent_type="Analyst",
        user_message="Need help",
        agent_response="Use the shared runtime service.",
        schedule_task=schedule_task,
    )

    assert task == "task"
    assert recorded["scheduled"] is not None
    recorded["scheduled"].close()


def test_llm_network_audit_context_restores_previous_context():
    assert get_active_llm_network_audit_context().call_purpose is None

    with llm_network_audit_context(call_purpose="outer", metadata={"scope": "outer"}):
        assert get_active_llm_network_audit_context().call_purpose == "outer"
        with llm_network_audit_context(
            call_purpose="memory_extraction",
            purpose_label="memory extraction",
            metadata={"memory_agent_type": "valet"},
        ):
            context = get_active_llm_network_audit_context()
            assert context.call_purpose == "memory_extraction"
            assert context.purpose_label == "memory extraction"
            assert context.metadata["memory_agent_type"] == "valet"

        restored = get_active_llm_network_audit_context()
        assert restored.call_purpose == "outer"
        assert restored.metadata["scope"] == "outer"


@pytest.mark.asyncio
async def test_extract_agent_memories_labels_llm_call_context(monkeypatch):
    import llm.client as llm_client

    recorded = {}

    async def fake_chat_framework_llm(messages, **kwargs):
        context = get_active_llm_network_audit_context()
        recorded["messages"] = messages
        recorded["kwargs"] = kwargs
        recorded["context"] = {
            "call_purpose": context.call_purpose,
            "purpose_label": context.purpose_label,
            "metadata": dict(context.metadata),
        }
        return "[]"

    monkeypatch.setattr(llm_client, "chat_framework_llm", fake_chat_framework_llm)

    persisted = await extract_agent_memories(
        7,
        "Valet",
        "What changed?",
        "I updated the network monitor.",
    )

    assert persisted == 0
    assert recorded["kwargs"]["temperature"] == 0.3
    assert recorded["kwargs"]["max_tokens"] == 500
    assert recorded["context"] == {
        "call_purpose": "memory_extraction",
        "purpose_label": "memory extraction",
        "metadata": {
            "memory_agent_type": "valet",
            "memory_extraction": True,
        },
    }
    assert "Agent Valet:" in recorded["messages"][1]["content"]
