"""AiCallLogHandler：把一次 LLM/Tool 调用收成工程向日志。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

@dataclass
class AiCallRecord:
    """一次模型调用的结构化日志（可落盘 / 可上报）。"""

    started_at: str
    ended_at: str | None = None
    latency_ms: float | None = None
    model: str | None = None
    prompt: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd_estimate: float | None = None
    error: str | None = None
    tool_starts: list[str] = field(default_factory=list)
    run_id: str | None = None

def _messages_to_prompt(messages: list[list[BaseMessage]] | list[BaseMessage]) -> str:
    batches = messages if messages and isinstance(messages[0], list) else [messages]
    lines: list[str] = []
    for batch in batches:  # type: ignore[assignment]
        for msg in batch:
            role = type(msg).__name__.replace("Message", "")
            content = getattr(msg, "content", "")
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)

def _estimate_cost(prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    if prompt_tokens is None and completion_tokens is None:
        return None
    pin = float(os.getenv("PRICE_INPUT_PER_1M", "0.15"))
    pout = float(os.getenv("PRICE_OUTPUT_PER_1M", "0.60"))
    cost = ((prompt_tokens or 0) / 1_000_000.0) * pin + (
        (completion_tokens or 0) / 1_000_000.0
    ) * pout
    return round(cost, 8)

class AiCallLogHandler(BaseCallbackHandler):
    """
    输出字段：Prompt / Token / Latency / Model / Cost / Error
    （另记录 Tool Start，便于 Agent 场景）
    """

    def __init__(self, log_dir: Path) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.records: list[AiCallRecord] = []
        self._pending: dict[UUID, tuple[AiCallRecord, float]] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        inv = invocation_params or kwargs.get("invocation_params") or {}
        meta = metadata or {}
        model = (
            inv.get("model")
            or inv.get("model_name")
            or meta.get("ls_model_name")
            or (serialized or {}).get("name")
        )
        rec = AiCallRecord(
            started_at=datetime.now().isoformat(timespec="seconds"),
            model=str(model) if model else None,
            prompt=_messages_to_prompt(messages),
            run_id=str(run_id),
        )
        self._pending[run_id] = (rec, time.perf_counter())

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        item = self._pending.pop(run_id, None)
        if not item:
            return
        rec, t0 = item
        rec.ended_at = datetime.now().isoformat(timespec="seconds")
        rec.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        usage = (response.llm_output or {}).get("token_usage") or {}
        # 兼容不同网关字段名
        rec.prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        rec.completion_tokens = usage.get("completion_tokens") or usage.get(
            "output_tokens"
        )
        rec.total_tokens = usage.get("total_tokens") or (
            (rec.prompt_tokens or 0) + (rec.completion_tokens or 0)
            if rec.prompt_tokens is not None or rec.completion_tokens is not None
            else None
        )
        if not rec.model:
            rec.model = (response.llm_output or {}).get("model_name")
        rec.cost_usd_estimate = _estimate_cost(rec.prompt_tokens, rec.completion_tokens)
        self._commit(rec)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        item = self._pending.pop(run_id, None)
        if item:
            rec, t0 = item
            rec.ended_at = datetime.now().isoformat(timespec="seconds")
            rec.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        else:
            rec = AiCallRecord(
                started_at=datetime.now().isoformat(timespec="seconds"),
                ended_at=datetime.now().isoformat(timespec="seconds"),
                run_id=str(run_id),
            )
        rec.error = f"{type(error).__name__}: {error}"
        self._commit(rec)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        name = (serialized or {}).get("name") or "tool"
        line = f"{name} input={input_str[:200]}"
        # 挂到最近一次未结束的 LLM 记录；若无则单独打印
        if self._pending:
            rec, _ = next(reversed(list(self._pending.values())))
            rec.tool_starts.append(line)
        print(f"[tool_start] {line}")

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        print(f"[tool_error] {type(error).__name__}: {error}")

    def _commit(self, rec: AiCallRecord) -> None:
        self.records.append(rec)
        self._print(rec)
        path = self.log_dir / f"ai-call-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
        path.write_text(
            json.dumps(asdict(rec), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[ai-log] → {path}")

    @staticmethod
    def _print(rec: AiCallRecord) -> None:
        print("\n—— AI 调用日志 ——")
        print(f"Model   : {rec.model}")
        print(f"Latency : {rec.latency_ms} ms")
        print(
            f"Token   : prompt={rec.prompt_tokens}  "
            f"completion={rec.completion_tokens}  total={rec.total_tokens}"
        )
        print(f"Cost    : ${rec.cost_usd_estimate} (estimate)")
        print(f"Error   : {rec.error}")
        if rec.tool_starts:
            print(f"Tools   : {rec.tool_starts}")
        print("Prompt  :")
        print(rec.prompt or "")
        print("——————\n")
