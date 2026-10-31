"""Gold price watch: sample the price and push an alert on a sharp move.

The feed (gold-api.com) returns only the current price, so the watcher builds
its own short history: every sample is stored, and a move is measured against
the sample closest to one window ago. That is honest about a keyless source
that has no candles, and it survives a restart because the samples live on
disk.

A move produces two things: a conversation notice for the record, and a push
notification to the owner's Winnie app through the assistant's ``Notifier``.
The model is not involved; an alert must not depend on a model being bound.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from winnie_assistant.config import GoldConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.files import atomic_write
from winnie_assistant.notify import Notifier
from winnie_assistant.scheduler import Schedule, parse_schedule

logger = logging.getLogger(__name__)

PRICE_URL = "https://api.gold-api.com/price/{symbol}"
SOURCE = "gold-api.com"
MAX_SAMPLES = 5000


class GoldError(RuntimeError):
    """The price could not be fetched or parsed."""


@dataclass(frozen=True)
class GoldQuote:
    symbol: str
    price: float
    currency: str
    quoted_at: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class GoldSample:
    at: datetime
    price: float


@dataclass(frozen=True)
class GoldMove:
    direction: str
    percent: float
    from_price: float
    to_price: float
    window_minutes: int
    daily_percent: float | None = None


@dataclass(frozen=True)
class GoldRun:
    status: str
    checked_at: datetime
    message: str = ""
    conversation_id: str = ""
    price: float | None = None
    move: GoldMove | None = None
    error: str = ""


class GoldService:
    """Fetch one quote and turn a sample history into a move."""

    def __init__(
        self,
        config: GoldConfig,
        *,
        http: httpx.AsyncClient,
        timezone: ZoneInfo,
    ) -> None:
        self.config = config
        self.http = http
        self.timezone = timezone

    async def quote(self) -> GoldQuote:
        url = PRICE_URL.format(symbol=self.config.symbol)
        try:
            response = await self.http.get(url, timeout=self.config.timeout_seconds)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GoldError(f"gold request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise GoldError(f"gold request returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoldError("gold response was not JSON") from exc
        return parse_quote(payload, self.config.symbol)

    def evaluate(
        self,
        samples: list[GoldSample],
        quote: GoldQuote,
    ) -> GoldMove | None:
        """Return a move when the configured window or day moved enough."""
        now = quote.fetched_at
        daily = _change(
            samples, now, minutes=24 * 60, min_age_ratio=0.85, price=quote.price
        )
        window = _change(
            samples,
            now,
            minutes=self.config.window_minutes,
            min_age_ratio=0.5,
            price=quote.price,
        )
        if window is not None:
            base, percent = window
            if abs(percent) >= self.config.threshold_percent:
                age = round((now - base.at).total_seconds() / 60)
                return GoldMove(
                    direction="up" if percent > 0 else "down",
                    percent=percent,
                    from_price=base.price,
                    to_price=quote.price,
                    window_minutes=age,
                    daily_percent=daily[1] if daily else None,
                )
        if (
            daily is not None
            and self.config.daily_threshold_percent > 0
            and abs(daily[1]) >= self.config.daily_threshold_percent
        ):
            base, percent = daily
            return GoldMove(
                direction="up" if percent > 0 else "down",
                percent=percent,
                from_price=base.price,
                to_price=quote.price,
                window_minutes=24 * 60,
                daily_percent=percent,
            )
        return None

    def summary(self, quote: GoldQuote, samples: list[GoldSample]) -> str:
        now = quote.fetched_at
        window = _change(
            samples,
            now,
            minutes=self.config.window_minutes,
            min_age_ratio=0.5,
            price=quote.price,
        )
        daily = _change(
            samples, now, minutes=24 * 60, min_age_ratio=0.85, price=quote.price
        )
        lines = [
            f"黄金 {quote.symbol}/{quote.currency} "
            f"{format_price(quote.price)} / 盎司",
            f"报价 {quote.quoted_at.astimezone(self.timezone):%m-%d %H:%M}，"
            f"抓取 {quote.fetched_at.astimezone(self.timezone):%m-%d %H:%M}",
        ]
        if window is None:
            lines.append(
                f"近 {self.config.window_minutes} 分钟：历史样本不足，"
                f"需要约 {self.config.window_minutes} 分钟的连续采样。"
            )
        else:
            base, percent = window
            lines.append(
                f"近 {self.config.window_minutes} 分钟：{signed(percent)}"
                f"（{format_price(base.price)} → {format_price(quote.price)}）"
            )
        if daily is not None:
            base, percent = daily
            lines.append(
                f"近 24 小时：{signed(percent)}"
                f"（{format_price(base.price)} → {format_price(quote.price)}）"
            )
        lines.append(
            f"提醒规则：{self.config.window_minutes} 分钟内波动 ≥ "
            f"{self.config.threshold_percent:g}%"
            + (
                f"，或 24 小时 ≥ {self.config.daily_threshold_percent:g}%"
                if self.config.daily_threshold_percent > 0
                else ""
            )
            + f"；冷却 {self.config.cooldown_minutes} 分钟。"
        )
        lines.append("数据：gold-api.com；提醒通过 Winnie App 推送。")
        return "\n".join(lines)


def parse_quote(payload: dict[str, Any], symbol: str) -> GoldQuote:
    if not isinstance(payload, dict):
        raise GoldError("gold response was not an object")
    price = payload.get("price")
    try:
        value = float(price)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise GoldError("gold response has no numeric price") from exc
    if value <= 0:
        raise GoldError("gold response has a non-positive price")
    fetched_at = datetime.now(timezone.utc)
    quoted_at = _parse_time(payload.get("updatedAt")) or fetched_at
    return GoldQuote(
        symbol=str(payload.get("symbol") or symbol),
        price=value,
        currency=str(payload.get("currency") or "USD"),
        quoted_at=quoted_at,
        fetched_at=fetched_at,
    )


def format_price(value: float) -> str:
    return f"{value:,.2f}"


def signed(value: float) -> str:
    return f"{value:+.2f}%"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _change(
    samples: list[GoldSample],
    now: datetime,
    *,
    minutes: int,
    min_age_ratio: float,
    price: float,
) -> tuple[GoldSample, float] | None:
    """The sample closest to ``now - minutes`` and the percentage change to it."""
    low = minutes * min_age_ratio
    high = max(minutes * 2.0, minutes + 60)
    candidates = [
        sample
        for sample in samples
        if low <= (now - sample.at).total_seconds() / 60 <= high
    ]
    if not candidates:
        return None
    base = min(
        candidates,
        key=lambda sample: abs((now - sample.at).total_seconds() / 60 - minutes),
    )
    if base.price <= 0:
        return None
    return base, (price - base.price) / base.price * 100.0


class GoldWatch:
    """Sample on schedule and push when a move clears the threshold."""

    def __init__(
        self,
        *,
        config: GoldConfig,
        service: GoldService,
        store: ConversationStore,
        registry: Any,
        notifier: Notifier | None,
        state_path: Path,
        timezone: ZoneInfo,
        clock=None,
        tick_seconds: float | None = None,
    ) -> None:
        self.config = config
        self.service = service
        self.store = store
        self.registry = registry
        self.notifier = notifier
        self.state_path = state_path
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(timezone))
        self.tick_seconds = tick_seconds or config.tick_seconds
        self._schedule: Schedule = parse_schedule(config.schedule)
        self._state: dict[str, Any] = self._load()
        self._loop: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if not self._state.get("next_poll"):
            self._state["next_poll"] = _iso(self._schedule.next_after(self.clock()))
            self._save()
        self._loop = asyncio.create_task(self._run_loop(), name="gold-watch")

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
            self._loop = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "symbol": self.config.symbol,
            "schedule": self.config.schedule,
            "window_minutes": self.config.window_minutes,
            "threshold_percent": self.config.threshold_percent,
            "daily_threshold_percent": self.config.daily_threshold_percent,
            "cooldown_minutes": self.config.cooldown_minutes,
            "next_poll": self._state.get("next_poll"),
            "last_poll": self._state.get("last_poll"),
            "last_status": self._state.get("last_status"),
            "last_price": self._state.get("last_price"),
            "last_alert_at": self._state.get("last_alert_at"),
            "push": self.notifier is not None,
        }

    # -- schedule --------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> GoldRun | None:
        moment = now or self.clock()
        next_poll = _parse_iso(self._state.get("next_poll"))
        if next_poll is None:
            self._state["next_poll"] = _iso(self._schedule.next_after(moment))
            self._save()
            return None
        if next_poll > moment:
            return None
        return await self._poll(moment, manual=False)

    async def run_now(self) -> GoldRun:
        """Fetch and report without pushing; a manual check is an inspection."""
        return await self._poll(self.clock(), manual=True)

    # -- sampling --------------------------------------------------------

    async def _poll(self, moment: datetime, *, manual: bool) -> GoldRun:
        if not self.config.enabled:
            return self._finish(
                GoldRun(status="disabled", checked_at=moment, error="gold is disabled"),
                moment,
                advance=not manual,
            )
        if not manual and not self._skill_enabled():
            return self._finish(
                GoldRun(
                    status="disabled",
                    checked_at=moment,
                    error="the gold skill is disabled",
                ),
                moment,
                advance=True,
            )
        try:
            quote = await self.service.quote()
        except GoldError as exc:
            logger.warning("gold check failed: %s", exc)
            return self._finish(
                GoldRun(status="error", checked_at=moment, error=str(exc)),
                moment,
                advance=not manual,
            )

        samples = self._samples()
        samples.append(GoldSample(at=quote.fetched_at, price=quote.price))
        samples = _prune(samples, quote.fetched_at, self.config.history_hours)
        self._store_samples(samples)

        move = self.service.evaluate(samples, quote)
        if move is not None and not manual and self._in_cooldown(moment):
            move = None
            status = "cooldown"
        elif move is None:
            status = "ok"
        else:
            status = "alert"

        message = self.service.summary(quote, samples)
        conversation_id = ""
        push_error = ""
        if move is not None and not manual:
            alert = render_alert(move, quote, self.timezone)
            conversation = await self.store.create_notice(
                title="黄金价格波动提醒",
                text=alert,
                source="gold",
                channel="notification",
                now=moment,
            )
            conversation_id = conversation.id
            if self.notifier is not None:
                try:
                    await self.notifier.notify(
                        title="黄金价格波动",
                        body=alert,
                        data={
                            "kind": "gold",
                            "symbol": quote.symbol,
                            "price": quote.price,
                            "percent": move.percent,
                            "window_minutes": move.window_minutes,
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - a failed push must not lose the record
                    logger.exception("gold push failed")
                    push_error = f"{type(exc).__name__}: {exc}"
            self._state["last_alert_at"] = _iso(moment)
            self._state["last_alert_price"] = quote.price
            self._state["last_alert_direction"] = move.direction

        self._state["last_price"] = quote.price
        run = GoldRun(
            status=status,
            checked_at=moment,
            message=message,
            conversation_id=conversation_id,
            price=quote.price,
            move=move,
            error=push_error,
        )
        return self._finish(run, moment, advance=not manual)

    def _finish(self, run: GoldRun, moment: datetime, *, advance: bool) -> GoldRun:
        self._state["last_poll"] = _iso(moment)
        self._state["last_status"] = run.status
        if advance:
            self._state["next_poll"] = _iso(self._schedule.next_after(moment))
        self._save()
        return run

    def _in_cooldown(self, now: datetime) -> bool:
        last = _parse_iso(self._state.get("last_alert_at"))
        if last is None:
            return False
        return now - last < timedelta(minutes=self.config.cooldown_minutes)

    def _samples(self) -> list[GoldSample]:
        raw = self._state.get("samples")
        samples: list[GoldSample] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                at = _parse_iso(item.get("at"))
                price = item.get("price")
                if at is None or not isinstance(price, (int, float)) or price <= 0:
                    continue
                samples.append(GoldSample(at=at, price=float(price)))
        return samples

    def _store_samples(self, samples: list[GoldSample]) -> None:
        self._state["samples"] = [
            {"at": _iso(sample.at), "price": sample.price}
            for sample in samples[-MAX_SAMPLES:]
        ]

    def _skill_enabled(self) -> bool:
        try:
            views = self.registry.views()
        except Exception:  # noqa: BLE001 - a missing registry must not stop the loop
            return False
        return any(
            view.name == "gold" and view.enabled and view.available for view in views
        )

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive one bad tick
                logger.exception("gold watch tick failed")
            await asyncio.sleep(self.tick_seconds)

    # -- state -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("gold state is unreadable; starting fresh")
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        atomic_write(
            self.state_path,
            json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )


def render_alert(move: GoldMove, quote: GoldQuote, timezone: ZoneInfo) -> str:
    window = (
        "过去 24 小时"
        if move.window_minutes >= 24 * 60
        else f"过去 {move.window_minutes} 分钟"
    )
    direction = "上涨" if move.direction == "up" else "下跌"
    lines = [
        f"黄金短时{direction}：{window} "
        f"{format_price(move.from_price)} → {format_price(move.to_price)} "
        f"{quote.currency}/盎司，{signed(move.percent)}。",
    ]
    if move.daily_percent is not None and move.window_minutes < 24 * 60:
        lines.append(f"近 24 小时：{signed(move.daily_percent)}。")
    lines.append(
        f"报价时间 {quote.quoted_at.astimezone(timezone):%m-%d %H:%M}；"
        f"数据 {SOURCE}。"
    )
    return "\n".join(lines)


def _prune(samples: list[GoldSample], now: datetime, hours: int) -> list[GoldSample]:
    cutoff = now - timedelta(hours=hours)
    return [sample for sample in samples if sample.at >= cutoff][-MAX_SAMPLES:]


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    return _parse_time(value)
