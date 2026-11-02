"""The assistant's read-only mail tools and their skill availability.

The mailbox is a data source here: search and open, never send. A tool that
could send would let a message body talk the assistant into writing to someone.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.skills import MAIL, ToolboxSkill
from winnie_assistant.tools import ToolBox
from winnie_chat import SkillRegistry

MESSAGE = {
    "id": "a1b2c3d4-0000-0000-0000-000000000001",
    "folder": "INBOX",
    "folder_display": "Inbox",
    "protocol": "imap",
    "uid": "12",
    "subject": "Your booking is confirmed",
    "sender": "hotel@example.com",
    "recipients": "winnie@example.com",
    "date": "2026-11-02T14:00:08",
    "snippet": "Check-in on 12 November after 15:00.",
    "unread": True,
    "size": 400,
    "has_body": True,
    "message_id": "<abc@example.com>",
    "body": "",
}


class StubMail:
    def __init__(self) -> None:
        self.searches: list[dict[str, Any]] = []
        self.opened: list[str] = []
        self.messages: list[dict[str, Any]] = [MESSAGE]

    async def search(
        self,
        query: str,
        *,
        folder: str = "",
        unread_only: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]:
        self.searches.append(
            {"query": query, "folder": folder, "unread_only": unread_only, "limit": limit}
        )
        return {
            "synced_at": "2026-11-02T15:00:00",
            "messages": self.messages,
        }

    async def message(self, message_id: str) -> dict[str, Any] | None:
        self.opened.append(message_id)
        if message_id != MESSAGE["id"]:
            return None
        return {**MESSAGE, "body": "Check-in on 12 November after 15:00.\nRoom 402."}


def toolbox(mail: StubMail | None, zone: str = "UTC") -> ToolBox:
    return ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
        mail=mail,
        timezone=ZoneInfo(zone),
    )


@pytest.mark.asyncio
async def test_search_mail_returns_the_cached_matches() -> None:
    mail = StubMail()
    text = await toolbox(mail).execute(
        "search_mail",
        {"query": "booking", "folder": "INBOX", "limit": 5},
        conversation_id="conv",
    )

    assert "1 matching messages" in text
    assert MESSAGE["id"] in text
    assert "From: hotel@example.com" in text
    assert "| Inbox" in text
    assert "Subject: Your booking is confirmed" in text
    assert "[unread]" in text
    assert mail.searches == [
        {"query": "booking", "folder": "INBOX", "unread_only": False, "limit": 5}
    ]


@pytest.mark.asyncio
async def test_search_mail_reports_an_empty_cache_with_its_age() -> None:
    mail = StubMail()
    mail.messages = []
    text = await toolbox(mail).execute(
        "search_mail", {"query": "nothing"}, conversation_id="conv"
    )

    assert "No cached message matches" in text
    assert "2026-11-02 15:00" in text


@pytest.mark.asyncio
async def test_search_mail_spells_out_the_timezone() -> None:
    """A bare ``14:00`` is read as local time by whoever sees it; UTC+08:00 is not.

    The cache stores naive UTC. The offset belongs in the text handed to the
    model, because the model cannot see the column type.
    """
    text = await toolbox(StubMail(), zone="Asia/Shanghai").execute(
        "search_mail", {"query": "booking"}, conversation_id="conv"
    )

    assert "2026-11-02 22:00 UTC+08:00" in text
    assert "2026-11-02 23:00 UTC+08:00" in text


@pytest.mark.asyncio
async def test_search_mail_requires_a_query() -> None:
    mail = StubMail()
    text = await toolbox(mail).execute(
        "search_mail", {"query": "   "}, conversation_id="conv"
    )

    assert text.startswith("ERROR:")
    assert "query" in text
    assert mail.searches == []


@pytest.mark.asyncio
async def test_read_mail_returns_the_body() -> None:
    mail = StubMail()
    text = await toolbox(mail).execute(
        "read_mail", {"message_id": MESSAGE["id"]}, conversation_id="conv"
    )

    assert "Room 402." in text
    assert "Subject: Your booking is confirmed" in text
    assert mail.opened == [MESSAGE["id"]]


@pytest.mark.asyncio
async def test_read_mail_reports_a_message_that_left_the_cache() -> None:
    text = await toolbox(StubMail()).execute(
        "read_mail", {"message_id": "gone"}, conversation_id="conv"
    )

    assert text == "ERROR: that message is not in the cache any more"


@pytest.mark.asyncio
async def test_without_a_mailbox_the_tool_reports_it() -> None:
    text = await toolbox(None).execute(
        "search_mail", {"query": "booking"}, conversation_id="conv"
    )

    assert text == "ERROR: the mailbox is not configured"


def test_mail_skill_is_available_and_safe_in_task_runs() -> None:
    skill = ToolboxSkill(MAIL, toolbox(StubMail()))
    registry = SkillRegistry([skill])

    interactive = {schema["function"]["name"] for schema in registry.schemas()}
    scheduled = {schema["function"]["name"] for schema in registry.schemas(scheduled=True)}

    assert interactive == {"search_mail", "read_mail"}
    assert scheduled == {"search_mail", "read_mail"}
    assert registry.views()[0].scheduled_safe is True


def test_mail_skill_reports_unavailable_without_a_mailbox() -> None:
    skill = ToolboxSkill(
        MAIL,
        toolbox(None),
        available=False,
        unavailable_reason="the mailbox is not configured",
    )
    registry = SkillRegistry([skill])

    assert registry.schemas() == []
    assert registry.views()[0].available is False


def test_there_is_no_mail_write_tool() -> None:
    from winnie_assistant.tools import TOOL_SCHEMAS

    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert "send_mail" not in names
    assert {"search_mail", "read_mail"} <= names
