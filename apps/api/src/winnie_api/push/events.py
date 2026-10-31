"""Turn phone-command lifecycle changes into push events.

Approving a command publishes a device-targeted ``sync`` control event, so the
phone executes it immediately instead of waiting for its periodic sync. The
result publishes a user-facing notification, which is how the owner learns that
the calendar changed or the SMS was submitted.
"""

from __future__ import annotations

import uuid
from typing import Any

from winnie_push.service import PushService


class PhoneEventsAdapter:
    def __init__(self, service: PushService) -> None:
        self.service = service

    async def command_approved(
        self, user_id: str, device_id: uuid.UUID, command: dict[str, Any]
    ) -> None:
        await self.service.publish(
            user_id,
            kind="sync",
            title="",
            body="",
            device_id=device_id,
            data={
                "reason": "command_approved",
                "command_id": str(command.get("id", "")),
            },
        )

    async def command_completed(
        self, user_id: str, device_id: uuid.UUID, command: dict[str, Any]
    ) -> None:
        succeeded = command.get("status") == "succeeded"
        summary = str(command.get("summary") or command.get("kind") or "phone action")
        detail = str(
            command.get("result") if succeeded else command.get("error") or ""
        ).strip()
        title = "Phone action completed" if succeeded else "Phone action failed"
        body = f"{summary}. {detail}".strip()
        await self.service.publish(
            user_id,
            kind="phone_command",
            title=title,
            body=body,
            device_id=device_id,
            data={
                "command_id": str(command.get("id", "")),
                "status": str(command.get("status", "")),
            },
        )
