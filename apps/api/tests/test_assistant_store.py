from __future__ import annotations

from datetime import datetime, timezone

import pytest

from winnie_assistant.conversations import (
    ConversationNotFound,
    ConversationReadOnly,
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
    assert 'channel: "api"' in stored
    assert read.channel == "api"
    assert "## user · 2026-11-01T02:15:00+00:00" in stored
    assert "## tool:web_search ·" in stored

    assert store.count() == 1
    summaries = store.list()
    assert summaries[0].id == conversation.id
    assert summaries[0].message_count == 3
    assert summaries[0].channel == "api"
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
        channel="task",
        task_id="morning-brief",
        owner="catroll",
        title="Morning brief",
    )
    stored = conversation.path.read_text(encoding="utf-8")
    assert 'source: "task"' in stored
    assert 'channel: "task"' in stored
    assert 'task: "morning-brief"' in stored
    assert 'owner: "catroll"' in stored
    assert conversation.title == "Morning brief"
    assert store.get(conversation.id).source == "task"
    assert store.get(conversation.id).channel == "task"


async def test_list_filters_by_channel_and_pages(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    times = [
        datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
    ]
    await store.create(first_message="from api", channel="api", now=times[0])
    await store.create(first_message="from admin", channel="admin", now=times[1])
    await store.create(first_message="from web", channel="web", now=times[2])

    assert store.count() == 3
    assert store.count(channel="admin") == 1
    assert [item.channel for item in store.list(channel="admin")] == ["admin"]
    assert [item.preview for item in store.list(limit=1, offset=0)] == ["from web"]
    assert [item.preview for item in store.list(limit=1, offset=1)] == ["from admin"]
    assert [item.preview for item in store.list(limit=1, offset=2)] == ["from api"]


async def test_task_conversation_rejects_a_chat_turn(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    conversation = await store.create(
        first_message="Scheduled brief",
        source="task",
        channel="task",
    )

    with pytest.raises(ConversationReadOnly):
        await store.get_or_create(
            first_message="continue",
            conversation_id=conversation.id,
            source="chat",
            channel="admin",
        )

    # The rejected turn must not be appended to the record.
    messages = store.get(conversation.id).messages
    assert len(messages) == 1
    assert messages[0].text == "Scheduled brief"


def test_a_file_without_a_channel_falls_back_to_its_source(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.path_for("20261101T021500Z-abcdef")
    conversation.parent.mkdir(parents=True)
    conversation.write_text(
        "---\n"
        'id: "20261101T021500Z-abcdef"\n'
        'title: "legacy"\n'
        'created_at: "2026-11-01T02:15:00+00:00"\n'
        'source: "chat"\n'
        'owner: "local"\n'
        "---\n\n"
        "# legacy\n\n"
        "## user · 2026-11-01T02:15:00+00:00\n\nhi\n",
        encoding="utf-8",
    )
    assert store.get(conversation.stem).channel == "api"


async def test_text_that_looks_like_a_header_stays_in_the_body(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    conversation = await store.create(
        first_message="## user · not a timestamp\nhello"
    )
    await store.append(conversation.id, "assistant", "ok")

    messages = store.get(conversation.id).messages
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].text == "## user · not a timestamp\nhello"
