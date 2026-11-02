"""Winnie API host: compose framework and capability packages."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import select, text

from winnie_assistant.capability import capability as assistant_capability
from winnie_assistant.factory import build_assistant_service
from winnie_core.auth.admin_router import router as admin_users_router
from winnie_core.auth.admin_service import AdminUserService
from winnie_core.auth.capability import capability as auth_capability
from winnie_core.auth.models import User
from winnie_core.capability import Capability, WinnieFramework
from winnie_core.jobs.queue import ArqJobQueue
from winnie_core.usage.ledger import UsageLedger
from winnie_gateway.capability import capability as gateway_capability
from winnie_gateway.keystore import GatewayKeyStore
from winnie_gateway.routes import Route
from winnie_mail.cache import MailCache
from winnie_mail.capability import capability as mail_capability
from winnie_mail.service import MailService
from winnie_mail.settings import MailSettingsStore
from winnie_meetings.capability import capability as meetings_capability
from winnie_news.capability import capability as news_capability
from winnie_news.service import NewsService
from winnie_phone.capability import capability as phone_capability
from winnie_proactive.capability import capability as proactive_capability
from winnie_push.capability import capability as push_capability
from winnie_proactive.service import ProactiveService
from winnie_llm import ApiKeys, ProviderRegistry

from winnie_api import __version__
from winnie_api.ai.providers import DynamicChatModel
from winnie_api.ai.routes import config_route_table, gateway_route_table
from winnie_api.ai.seed import seed_input
from winnie_api.gateway.router import router as gateway_admin_router
from winnie_api.news.gateway import NewsGatewayAdapter
from winnie_api.news.llm import DynamicNewsLLM
from winnie_api.news.search import build_news_search
from winnie_api.phone.gateway import PhoneGatewayAdapter
from winnie_api.push.notify import PushNotifierAdapter
from winnie_api.ai.router import router as ai_router
from winnie_api.assistant_voice import router as assistant_voice_router
from winnie_api.index_router import router as index_router
from winnie_api.runtime import AppRuntime, build_runtime
from winnie_api.proactive.adapters import (
    ModelComposer,
    PhoneCalendarAdapter,
    PhoneContactsAdapter,
    ScheduleAdapter,
    build_channels,
)
from winnie_api.schedule.router import router as schedule_router
from winnie_api.schedule.service import ScheduleService, ScheduleUnavailable
from winnie_api.settings import Settings, load_settings
from winnie_api.usage.balances import BalanceService
from winnie_api.usage.router import router as usage_router

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Give the capability packages' loggers somewhere to go.

    Uvicorn configures only its own loggers, so the root logger had no handler
    and every ``logger.info`` in the packages was dropped: the server kept an
    access log but no record of *what* a phone uploaded, which is why "kind=call
    never arrived" had to be inferred from a sync-state table. A no-op when a
    handler is already installed, so tests and embedders keep their own setup.
    """
    if logging.getLogger().handlers:
        return
    level = os.environ.get("WINNIE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Third-party chatter that would otherwise drown the one line per request
    # per phone that matters here.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()


def create_app(
    *,
    settings: Settings | None = None,
    http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the application from independently registered capabilities."""
    resolved = settings or load_settings()
    owns_http = http is None
    # One ledger for the whole process: internal model calls and gateway
    # traffic append to the same files, so the console can report both.
    usage_ledger = UsageLedger(
        resolved.resolve_usage_root(), enabled=resolved.usage.enabled
    )
    # The env backup the console mirrors supplier keys into. Internal providers
    # read the registry; only a gateway-only deployment reads this file.
    api_keys = ApiKeys.load()
    ai_settings = (
        ProviderRegistry(
            resolved.resolve_ai_path(),
            keys=api_keys,
            seed=seed_input(resolved),
        )
        if resolved.ai.enabled
        else None
    )
    if ai_settings is not None:
        # Seed or validate the file before any service is built: a broken
        # registry should fail startup loudly, not surface on the first chat.
        ai_settings.load()
    gateway_config = resolved.gateway_config()
    gateway_keys = GatewayKeyStore(resolved.resolve_gateway_keys_path())

    def build_route_table() -> dict[str, Route]:
        """The live route table, with each route's supplier credential."""
        if ai_settings is not None:
            return gateway_route_table(ai_settings)
        return config_route_table(gateway_config, api_keys)

    http_client = http or httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0)
    )
    runtime: AppRuntime | None = build_runtime(
        resolved,
        ai=ai_settings,
        http=http_client,
        usage=usage_ledger,
    )

    async def resolve_owner() -> str:
        """Whose phone and notifications the assistant may use.

        A device belongs to the account that registered it. The assistant is a
        single-owner service, so it reads the configured owner, or the only
        active account. A multi-account instance must name the owner.
        """
        configured = resolved.phone.owner_user.strip()
        if configured:
            return configured
        if runtime is None or runtime.auth_service is None:
            return resolved.meetings.default_user_id or "local"
        async with runtime.session_factory() as session:
            user_ids = list(
                await session.scalars(
                    select(User.id)
                    .where(User.is_active.is_(True))
                    .limit(2)
                )
            )
        return str(user_ids[0]) if len(user_ids) == 1 else ""

    balance_service = (
        BalanceService(
            ai=ai_settings,
            http=http_client,
            ttl_seconds=resolved.usage.balance_ttl_seconds,
            timeout_seconds=resolved.usage.balance_timeout_seconds,
        )
        if ai_settings is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = gateway_config
        app.state.gateway_keys = gateway_keys
        app.state.routes_table = build_route_table()
        app.state.rebuild_routes = build_route_table
        # Both of these are startup failures that would otherwise surface as a
        # 401 or 503 on a client that looks correctly configured.
        if not gateway_keys.list():
            logger.warning(
                "no gateway keys exist; every gateway call answers 401 until a "
                "key is created in the console"
            )
        missing = sorted(
            name
            for name, route in app.state.routes_table.items()
            if not route.credential_present
        )
        if missing:
            logger.info(
                "gateway routes without a server credential: %s", ", ".join(missing)
            )
        app.state.ai_settings = ai_settings
        app.state.usage_ledger = usage_ledger
        app.state.usage_prices = resolved.usage.price_table()
        app.state.usage_balances = balance_service
        app.state.log_root = resolved.resolve_log_root()
        app.state.recorders = {}
        app.state.default_user_id = resolved.meetings.default_user_id
        app.state.index_base_url = resolved.index.base_url if resolved.index.enabled else ""
        app.state.index_timeout = resolved.index.timeout_seconds
        app.state.http = http_client
        app.state.runtime = runtime
        app.state.assistant_service = None
        app.state.assistant_error = ""
        app.state.news_service = None
        app.state.news_error = ""
        app.state.admin_user_service = None
        app.state.schedule_service = None
        app.state.schedule_error = ""
        app.state.proactive_service = None
        app.state.proactive_error = ""
        app.state.mail_service = None
        if resolved.mail.enabled:
            # A file-backed per-user store, so it starts without the database and
            # is available to the console and to the proactive email rung.
            app.state.mail_service = MailService(
                MailSettingsStore(resolved.resolve_mail_root()),
                timeout_seconds=resolved.mail.default_timeout_seconds,
                per_folder_limit=resolved.mail.per_folder_limit,
            )
        if runtime is not None:
            await runtime.startup()
            app.state.auth_service = runtime.auth_service
            app.state.meeting_service = runtime.meeting_service
            app.state.phone_service = runtime.phone_service
            app.state.push_service = runtime.push_service
            if app.state.mail_service is not None:
                # The cache lives in the application database; without one the
                # account still sends and tests, but browsing answers 503.
                app.state.mail_service.attach_cache(
                    MailCache(runtime.session_factory)
                )
            # User management needs a database but not necessarily accounts:
            # with auth disabled the local operator may still create accounts for
            # a deployment that enables login later.
            app.state.admin_user_service = AdminUserService(
                session_factory=runtime.session_factory
            )
        else:
            app.state.phone_service = None
            app.state.push_service = None
        # The schedule is a directory of Markdown files, not a database table:
        # it starts independently of the runtime so a console with the database
        # down can still report what failed.
        if resolved.schedule.enabled:
            try:
                app.state.schedule_service = ScheduleService(resolved.resolve_schedule_root())
            except ScheduleUnavailable as exc:
                app.state.schedule_error = str(exc)
                logger.warning("schedule unavailable: %s", exc)

        proactive = None
        if resolved.proactive.enabled and runtime is not None:
            try:
                channels = build_channels(
                    resolved.proactive,
                    http=http_client,
                    push_service=runtime.push_service,
                    resolve_owner=resolve_owner,
                    mail_service=app.state.mail_service,
                )
                proactive = ProactiveService(
                    resolved.proactive,
                    session_factory=runtime.session_factory,
                    channels=channels,
                    resolve_owner=resolve_owner,
                    root=resolved.resolve_proactive_root(),
                    calendar=(
                        PhoneCalendarAdapter(
                            runtime.phone_service, resolve_owner=resolve_owner
                        )
                        if runtime.phone_service is not None
                        else None
                    ),
                    contacts=(
                        PhoneContactsAdapter(
                            runtime.phone_service, resolve_owner=resolve_owner
                        )
                        if runtime.phone_service is not None
                        else None
                    ),
                    schedule=(
                        ScheduleAdapter(app.state.schedule_service)
                        if app.state.schedule_service is not None
                        else None
                    ),
                    composer=(
                        ModelComposer(
                            DynamicChatModel(ai_settings, http_client, usage_ledger),
                            max_chars=resolved.proactive.composer.max_chars,
                            timeout_seconds=resolved.proactive.composer.timeout_seconds,
                        )
                        if resolved.proactive.composer.enabled
                        and ai_settings is not None
                        else None
                    ),
                )
                await proactive.startup()
                app.state.proactive_service = proactive
            except Exception as exc:  # noqa: BLE001 - the rest of the API stays up
                app.state.proactive_error = f"{type(exc).__name__}: {exc}"
                logger.exception("proactive assistance failed to start")

        news = None
        if resolved.news.enabled:
            try:
                news_llm = (
                    DynamicNewsLLM(ai_settings, http_client, usage_ledger)
                    if ai_settings is not None
                    else None
                )
                news = NewsService(
                    resolved.news,
                    root=resolved.resolve_news_root(),
                    llm=news_llm,
                    search=build_news_search(resolved, http_client),
                )
                await news.startup()
                app.state.news_service = news
            except Exception as exc:  # noqa: BLE001 - keep the rest of the API up
                app.state.news_error = f"{type(exc).__name__}: {exc}"
                logger.exception("news failed to start")

        assistant = None
        if resolved.assistant.enabled:
            try:
                model = (
                    DynamicChatModel(ai_settings, http_client, usage_ledger)
                    if ai_settings is not None
                    else None
                )
                assistant = build_assistant_service(
                    config=resolved.assistant,
                    root=resolved.resolve_assistant_root(),
                    tasks_file=resolved.resolve_assistant_tasks_file(),
                    http=http_client,
                    model=model,
                    model_name=getattr(model, "name", "") if model is not None else "",
                    news=NewsGatewayAdapter(news) if news is not None else None,
                    phone=(
                        PhoneGatewayAdapter(
                            runtime.phone_service,
                            resolve_owner=resolve_owner,
                        )
                        if runtime is not None and runtime.phone_service is not None
                        else None
                    ),
                    notifier=(
                        PushNotifierAdapter(
                            runtime.push_service,
                            resolve_owner=resolve_owner,
                        )
                        if runtime is not None and runtime.push_service is not None
                        else None
                    ),
                )
                await assistant.startup()
                app.state.assistant_service = assistant
            except Exception as exc:  # noqa: BLE001 - keep the rest of the API up
                app.state.assistant_error = f"{type(exc).__name__}: {exc}"
                logger.exception("assistant failed to start")
        try:
            yield
        finally:
            if proactive is not None:
                await proactive.shutdown()
                app.state.proactive_service = None
            if news is not None:
                await news.shutdown()
                app.state.news_service = None
            if assistant is not None:
                await assistant.shutdown()
                app.state.assistant_service = None
            if runtime is not None:
                await runtime.shutdown()
                app.state.runtime = None
            if owns_http:
                await http_client.aclose()

    app = FastAPI(title="Winnie", version=__version__, lifespan=lifespan)
    framework = WinnieFramework(app)
    if resolved.auth.enabled:
        framework.register(auth_capability())
    if resolved.meetings.enabled:
        framework.register(meetings_capability())
    if resolved.assistant.enabled:
        framework.register(assistant_capability())
    if resolved.news.enabled:
        framework.register(news_capability())
    if resolved.phone.enabled:
        framework.register(phone_capability())
    if resolved.push.enabled:
        framework.register(push_capability())
    if resolved.mail.enabled:
        framework.register(mail_capability())
    if resolved.proactive.enabled:
        framework.register(proactive_capability())
    # The document index is not a capability: it is a separate daemon, and this
    # router only proxies to it, so the host owns the mount.
    if resolved.index.enabled:
        app.include_router(index_router, prefix="/api/v1")
    if resolved.ai.enabled:
        app.include_router(ai_router, prefix="/api/v1")
        # Voice chat is a short ASR call on the same binding as meeting notes,
        # not an assistant-service endpoint: it must answer even when the
        # assistant capability itself is switched off.
        app.include_router(assistant_voice_router, prefix="/api/v1")
    # Recording telemetry is a host concern: it spans the gateway and every
    # capability's internal model calls, so it is not owned by one capability.
    app.include_router(usage_router, prefix="/api/v1")
    # The schedule is a host concern: it owns a filesystem tree outside the
    # repository rather than a capability's service object.
    if resolved.schedule.enabled:
        app.include_router(schedule_router, prefix="/api/v1")
    # Admin user management is a host concern too: it must exist whether or not
    # the auth capability is registered, because an auth-disabled deployment
    # still acts as an administrator.
    app.include_router(admin_users_router, prefix="/api/v1")
    # Gateway keys are Winnie credentials, so the console owns them regardless
    # of whether the AI registry is enabled.
    app.include_router(gateway_admin_router, prefix="/api/v1")
    # The gateway is registered last: its data plane is a catch-all under
    # /{route}/{path}, so every concrete host route must be matched first.
    framework.register(gateway_capability())
    app.state.capability_names = framework.capability_names

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        runtime = getattr(app.state, "runtime", None)
        if runtime is None:
            return {
                "status": "ok",
                "database": "disabled",
                "queue": "disabled",
            }
        try:
            async with runtime.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("readiness database check failed")
            raise HTTPException(
                status_code=503, detail="database unavailable"
            ) from exc

        queue = resolved.jobs.backend
        if queue == "arq":
            try:
                await asyncio.wait_for(
                    ArqJobQueue(resolved.jobs.redis_url).ping(),
                    timeout=2.0,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("readiness queue check failed")
                raise HTTPException(
                    status_code=503, detail="queue unavailable"
                ) from exc
        return {"status": "ok", "database": "ok", "queue": queue}

    return app


app = create_app()
