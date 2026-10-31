"""Build the assistant service from host-resolved paths and settings."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from winnie_core.config import LLMConfig
from winnie_core.llm.openai_compatible import OpenAICompatibleLLM

from winnie_assistant.agent import AssistantAgent, ChatModel
from winnie_assistant.config import AssistantConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.fake import FakeChatModel
from winnie_assistant.index_client import IndexClient
from winnie_assistant.memory import MemoryStore
from winnie_assistant.memory_builder import MemoryBuilder
from winnie_assistant.scheduler import (
    AssistantScheduler,
    ScheduledTask,
    TaskStore,
)
from winnie_assistant.search import BingRSSSearch, FakeSearchProvider, SearchProvider
from winnie_assistant.service import AssistantService
from winnie_assistant.tools import ToolBox


def build_assistant_service(
    *,
    config: AssistantConfig,
    llm: LLMConfig,
    root: Path,
    tasks_file: Path,
    http: httpx.AsyncClient,
    clock=None,
) -> AssistantService:
    timezone = _timezone(config.timezone)
    model_name = (config.model or llm.model or "fake").strip()
    model = _build_model(model_name, llm, http)
    search = _build_search(config, http)
    index = (
        IndexClient(
            url=config.index.url,
            http=http,
            token_env=config.index.token_env,
            timeout_seconds=config.index.timeout_seconds,
            limit=config.index.limit,
        )
        if config.index.enabled
        else None
    )

    store = ConversationStore(root / "conversations")
    memory = (
        MemoryStore(
            root / "profile",
            label="profile",
            digest_chars=config.memory.digest_chars,
        )
        if config.memory.enabled
        else None
    )
    builder = (
        MemoryBuilder(
            store=store,
            memory=memory,  # type: ignore[arg-type]
            model=model,
            config=config,
            state_path=root / ".state" / "memory.json",
            timezone=timezone,
            clock=clock,
        )
        if memory is not None
        else None
    )

    holder: dict[str, AssistantService] = {}

    async def runner(task: ScheduledTask):
        return await holder["service"].chat_task(task)

    scheduler = AssistantScheduler(
        runner=runner,
        tasks=TaskStore(tasks_file),
        state_path=root / ".state" / "scheduler.json",
        runs_path=root / "tasks" / "runs.jsonl",
        timezone=timezone,
        tick_seconds=config.tasks.tick_seconds,
        catch_up_seconds=config.tasks.catch_up_seconds,
        webhook_timeout_seconds=config.tasks.webhook_timeout_seconds,
        http=http,
        clock=clock,
        background=config.tasks.enabled,
    )
    toolbox = ToolBox(
        search=search,
        index=index,
        memory=memory,
        scheduler=scheduler,
        http=http,
        tasks_enabled=config.tasks.enabled,
        max_output_chars=config.max_tool_output_chars,
    )
    agent = AssistantAgent(
        store=store,
        memory=memory,
        toolbox=toolbox,
        model=model,
        config=config,
        timezone=timezone,
        clock=clock,
    )
    service = AssistantService(
        config=config,
        root=root,
        store=store,
        memory=memory,
        builder=builder,
        agent=agent,
        scheduler=scheduler,
        model=model,
        model_name=model_name,
        search=search,
        index=index,
        timezone=timezone,
        clock=clock,
    )
    holder["service"] = service
    return service


def _build_model(
    model_name: str, llm: LLMConfig, http: httpx.AsyncClient
) -> ChatModel:
    if model_name == "fake":
        return FakeChatModel()
    api_key = os.environ.get(llm.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"assistant.chat needs the model API key: set {llm.api_key_env} "
            "(or set assistant.model: fake for an offline demo)"
        )
    if not llm.base_url:
        raise RuntimeError("assistant.chat needs llm.base_url for the model")
    return OpenAICompatibleLLM(
        base_url=llm.base_url,
        api_key=api_key,
        model=model_name,
        timeout_seconds=llm.timeout_seconds,
        max_output_tokens=llm.max_output_tokens,
        temperature=llm.temperature,
        max_retries=llm.max_retries,
        retry_backoff_seconds=llm.retry_backoff_seconds,
        http=http,
    )


def _build_search(
    config: AssistantConfig, http: httpx.AsyncClient
) -> SearchProvider:
    if config.search.provider == "fake":
        return FakeSearchProvider()
    return BingRSSSearch(
        config.search.endpoint,
        timeout_seconds=config.search.timeout_seconds,
        http=http,
    )


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(f"assistant.timezone is not a known zone: {name}") from exc
