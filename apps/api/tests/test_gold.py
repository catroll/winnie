from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_assistant.config import GoldConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.gold import (
    GoldError,
    GoldMove,
    GoldQuote,
    GoldSample,
    GoldService,
    GoldWatch,
    parse_quote,
    render_alert,
)
from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.tools import ToolBox

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 1, 7, 0, tzinfo=TZ)
PAYLOAD = {
    "symbol": "XAU",
    "currency": "USD",
    "price": 3876.4,
    "updatedAt": "2026-10-31T22:28:00Z",
}


def _quote(price: float = 3876.4, *, at: datetime = NOW) -> GoldQuote:
    return GoldQuote(
        symbol="XAU",
        price=price,
        currency="USD",
        quoted_at=at - timedelta(minutes=2),
        fetched_at=at,
    )


def _service(handler=None, *, config: GoldConfig | None = None) -> GoldService:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json=PAYLOAD))
        )
    )
    return GoldService(config or GoldConfig(), http=http, timezone=TZ)


def _samples(*points: tuple[int, float]) -> list[GoldSample]:
    """(minutes before NOW, price) pairs."""
    return [GoldSample(at=NOW - timedelta(minutes=age), price=price) for age, price in points]


def test_parse_quote_reads_price_and_time() -> None:
    quote = parse_quote(PAYLOAD, "XAU")
    assert quote.symbol == "XAU"
    assert quote.currency == "USD"
    assert quote.price == 3876.4
    assert quote.quoted_at == datetime(2026, 10, 31, 22, 28, tzinfo=timezone.utc)


async def test_fetch_requests_the_symbol_and_reports_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PAYLOAD)

    quote = await _service(handler).quote()
    assert quote.price == 3876.4
    assert seen[0].url == httpx.URL("https://api.gold-api.com/price/XAU")

    with pytest.raises(GoldError, match="HTTP 503"):
        await _service(lambda request: httpx.Response(503)).quote()


def test_window_move_up_and_down() -> None:
    service = _service()
    up = service.evaluate(
        _samples((60, 3800.0), (0, 3876.4)),
        _quote(),
    )
    assert up is not None
    assert up.direction == "up"
    assert up.window_minutes == 60
    assert up.percent == pytest.approx(2.0105, abs=0.001)

    down = service.evaluate(
        _samples((60, 3800.0)),
        _quote(3700.0),
    )
    assert down is not None
    assert down.direction == "down"


def test_small_move_and_missing_history_do_not_alert() -> None:
    service = _service()
    assert service.evaluate(_samples((60, 3800.0)), _quote(3810.0)) is None
    assert service.evaluate([], _quote()) is None
    # A sample from ten minutes ago is inside the window but too recent to call
    # it an hour; another from four hours ago is outside it.
    assert service.evaluate(_samples((10, 3000.0)), _quote()) is None
    assert service.evaluate(_samples((240, 3000.0)), _quote()) is None


def test_daily_threshold_alerts_without_a_short_window_sample() -> None:
    service = _service(config=GoldConfig(threshold_percent=5.0))
    daily = service.evaluate(_samples((24 * 60, 3600.0)), _quote(3876.4))
    assert daily is not None
    assert daily.window_minutes == 24 * 60
    assert daily.percent == pytest.approx(7.68, abs=0.01)


def test_summary_explains_the_rules() -> None:
    service = _service()
    summary = service.summary(_quote(), _samples((60, 3800.0)))
    assert "黄金 XAU/USD" in summary
    assert "3,876.40" in summary
    assert "+2.01%" in summary
    assert "1%" in summary
    assert "Winnie App" in summary


class _Registry:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def views(self):
        return [SimpleNamespace(name="gold", enabled=self.enabled, available=True)]


class _FakeService:
    def __init__(self, move: GoldMove | None = None) -> None:
        self.calls = 0
        self.move = move

    async def quote(self):
        self.calls += 1
        return _quote()

    def evaluate(self, samples, quote):
        return self.move

    def summary(self, quote, samples):
        return "黄金 XAU/USD 3,876.40 / 盎司"


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    async def notify(self, *, title, body="", data=None):
        self.calls.append((title, body, data))
        return {"seq": len(self.calls)}


def _watch(tmp_path, *, service, registry=None, notifier=None, state=None):
    state_path = tmp_path / "state.json"
    if state is not None:
        state_path.write_text(json.dumps(state), encoding="utf-8")
    watch = GoldWatch(
        config=GoldConfig(schedule="every 5m"),
        service=service,
        store=ConversationStore(tmp_path / "conversations"),
        registry=registry or _Registry(),
        notifier=notifier,
        state_path=state_path,
        timezone=TZ,
        clock=lambda: NOW,
        tick_seconds=3600,
    )
    return watch


async def test_tick_pushes_once_and_records_a_notice(tmp_path) -> None:
    move = GoldMove("up", 2.01, 3800.0, 3876.4, 60)
    notifier = _Notifier()
    watch = _watch(
        tmp_path,
        service=_FakeService(move),
        notifier=notifier,
        state={"next_poll": (NOW - timedelta(minutes=1)).isoformat()},
    )

    run = await watch.tick()
    assert run is not None and run.status == "alert"
    assert len(notifier.calls) == 1
    title, body, data = notifier.calls[0]
    assert title == "黄金价格波动"
    assert "上涨" in body
    assert data["kind"] == "gold"

    conversations = watch.store.list()
    assert len(conversations) == 1
    assert conversations[0].source == "gold"
    assert conversations[0].channel == "notification"

    # Inside the cooldown the same move is not pushed again.
    watch._state["next_poll"] = NOW.isoformat()
    second = await watch.tick(NOW + timedelta(minutes=5))
    assert second is not None and second.status == "cooldown"
    assert len(notifier.calls) == 1


async def test_disabled_skill_does_not_sample(tmp_path) -> None:
    service = _FakeService(None)
    watch = _watch(
        tmp_path,
        service=service,
        registry=_Registry(enabled=False),
        state={"next_poll": (NOW - timedelta(minutes=1)).isoformat()},
    )
    run = await watch.tick()
    assert run is not None and run.status == "disabled"
    assert service.calls == 0
    assert watch.store.list() == []


async def test_run_now_never_pushes(tmp_path) -> None:
    move = GoldMove("up", 2.01, 3800.0, 3876.4, 60)
    notifier = _Notifier()
    watch = _watch(tmp_path, service=_FakeService(move), notifier=notifier)

    run = await watch.run_now()
    assert run.status == "alert"
    assert notifier.calls == []
    assert watch.store.list() == []


def test_alert_text_names_the_window_and_source() -> None:
    quote = _quote()
    text = render_alert(GoldMove("up", 2.01, 3800.0, 3876.4, 45, 3.1), quote, TZ)
    assert "过去 45 分钟" in text
    assert "3,800.00 → 3,876.40" in text
    assert "+2.01%" in text
    assert "近 24 小时：+3.10%" in text
    assert "gold-api.com" in text


async def test_gold_tool_returns_the_summary_or_an_error(tmp_path) -> None:
    move = GoldMove("up", 2.01, 3800.0, 3876.4, 60)
    watch = _watch(tmp_path, service=_FakeService(move))
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    toolbox = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=http,
        tasks_enabled=False,
        gold=watch,
    )
    output = await toolbox.execute("gold_price", {}, conversation_id="c")
    assert "3,876.40" in output

    disabled = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=http,
        tasks_enabled=False,
    )
    error = await disabled.execute("gold_price", {}, conversation_id="c")
    assert error.startswith("ERROR: gold is disabled")
