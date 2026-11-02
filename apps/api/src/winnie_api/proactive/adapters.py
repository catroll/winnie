"""Adapters that connect the proactive engine to the host's capabilities.

The engine knows four small protocols; this module is where the phone service,
the weekly schedule, the push service and the channel configuration meet them.
It is the only place that imports both sides, which keeps `winnie-proactive`
free of any sibling capability.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

import httpx

from winnie_mail.client import MailError
from winnie_proactive.channels import EmailChannel, NtfyChannel
from winnie_proactive.ports import Channel, ChannelError, Message

COMPOSE_PROMPT = """\
你在为一位个人助理改写要发给主人的提醒正文。

规则：
- 只输出正文，不要标题、编号、Markdown 标题或解释。
- 原文里的日期、星期、姓名、数字一个字都不要改；不要新增日期或事实。
- 补一句具体、可执行的下一步建议（不超过一句）。不知道的事情不要编。
- 用主人使用的语言，最多三句话，语气直接、像熟人提醒。
"""

OwnerResolver = Callable[[], Awaitable[str]]


class PushChannel:
    """The self-hosted app push, through the existing push capability."""

    name = "push"

    def __init__(self, service, *, resolve_owner: OwnerResolver) -> None:
        self.service = service
        self.resolve_owner = resolve_owner

    async def send(self, message: Message) -> str:
        owner = await self.resolve_owner()
        await self.service.notify(
            owner,
            title=message.title,
            body=message.body,
            data=dict(message.data),
        )
        return "push"


class UserMailChannel:
    """The email rung, sent from the mailbox the owner configured in Settings.

    The stored account wins over the ``proactive.channels.email`` block: that
    block cannot hold a password and can only read one from the environment,
    while the console lets the owner point the rung at any provider. The
    environment-backed channel stays as the fallback so an existing deployment
    keeps working before the first save.
    """

    name = "email"

    def __init__(
        self,
        service,
        *,
        resolve_owner: OwnerResolver,
        fallback: EmailChannel | None = None,
    ) -> None:
        self.service = service
        self.resolve_owner = resolve_owner
        self.fallback = fallback

    async def send(self, message: Message) -> str:
        owner = await self.resolve_owner()
        settings = self.service.load(owner) if owner else None
        if settings is None or not settings.can_send():
            if self.fallback is not None:
                return await self.fallback.send(message)
            raise ChannelError("no mailbox is configured in Settings")
        body = message.body or message.title
        ack_url = str(message.data.get("ack_url") or "")
        if ack_url:
            body += f"\n\n已收到：{ack_url}"
        try:
            return await self.service.send(
                owner,
                to=settings.address,
                subject=message.title,
                body=body,
            )
        except MailError as exc:
            raise ChannelError(f"mail: {exc}") from exc


class ModelComposer:
    """Ask the bound chat model to improve the wording of one reminder.

    The engine stores the result on the item, so the cost is one call per item
    and an escalation re-sends the same text. A model failure is not a delivery
    failure: the adapter raises and the engine keeps the template.
    """

    def __init__(
        self,
        model,
        *,
        max_chars: int = 600,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.model = model
        self.max_chars = max_chars
        self.timeout_seconds = timeout_seconds

    async def compose(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        due_at,
        evidence: list[dict[str, Any]],
    ) -> str:
        when = due_at.isoformat() if due_at is not None else ""
        user = (
            f"类型: {kind}\n标题: {title}\n原文: {body}\n"
            f"时间: {when}\n来源: {evidence}"
        )
        result = await asyncio.wait_for(
            self.model.chat(
                [
                    {"role": "system", "content": COMPOSE_PROMPT},
                    {"role": "user", "content": user},
                ]
            ),
            timeout=self.timeout_seconds,
        )
        text = (getattr(result, "text", "") or "").strip()
        if not text:
            raise ValueError("the composer returned no text")
        return text[: self.max_chars]


class PhoneCalendarAdapter:
    """Calendar occurrences, already expanded by the phone capability."""

    def __init__(self, service, *, resolve_owner: OwnerResolver) -> None:
        self.service = service
        self.resolve_owner = resolve_owner

    async def upcoming(self, *, since, until) -> list[dict[str, Any]]:
        owner = await self.resolve_owner()
        return await self.service.calendar(
            owner, since=since, until=until, limit=500
        )


class PhoneContactsAdapter:
    def __init__(self, service, *, resolve_owner: OwnerResolver) -> None:
        self.service = service
        self.resolve_owner = resolve_owner

    async def all(self) -> list[dict[str, Any]]:
        owner = await self.resolve_owner()
        return await self.service.contacts(owner, limit=2000)


class ScheduleAdapter:
    """The owner's weekly Markdown schedule, flattened into dated events.

    A read walks the weeks that overlap the range rather than every day: one
    parse per week, and a missing week file simply contributes nothing.
    """

    def __init__(self, service) -> None:
        self.service = service

    async def events(self, *, since: date, until: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        day = since
        seen: set[str] = set()
        while day <= until:
            try:
                week = await asyncio.to_thread(self.service.week, day)
            except Exception:  # noqa: BLE001 - a missing week is not an error
                day += timedelta(days=7)
                continue
            for section in week.get("days") or []:
                try:
                    on = date.fromisoformat(str(section.get("date")))
                except ValueError:
                    continue
                if not (since <= on <= until):
                    continue
                for event in section.get("events") or []:
                    key = f"{on.isoformat()}:{event.get('id')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "id": str(event.get("id") or key),
                            "date": on.isoformat(),
                            "start": str(event.get("start") or ""),
                            "end": str(event.get("end") or ""),
                            "text": str(event.get("text") or ""),
                            "done": bool(event.get("done")),
                            "scope": str(event.get("scope") or "day"),
                        }
                    )
            day += timedelta(days=7)
        return rows


def build_channels(
    config,
    *,
    http: httpx.AsyncClient,
    push_service,
    resolve_owner: OwnerResolver,
    mail_service=None,
) -> dict[str, Channel]:
    """Only channels that can actually deliver are registered.

    A rung that is configured but unreachable makes the ladder look longer than
    it is; an absent one is skipped at send time.
    """
    channels: dict[str, Channel] = {}
    if push_service is not None:
        channels["push"] = PushChannel(push_service, resolve_owner=resolve_owner)
    ntfy = config.channels.ntfy
    if ntfy.enabled:
        channel = NtfyChannel(ntfy, http=http)
        if channel.topic:
            channels["ntfy"] = channel
    email = config.channels.email
    fallback = (
        EmailChannel(email) if email.enabled and email.to and email.smtp_host else None
    )
    # Register the rung only if some mailbox exists to send from: a channel
    # that can never deliver would be walked on every item and fail each time.
    if mail_service is not None and (fallback is not None or mail_service.has_accounts()):
        channels["email"] = UserMailChannel(
            mail_service, resolve_owner=resolve_owner, fallback=fallback
        )
    elif fallback is not None:
        channels["email"] = fallback
    return channels
