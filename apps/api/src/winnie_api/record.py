"""本地沟通日志：ask / answer 成对落盘。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request


def client_request_url(request: Request) -> str:
    """客户打到本代理的完整 URL。"""
    return str(request.url)


class InteractionRecorder:
    """每一轮代理转发写一对 ask/answer 文件。"""

    def __init__(self, logs_dir: Path, *, service: str = "openai-proxy") -> None:
        self.service = service
        self.dir = logs_dir.resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0

    def begin(
        self,
        *,
        client_url: str,
        upstream_url: str,
        method: str = "POST",
        body: Any = None,
        headers_meta: dict[str, Any] | None = None,
    ) -> str:
        prefix = self._next_prefix()
        payload: dict[str, Any] = {
            "client_url": client_url,
            "upstream_url": upstream_url,
            "method": method,
            "body": body,
        }
        if headers_meta:
            payload["meta"] = headers_meta
        self._write(
            prefix,
            "ask",
            payload,
            extra_headers={
                "client_url": client_url,
                "upstream_url": upstream_url,
                "method": method,
            },
        )
        return prefix

    def end_json(self, prefix: str, body: Any, *, status_code: int | None = None) -> Path:
        payload: dict[str, Any] = {"body": body}
        if status_code is not None:
            payload["status_code"] = status_code
        return self._write(prefix, "answer", payload)

    def end_stream(self, prefix: str, *, text: str, raw_events: list[str], status_code: int) -> Path:
        return self._write(
            prefix,
            "answer",
            {
                "status_code": status_code,
                "stream": True,
                "text": text,
                "event_count": len(raw_events),
                "raw_sse": raw_events,
            },
        )

    def end_error(self, prefix: str, error: BaseException | str, *, status_code: int | None = None) -> Path:
        msg = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        payload: dict[str, Any] = {"error": msg}
        if status_code is not None:
            payload["status_code"] = status_code
        return self._write(prefix, "answer", payload)

    def _next_prefix(self) -> str:
        now = datetime.now()
        with self._lock:
            self._seq += 1
            seq = self._seq
        return f"{now.strftime('%Y%m%d-%H%M%S')}-{seq:09d}"

    def _write(
        self,
        prefix: str,
        kind: str,
        payload: Any,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> Path:
        path = self.dir / f"{prefix}-{kind}.txt"
        lines = [
            f"# service: {self.service}",
            f"# kind: {kind}",
            f"# recorded_at: {datetime.now().isoformat(timespec='seconds')}",
        ]
        if extra_headers:
            for key, value in extra_headers.items():
                lines.append(f"# {key}: {value}")
        header = "\n".join(lines) + "\n\n"
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        path.write_text(header + body + "\n", encoding="utf-8")
        return path
