"""异步调用积木：chain + 串行 / gather / 信号量限流 / 超时。"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence

from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate


def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)


PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "用一两句中文回答。不要废话。"),
        ("human", "{question}"),
    ]
)


def build_chain(model):
    return PROMPT | model | StrOutputParser()


DEFAULT_TASKS = [
    "一句话解释 asyncio",
    "一句话解释 ainvoke",
    "一句话解释并发限流",
]


async def run_serial(chain, questions: Sequence[str], config: dict) -> list[str]:
    """串行：一个接一个 ainvoke。"""
    out: list[str] = []
    for q in questions:
        out.append(await chain.ainvoke({"question": q}, config=config))
    return out


async def run_gather(chain, questions: Sequence[str], config: dict) -> list[str]:
    """并行：asyncio.gather 同时 ainvoke。"""
    return list(
        await asyncio.gather(
            *[chain.ainvoke({"question": q}, config=config) for q in questions]
        )
    )


async def run_limited(
    chain,
    questions: Sequence[str],
    config: dict,
    *,
    limit: int = 2,
) -> list[str]:
    """限流：信号量限制同时进行的 LLM 调用数。"""
    sem = asyncio.Semaphore(limit)

    async def one(q: str) -> str:
        async with sem:
            return await chain.ainvoke({"question": q}, config=config)

    return list(await asyncio.gather(*[one(q) for q in questions]))


async def run_with_timeout(
    chain,
    question: str,
    config: dict,
    *,
    timeout: float = 30.0,
) -> str:
    """超时：asyncio.wait_for 包住 ainvoke。"""
    return await asyncio.wait_for(
        chain.ainvoke({"question": question}, config=config),
        timeout=timeout,
    )


async def run_astream_one(chain, question: str, config: dict) -> str:
    """复习：异步流式（与第 8 章衔接）。"""
    parts: list[str] = []
    async for chunk in chain.astream({"question": question}, config=config):
        parts.append(chunk)
        print(chunk, end="", flush=True)
    print()
    return "".join(parts)


async def timed(label: str, coro: Awaitable) -> tuple[str, float, object]:
    t0 = time.perf_counter()
    result = await coro
    return label, time.perf_counter() - t0, result


# 简易「失败重试」示意（教学用，非生产 Retry 中间件）
async def ainvoke_with_retry(
    fn: Callable[[], Awaitable[str]],
    *,
    retries: int = 2,
) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — demo
            last = exc
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (attempt + 1))
    assert last is not None
    raise last
