"""Build the assistant service from host-resolved paths and settings."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from winnie_core.config import LLMConfig
from winnie_core.llm.openai_compatible import OpenAICompatibleLLM
from winnie_core.usage.ledger import UsageSink

from winnie_assistant.agent import AssistantAgent, ChatModel
from winnie_assistant.config import AssistantConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.fake import FakeChatModel
from winnie_assistant.index_client import IndexClient
from winnie_local import (
    AuditLog,
    CommandRunner,
    LLMReviewer,
    LocalExecutor,
    PendingActions,
    Whitelist,
    Workspace,
)
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
    model: ChatModel | None = None,
    model_name: str = "",
    usage: UsageSink | None = None,
) -> AssistantService:
    timezone = _timezone(config.timezone)
    resolved_name = model_name or (config.model or llm.model or "fake").strip()
    resolved_model = model or _build_model(resolved_name, llm, http, usage)
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

    workspace = _build_workspace(config, root)
    commands = _build_commands(config, workspace, root)
    local = _build_local(
        config, root, workspace, commands, resolved_model, resolved_name
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
            model=resolved_model,
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
        local=local,
    )
    agent = AssistantAgent(
        store=store,
        memory=memory,
        toolbox=toolbox,
        model=resolved_model,
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
        model=resolved_model,
        model_name=resolved_name,
        search=search,
        index=index,
        timezone=timezone,
        clock=clock,
    )
    holder["service"] = service
    return service


def _expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _path_from(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _build_workspace(config: AssistantConfig, root: Path) -> Workspace | None:
    if not config.workspace.enabled:
        return None
    workspace_root = _expand_path(config.workspace.root)
    protected = [
        root / "conversations",
        root / "profile",
        root / ".state",
        root / "tasks",
        *[
            _path_from(workspace_root, value)
            for value in config.workspace.protected
        ],
    ]
    state = root / ".state"
    return Workspace(
        root=workspace_root,
        extra_roots=[
            _path_from(workspace_root, value)
            for value in config.workspace.extra_roots
        ],
        protected=protected,
        max_read_bytes=config.workspace.max_read_bytes,
        max_write_bytes=config.workspace.max_write_bytes,
        max_list_entries=config.workspace.max_list_entries,
        confirm_writes=config.workspace.confirm_writes,
        audit=AuditLog(state / "files.jsonl"),
        backup_dir=state / "backups",
    )


def _build_commands(
    config: AssistantConfig, workspace: Workspace | None, root: Path
) -> CommandRunner | None:
    if not config.exec.enabled:
        return None
    base = workspace.root if workspace is not None else Path.home()
    workdir = _path_from(base, config.exec.workdir or ".")
    roots = list(workspace.roots) if workspace is not None else [workdir]
    if workdir not in roots and not any(
        workdir.is_relative_to(candidate) for candidate in roots
    ):
        roots.append(workdir)
    return CommandRunner(
        roots=roots,
        workdir=workdir,
        timeout_seconds=config.exec.timeout_seconds,
        max_output_chars=config.exec.max_output_chars,
        allow=config.exec.allow,
        extra_env=config.exec.env,
        require_confirmation=config.exec.require_confirmation,
        audit=AuditLog(root / ".state" / "exec.jsonl"),
    )


def _build_local(
    config: AssistantConfig,
    root: Path,
    workspace: Workspace | None,
    commands: CommandRunner | None,
    model: ChatModel,
    model_name: str,
) -> LocalExecutor | None:
    if workspace is None and commands is None:
        return None
    reviewer = None
    if config.local.review.enabled:

        async def complete(system: str, user: str) -> str:
            result = await model.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=None,
            )
            return result.text

        reviewer = LLMReviewer(
            complete=complete,
            model=model_name,
            timeout_seconds=config.local.review.timeout_seconds,
            max_action_chars=config.local.review.max_action_chars,
        )
    return LocalExecutor(
        workspace=workspace,
        commands=commands,
        reviewer=reviewer,
        review_enabled=config.local.review.enabled,
        review_audit=AuditLog(root / ".state" / "review.jsonl"),
        whitelist=Whitelist(
            operations=config.local.whitelist.operations,
            commands=config.local.whitelist.commands,
            git_subcommands=config.local.whitelist.git_subcommands,
        ),
        pending=PendingActions(ttl_seconds=config.exec.pending_ttl_seconds),
        model_name=model_name,
        max_review_detail_chars=config.local.review.max_action_chars,
    )


def _build_model(
    model_name: str,
    llm: LLMConfig,
    http: httpx.AsyncClient,
    usage: UsageSink | None,
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
        usage=usage,
        capability="assistant.chat",
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
