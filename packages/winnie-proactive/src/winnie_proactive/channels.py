"""The two channels the engine owns.

Push is the host's: it wraps the existing push service and is passed in. ntfy
and email are generic HTTP/SMTP clients, so they live here next to the ladder
that uses them. Each channel raises :class:`ChannelError` on failure, and the
ladder moves on instead of treating a failed rung as delivered.
"""

from __future__ import annotations

import asyncio
import base64
import os
import smtplib
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

import httpx

from winnie_proactive.config import EmailConfig, NtfyConfig
from winnie_proactive.ports import ChannelError, Message


def _header(value: str) -> str:
    """RFC 2047 for a non-ASCII Title, which ntfy decodes back to text."""
    if value.isascii():
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{encoded}?="


class NtfyChannel:
    name = "ntfy"

    def __init__(
        self,
        config: NtfyConfig,
        *,
        http: httpx.AsyncClient,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.http = http
        self.environ = environ if environ is not None else os.environ

    async def send(self, message: Message) -> str:
        if not self.config.topic:
            raise ChannelError("ntfy topic is not configured")
        url = f"{self.config.server.rstrip('/')}/{quote(self.config.topic)}"
        headers = {
            "Title": _header(message.title),
            "Priority": self.config.priority,
            "Tags": "bell",
        }
        token = self.environ.get(self.config.token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        ack_url = str(message.data.get("ack_url") or "")
        if ack_url:
            headers["Click"] = ack_url
        body = (message.body or message.title).encode("utf-8")
        try:
            response = await self.http.post(
                url,
                content=body,
                headers=headers,
                timeout=self.config.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ChannelError(f"ntfy transport failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ChannelError(f"ntfy returned HTTP {response.status_code}")
        return f"ntfy {response.status_code}"


class EmailChannel:
    name = "email"

    def __init__(
        self,
        config: EmailConfig,
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.environ = environ if environ is not None else os.environ

    async def send(self, message: Message) -> str:
        return await asyncio.to_thread(self._send, message)

    def _send(self, message: Message) -> str:
        if not (self.config.to and self.config.smtp_host):
            raise ChannelError("email is not configured")
        username = self.environ.get(self.config.username_env, "").strip()
        password = self.environ.get(self.config.password_env, "").strip()
        if not username or not password:
            raise ChannelError(
                f"{self.config.username_env} / {self.config.password_env} are not set"
            )
        sender = self.config.from_address or username
        body = message.body or message.title
        ack_url = str(message.data.get("ack_url") or "")
        if ack_url:
            body += f"\n\n已收到：{ack_url}"
        mail = EmailMessage()
        mail["Subject"] = message.title
        mail["From"] = sender
        mail["To"] = self.config.to
        mail.set_content(body)
        host, port = self.config.smtp_host, self.config.smtp_port
        try:
            if self.config.use_ssl:
                with smtplib.SMTP_SSL(
                    host, port, timeout=self.config.timeout_seconds
                ) as smtp:
                    smtp.login(username, password)
                    smtp.send_message(mail)
            else:
                with smtplib.SMTP(host, port, timeout=self.config.timeout_seconds) as smtp:
                    smtp.starttls()
                    smtp.login(username, password)
                    smtp.send_message(mail)
        except (OSError, smtplib.SMTPException) as exc:
            raise ChannelError(f"smtp failed: {type(exc).__name__}: {exc}") from exc
        return f"smtp {self.config.to}"


__all__ = ["EmailChannel", "NtfyChannel"]
