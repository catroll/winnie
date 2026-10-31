from __future__ import annotations

from datetime import datetime, timezone

import pytest

from winnie_assistant.conversations import (
    ConversationNotFound,
    ConversationStore,
    InvalidConversationId,
)


async def test_conversation_round_trip(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    now = datetime(2026, 11, 1, 2, 15, tzinfo=timezone.utc)
    conversation = await store.create(first_message="明天的出行安排", now=now)

    assert conversation.id.startswith("20261101T021500Z-")
    assert conversation.title == "明天的出行安排"
    assert store.path_for(conversation.id) == (
        tmp_path / "2026-11-01" / f"{conversation.id}.md"
    )

    await store.append(conversation.id, "assistant", "记得带伞。", now=now)
    await store.append(
        conversation.id, "tool", "1. 天气\n   https://example.com", name="web_search"
    )

    read = store.get(conversation.id)
    assert [message.role for message in read.messages] == ["user", "assistant", "tool"]
    assert read.messages[0].text == "明天的出行安排"
    assert read.messages[2].name == "web_search"
    assert read.created_at == now

    stored = read.path.read_text(encoding="utf-8")
    assert 'source: "chat"' in stored
    assert "## user · 2026-11-01T02:15:00+00:00" in stored
    assert "## tool:web_search ·" in stored

    assert store.count() == 1
    summaries = store.list()
    assert summaries[0].id == conversation.id
    assert summaries[0].message_count == 3
    assert summaries[0].preview == "明天的出行安排"


async def test_append_requires_existing_file(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    with pytest.raises(ConversationNotFound):
        await store.append("20260101T000000Z-abcdef", "assistant", "hi")


def test_ids_are_validated_before_touching_the_filesystem(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    for conversation_id in (
        "../../etc/passwd",
        "20260101T000000Z-abcdef/../../x",
        "2026-01-01-x",
        "",
    ):
        with pytest.raises(InvalidConversationId):
            store.path_for(conversation_id)

    with pytest.raises(ConversationNotFound):
        store.get("20260101T000000Z-abcdef")


async def test_create_with_task_title_and_owner(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    conversation = await store.create(
        first_message="Summarise the news",
        source="task",
        task_id="morning-brief",
        owner="catroll",
        title="Morning brief",
    )
    stored = conversation.path.read_text(encoding="utf-8")
    assert 'source: "task"' in stored
    assert 'task: "morning-brief"' in stored
    assert 'owner: "catroll"' in stored
    assert conversation.title == "Morning brief"
    assert store.get(conversation.id).source == "task"


async def test_text_that_looks_like_a_header_stays_in_the_body(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    conversation = await store.create(
        first_message="## user · not a timestamp\nhello"
    )
    await store.append(conversation.id, "assistant", "ok")

    messages = store.get(conversation.id).messages
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].text == "## user · not a timestamp\nhello"
