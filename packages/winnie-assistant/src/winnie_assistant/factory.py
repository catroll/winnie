"""Build the assistant service from host-resolved paths and settings."""

from __future__ import annotations

import logging
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from winnie_chat import ChatEngine, SkillRegistry
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
from winnie_assistant.memory_tidy import MemoryTidy
from winnie_assistant.news import NewsGateway
from winnie_assistant.notify import Notifier
from winnie_assistant.mail import MailGateway
from winnie_assistant.phone import PhoneGateway
from winnie_assistant.memory_builder import MemoryBuilder
from winnie_assistant.scheduler import (
    AssistantScheduler,
    ScheduledTask,
    TaskStore,
)
from winnie_assistant.search import BingRSSSearch, FakeSearchProvider, SearchProvider
from winnie_assistant.service import AssistantService
from winnie_assistant.skills import build_skills
from winnie_assistant.gold import GoldService, GoldWatch
from winnie_assistant.tools import ToolBox
from winnie_assistant.weather import ForecastCache, WeatherService, WeatherWatch

logger = logging.getLogger(__name__)


def build_assistant_service(
    *,
    config: AssistantConfig,
    root: Path,
    tasks_file: Path,
    http: httpx.AsyncClient,
    clock=None,
    model: ChatModel | None = None,
    model_name: str = "",
    news: NewsGateway | None = None,
    phone: PhoneGateway | None = None,
    mail: MailGateway | None = None,
    notifier: Notifier | None = None,
) -> AssistantService:
    timezone = _timezone(config.timezone)
    resolved_model = model or FakeChatModel()
    resolved_name = model_name or getattr(resolved_model, "name", "fake")
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

    weather = (
        WeatherService(
            config.weather,
            http=http,
            timezone=timezone,
            cache=ForecastCache(root / ".state" / "weather-cache.json"),
        )
        if config.weather.enabled
        else None
    )
    gold_service = (
        GoldService(config.gold, http=http, timezone=timezone)
        if config.gold.enabled
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
        reply = await holder["service"].chat_task(task)
        if notifier is not None and config.tasks.notify_results and reply.text.strip():
            try:
                await notifier.notify(
                    title=task.title,
                    # A notification body is read on a lock screen, not in the
                    # chat; keep the full brief in the conversation.
                    body=reply.text.strip()[:1000],
                    data={
                        "task_id": task.id,
                        "conversation_id": reply.conversation_id,
                    },
                )
            except Exception:  # noqa: BLE001 - a push failure must not fail the run
                logger.warning("scheduled task push failed", exc_info=True)
        return reply

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
    gold_watch = (
        GoldWatch(
            config=config.gold,
            service=gold_service,
            store=store,
            registry=None,
            notifier=notifier,
            state_path=root / ".state" / "gold.json",
            timezone=timezone,
            clock=clock,
        )
        if gold_service is not None
        else None
    )
    # Built whenever memory exists, even when the pass is disabled, so the
    # status endpoint can say why nothing happens instead of omitting the field.
    memory_tidy = (
        MemoryTidy(
            config=config.memory.tidy,
            memory=memory,  # type: ignore[arg-type]
            model=resolved_model,
            store=store,
            notifier=notifier,
            timezone=timezone,
            state_path=root / ".state" / "memory-tidy.json",
            backup_dir=root / ".state" / "memory-backups",
            clock=clock,
        )
        if memory is not None
        else None
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
        news=news,
        weather=weather,
        gold=gold_watch,
        phone=phone,
        mail=mail,
        notifier=notifier,
        timezone=timezone,
        clock=clock,
    )
    registry = SkillRegistry(
        build_skills(toolbox),
        state_path=root / "skills" / "skills.json",
    )
    if gold_watch is not None:
        gold_watch.registry = registry
    engine = ChatEngine(
        registry=registry,
        model=resolved_model,
        max_tool_rounds=config.max_tool_rounds,
        max_tool_output_chars=config.max_tool_output_chars,
    )
    weather_watch = (
        WeatherWatch(
            config=config.weather,
            service=weather,
            store=store,
            registry=registry,
            timezone=timezone,
            state_path=root / ".state" / "weather.json",
            http=http,
            clock=clock,
            tick_seconds=config.weather.tick_seconds,
            phone=phone,
        )
        if weather is not None
        else None
    )
    agent = AssistantAgent(
        store=store,
        memory=memory,
        toolbox=toolbox,
        engine=engine,
        max_history_messages=config.max_history_messages,
        timezone=timezone,
        clock=clock,
        recall_limit=config.memory.recall_limit,
        recall_chars=config.memory.recall_chars,
        recall_context_messages=config.memory.recall_context_messages,
    )
    service = AssistantService(
        config=config,
        root=root,
        store=store,
        memory=memory,
        builder=builder,
        agent=agent,
        scheduler=scheduler,
        registry=registry,
        model=resolved_model,
        model_name=resolved_name,
        search=search,
        index=index,
        weather_watch=weather_watch,
        gold_watch=gold_watch,
        memory_tidy=memory_tidy,
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
        root / "skills",
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
            unattended=config.local.whitelist.unattended,
        ),
        pending=PendingActions(ttl_seconds=config.exec.pending_ttl_seconds),
        model_name=model_name,
        max_review_detail_chars=config.local.review.max_action_chars,
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
