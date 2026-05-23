from services.assistant_handoff import build_handoff_metadata, build_handoff_trigger_content, handoff_targets


def test_handoff_targets_support_multiple_mentions_and_filter_self():
    targets = handoff_targets(
        content="@tester please sync with @developer and @tester on this",
        agent_name="valet",
        metadata={},
    )
    assert targets == ["tester", "developer"]


def test_handoff_targets_require_leading_mention_and_stop_on_depth():
    assert handoff_targets(content="please ask @tester", agent_name="valet", metadata={}) == []
    assert handoff_targets(content="@tester do this", agent_name="valet", metadata={"handoff_depth": 1}) == ["tester"]
    assert handoff_targets(content="@tester do this", agent_name="valet", metadata={"handoff_depth": 2}) == []


def test_handoff_targets_accept_tail_paragraph_mentions_only():
    targets = handoff_targets(
        content="Background first.\n\n@tester @developer please inspect the backend",
        agent_name="valet",
        metadata={},
    )
    assert targets == ["tester", "developer"]


def test_handoff_targets_do_not_trigger_when_only_middle_paragraph_mentions():
    targets = handoff_targets(
        content="Background first.\n\n@developer please inspect the backend\n\nClosing summary only.",
        agent_name="valet",
        metadata={},
    )
    assert targets == []


def test_handoff_targets_ignore_mentions_inside_fenced_code_blocks():
    targets = handoff_targets(
        content="Background first.\n\n```text\n@developer please inspect the backend\n```",
        agent_name="valet",
        metadata={},
    )
    assert targets == []


def test_build_handoff_metadata_increments_depth_for_two_hop_roundtrip():
    first = build_handoff_metadata({}, from_agent="valet", to_agent="tester", source_message_id=1)
    second = build_handoff_metadata(first, from_agent="tester", to_agent="valet", source_message_id=2)
    assert first["handoff_depth"] == 1
    assert second["handoff_depth"] == 2


def test_build_handoff_trigger_content_targets_one_agent_only():
    content = "@tester @developer please inspect the backend"
    assert build_handoff_trigger_content(content, target_agent="tester") == "@tester please inspect the backend"
    assert build_handoff_trigger_content(content, target_agent="developer") == "@developer please inspect the backend"


def test_build_handoff_trigger_content_rewrites_tail_paragraph_only():
    content = "Background first.\n\n@tester @developer please inspect the backend"
    assert build_handoff_trigger_content(content, target_agent="developer") == "@developer please inspect the backend"
