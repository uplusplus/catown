from services.memory_extraction import (
    build_memory_extraction_messages,
    parse_memory_extraction_response,
    persist_extracted_memories,
)


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
