"""The assistant's notify tool and its skill availability."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.skills import NOTIFY, ToolboxSkill
from winnie_assistant.tools import ToolBox
from winnie_chat import SkillRegistry


class StubNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def notify(
        self,
        *,
        title: str,
        body: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"title": title, "body": body, "data": data})
        return {"seq": len(self.calls), "title": title}


def toolbox(notifier: StubNotifier | None) -> ToolBox:
    return ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
        notifier=notifier,
    )


@pytest.mark.asyncio
async def test_notify_sends_a_notification() -> None:
    notifier = StubNotifier()
    text = await toolbox(notifier).execute(
        "notify_user",
        {"title": "Standup in 10 minutes", "body": "The 09:30 review starts soon."},
        conversation_id="conv",
    )

    assert text == 'Sent the notification "Standup in 10 minutes".'
    assert notifier.calls == [
        {
            "title": "Standup in 10 minutes",
            "body": "The 09:30 review starts soon.",
            "data": None,
        }
    ]


@pytest.mark.asyncio
async def test_notify_requires_a_title() -> None:
    notifier = StubNotifier()
    text = await toolbox(notifier).execute(
        "notify_user", {"body": "no title"}, conversation_id="conv"
    )

    assert text.startswith("ERROR:")
    assert "title" in text
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_without_push_the_tool_reports_it() -> None:
    text = await toolbox(None).execute(
        "notify_user", {"title": "Hello"}, conversation_id="conv"
    )

    assert text == "ERROR: push notifications are disabled"


def test_notify_skill_is_available_and_safe_in_task_runs() -> None:
    notifier = StubNotifier()
    skill = ToolboxSkill(NOTIFY, toolbox(notifier))
    registry = SkillRegistry([skill])

    interactive = {
        schema["function"]["name"] for schema in registry.schemas()
    }
    scheduled = {
        schema["function"]["name"] for schema in registry.schemas(scheduled=True)
    }

    assert interactive == {"notify_user"}
    assert scheduled == {"notify_user"}
    assert registry.views()[0].scheduled_safe is True


def test_notify_skill_reports_unavailable_without_push() -> None:
    skill = ToolboxSkill(
        NOTIFY,
        toolbox(None),
        available=False,
        unavailable_reason="push notifications are disabled",
    )
    registry = SkillRegistry([skill])

    assert registry.schemas() == []
    view = registry.views()[0]
    assert view.available is False
    assert view.unavailable_reason == "push notifications are disabled"
