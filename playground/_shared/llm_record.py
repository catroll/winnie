"""Playground 共用：将每一次程序↔LLM 交互落盘为 ask/answer 文本。

文件名：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`
目录：各示例子目录下的 `logs/`（见 `.cursor/rules/playground-llm-logs.mdc`）
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult


def ensure_logs_dir(logs_dir: Path) -> Path:
    path = logs_dir.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serialize_messages(messages: list[BaseMessage] | list[list[BaseMessage]]) -> str:
    if messages and isinstance(messages[0], list):
        batches = messages
    else:
        batches = [messages]  # type: ignore[list-item]

    lines: list[str] = []
    for bi, batch in enumerate(batches):
        if len(batches) > 1:
            lines.append(f"## batch {bi}")
        for msg in batch:
            role = type(msg).__name__
            content = getattr(msg, "content", "")
            extra = ""
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                extra = "\ntool_calls: " + json.dumps(
                    tool_calls, ensure_ascii=False, default=str
                )
            lines.append(f"### {role}\n{content}{extra}\n")
    return "\n".join(lines).rstrip() + "\n"


def _serialize_llm_result(response: LLMResult) -> str:
    parts: list[str] = []
    for gens in response.generations:
        for gen in gens:
            if isinstance(gen, ChatGeneration):
                msg = gen.message
                role = type(msg).__name__
                content = getattr(msg, "content", "")
                tool_calls = getattr(msg, "tool_calls", None)
                block = f"### {role}\n{content}\n"
                if tool_calls:
                    block += (
                        "tool_calls: "
                        + json.dumps(tool_calls, ensure_ascii=False, default=str)
                        + "\n"
                    )
                parts.append(block)
            else:
                parts.append(f"### Generation\n{getattr(gen, 'text', gen)}\n")
    if response.llm_output:
        parts.append(
            "### llm_output\n"
            + json.dumps(response.llm_output, ensure_ascii=False, default=str)
            + "\n"
        )
    return "\n".join(parts).rstrip() + "\n"


class LlmInteractionRecorder(BaseCallbackHandler):
    """每次 chat/LLM 调用写一对 ask/answer 文件到示例 logs/。"""

    def __init__(self, logs_dir: Path, *, example_id: str = "") -> None:
        super().__init__()
        self.example_id = example_id or logs_dir.parent.name
        self.dir = ensure_logs_dir(logs_dir)
        self._lock = threading.Lock()
        self._seq = 0
        self._pending: dict[UUID, str] = {}

    def _next_prefix(self) -> str:
        now = datetime.now()
        with self._lock:
            self._seq += 1
            seq = self._seq
        return f"{now.strftime('%Y%m%d-%H%M%S')}-{seq:09d}"

    def _write(self, prefix: str, kind: str, body: str) -> Path:
        path = self.dir / f"{prefix}-{kind}.txt"
        header = (
            f"# example: {self.example_id}\n"
            f"# kind: {kind}\n"
            f"# recorded_at: {datetime.now().isoformat(timespec='seconds')}\n\n"
        )
        path.write_text(header + body, encoding="utf-8")
        return path

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        prefix = self._next_prefix()
        self._pending[run_id] = prefix
        model = (serialized or {}).get("id") or (serialized or {}).get("name") or ""
        body = f"model: {model}\nparent_run_id: {parent_run_id}\n\n"
        body += _serialize_messages(messages)
        path = self._write(prefix, "ask", body)
        print(f"[llm-record] ask → {path}")

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        prefix = self._pending.pop(run_id, None) or self._next_prefix()
        body = _serialize_llm_result(response)
        path = self._write(prefix, "answer", body)
        print(f"[llm-record] answer → {path}")

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        prefix = self._pending.pop(run_id, None) or self._next_prefix()
        path = self._write(
            prefix, "answer", f"ERROR: {type(error).__name__}: {error}\n"
        )
        print(f"[llm-record] answer(error) → {path}")
