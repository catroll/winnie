"""Usage recording, cost estimates and the console's usage endpoints."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from winnie_api.ai.providers import DynamicTranslator
from winnie_api.ai.schema import BindingDraft, IntegrationDraft
from winnie_api.ai.store import AISettingsService
from winnie_api.main import create_app
from winnie_api.settings import AIConfig, DatabaseConfig, RouteConfig, Settings
from winnie_core.llm.openai_compatible import OpenAICompatibleLLM
from winnie_core.usage.config import ModelPriceConfig, UsageConfig
from winnie_core.usage.event import UsageEvent
from winnie_core.usage.ledger import UsageLedger
from winnie_core.usage.pricing import Price, PriceTable, resolve_cost
from winnie_core.usage.report import summarize
from winnie_core.usage.tokens import parse_usage
from winnie_gateway.config import RouteConfig as GatewayRouteConfig


def _at() -> datetime:
    return datetime.now(timezone.utc)


def _read(root: Path) -> list[UsageEvent]:
    moment = _at()
    return UsageLedger(root).read(
        start=moment - timedelta(hours=1), end=moment + timedelta(hours=1)
    )


def _event(**overrides: Any) -> UsageEvent:
    base: dict[str, Any] = {
        "at": _at(),
        "capability": "assistant.chat",
        "source": "internal",
        "provider": "deepseek",
        "model": "deepseek-flash",
        "status": "ok",
        "latency_ms": 120,
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
    }
    base.update(overrides)
    return UsageEvent(**base)


@asynccontextmanager
async def _open(settings: Settings, handler):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0)
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    await http.aclose()


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "log_root": str(tmp_path / "logs"),
        "routes": {
            "fake-openai": GatewayRouteConfig(
                protocol="openai", base_url="https://fake.openai"
            ),
            "fake-anthropic": GatewayRouteConfig(
                protocol="anthropic", base_url="https://fake.anthropic"
            ),
        },
        "usage": UsageConfig(
            enabled=True,
            root=str(tmp_path / "usage"),
            currency="USD",
            prices={
                "deepseek-flash": ModelPriceConfig(
                    input=0.30, output=1.20, cached_input=0.006
                ),
                "gpt-test": ModelPriceConfig(input=1.0, output=2.0),
            },
        ),
    }
    values.update(overrides)
    return Settings(**values)


# -- token normalization ---------------------------------------------------


def test_openai_usage_normalizes_cached_tokens_out_of_the_input() -> None:
    tokens = parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
    )
    assert tokens.input_tokens == 200
    assert tokens.cached_input_tokens == 800
    assert tokens.output_tokens == 50
    assert tokens.reasoning_tokens == 20
    assert tokens.total_tokens == 1050


def test_deepseek_cache_fields_are_understood() -> None:
    tokens = parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_cache_hit_tokens": 900,
            "prompt_cache_miss_tokens": 100,
        }
    )
    assert tokens.input_tokens == 100
    assert tokens.cached_input_tokens == 900


def test_anthropic_usage_keeps_cache_tokens_separate() -> None:
    tokens = parse_usage(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
        }
    )
    assert tokens.input_tokens == 100
    assert tokens.cached_input_tokens == 50
    assert tokens.cache_write_tokens == 10
    assert tokens.total_tokens == 180


def test_a_missing_usage_object_is_not_zero() -> None:
    tokens = parse_usage({})
    assert not tokens.reported
    assert tokens.total_tokens is None


# -- pricing ---------------------------------------------------------------


def test_price_lookup_prefers_provider_then_model_then_wildcard() -> None:
    table = PriceTable(
        {
            "deepseek/deepseek-flash": Price(Decimal("1"), Decimal("1")),
            "deepseek-flash": Price(Decimal("2"), Decimal("2")),
            "*": Price(Decimal("3"), Decimal("3")),
        }
    )
    assert table.lookup("deepseek", "deepseek-flash") == Price(
        Decimal("1"), Decimal("1")
    )
    assert table.lookup("other", "deepseek-flash") == Price(
        Decimal("2"), Decimal("2")
    )
    assert table.lookup("other", "unknown") == Price(Decimal("3"), Decimal("3"))


def test_estimate_applies_input_output_and_cached_rates() -> None:
    table = PriceTable(
        {
            "model": Price(
                Decimal("0.30"), Decimal("1.20"), Decimal("0.006")
            )
        }
    )
    event = _event(
        model="model",
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        output_tokens=1_000_000,
        total_tokens=3_000_000,
    )
    cost = resolve_cost(event, table)
    assert cost.source == "estimated"
    # 0.30 + 0.006 + 1.20 = 1.506 currency units.
    assert cost.micros == 1_506_000


def test_missing_cached_price_is_unpriced_not_underpriced() -> None:
    table = PriceTable({"model": Price(Decimal("1"), Decimal("2"))})
    event = _event(model="model", cached_input_tokens=500)
    cost = resolve_cost(event, table)
    assert cost.micros is None
    assert cost.source == "unpriced"
    assert "cached" in cost.reason


def test_provider_reported_cost_wins_over_the_table() -> None:
    table = PriceTable({"model": Price(Decimal("1"), Decimal("2"))})
    event = _event(model="model", provider_cost_micros=1234)
    cost = resolve_cost(event, table)
    assert cost.micros == 1234
    assert cost.source == "provider"


# -- ledger ----------------------------------------------------------------


def test_ledger_appends_one_file_per_month_and_reads_a_range(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage")
    old = _event(at=datetime(2026, 1, 31, 23, 59, tzinfo=timezone.utc))
    recent = _event(at=datetime(2026, 2, 1, 0, 1, tzinfo=timezone.utc))
    ledger.record(old)
    ledger.record(recent)

    files = sorted((tmp_path / "usage").glob("*.jsonl"))
    assert [path.name for path in files] == [
        "usage-2026-01.jsonl",
        "usage-2026-02.jsonl",
    ]
    events = ledger.read(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    assert [event.id for event in events] == [old.id, recent.id]


def test_a_disabled_ledger_writes_nothing(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage", enabled=False)
    ledger.record(_event())
    assert not (tmp_path / "usage").exists()


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage")
    ledger.record(_event())
    path = next((tmp_path / "usage").glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    assert len(_read(tmp_path / "usage")) == 1


def test_summary_groups_and_fills_days() -> None:
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 3, 23, 59, tzinfo=timezone.utc)
    events = [
        _event(at=datetime(2026, 2, 1, 10, tzinfo=timezone.utc)),
        _event(
            at=datetime(2026, 2, 3, 10, tzinfo=timezone.utc),
            capability="gateway",
            source="gateway",
            status="error",
        ),
    ]
    report = summarize(events, PriceTable({}, currency="USD"), start=start, end=end)
    assert report["totals"]["requests"] == 2
    assert report["totals"]["errors"] == 1
    assert report["totals"]["unpriced_requests"] == 2
    assert [row["date"] for row in report["by_day"]] == [
        "2026-02-01",
        "2026-02-02",
        "2026-02-03",
    ]
    assert {row["capability"] for row in report["by_capability"]} == {
        "assistant.chat",
        "gateway",
    }


# -- recording -------------------------------------------------------------


async def test_internal_client_records_a_call_and_its_error(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["messages"][-1]["content"] == "fail":
            return httpx.Response(400, json={"error": "nope"})
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 40},
                },
            },
        )

    llm = OpenAICompatibleLLM(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="deepseek-flash",
        max_retries=0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        usage=ledger,
        capability="meetings.translate",
        provider="test-llm",
    )
    await llm.chat([{"role": "user", "content": "hi"}])
    with pytest.raises(RuntimeError):
        await llm.chat([{"role": "user", "content": "fail"}])

    events = _read(tmp_path / "usage")
    assert len(events) == 2
    ok, failure = events
    assert ok.capability == "meetings.translate"
    assert ok.source == "internal"
    assert ok.provider == "test-llm"
    assert ok.model == "deepseek-flash"
    assert ok.status == "ok"
    assert ok.input_tokens == 60
    assert ok.cached_input_tokens == 40
    assert ok.output_tokens == 10
    assert ok.latency_ms is not None
    assert failure.status == "error"
    assert "HTTP 400" in failure.error


async def test_gateway_records_json_usage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "model": "gpt-test",
                    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                    "usage": {
                        "prompt_tokens": 300,
                        "completion_tokens": 30,
                        "total_tokens": 330,
                    },
                },
            )
        return httpx.Response(404)

    async with _open(settings, handler) as client:
        response = await client.post(
            "/fake-openai/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "ping"}]},
        )
        assert response.status_code == 200

    events = _read(settings.resolve_usage_root())
    assert len(events) == 1
    event = events[0]
    assert event.capability == "gateway"
    assert event.source == "gateway"
    assert event.provider == "fake-openai"
    assert event.model == "gpt-test"
    assert event.input_tokens == 300
    assert event.output_tokens == 30
    assert event.status == "ok"


async def test_gateway_records_streamed_openai_usage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"po"}}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":5,"total_tokens":55}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    async with _open(settings, handler) as client:
        response = await client.post(
            "/fake-openai/v1/chat/completions",
            json={
                "model": "gpt-test",
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        assert response.status_code == 200
        await response.aread()

    events = _read(settings.resolve_usage_root())
    assert len(events) == 1
    assert events[0].detail["stream"] is True
    assert events[0].input_tokens == 50
    assert events[0].output_tokens == 5


async def test_gateway_records_anthropic_streamed_usage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b"event: message_start\n"
                b'data: {"type":"message_start","message":{"usage":{"input_tokens":80,"cache_read_input_tokens":20}}}\n\n'
                b"event: content_block_delta\n"
                b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"po"}}\n\n'
                b"event: message_delta\n"
                b'data: {"type":"message_delta","usage":{"output_tokens":12}}\n\n'
                b"event: message_stop\n"
                b'data: {"type":"message_stop"}\n\n'
            ),
        )

    async with _open(settings, handler) as client:
        response = await client.post(
            "/fake-anthropic/v1/messages",
            json={
                "model": "claude-test",
                "max_tokens": 16,
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        assert response.status_code == 200
        await response.aread()

    events = _read(settings.resolve_usage_root())
    assert len(events) == 1
    assert events[0].input_tokens == 80
    assert events[0].cached_input_tokens == 20
    assert events[0].output_tokens == 12
    assert events[0].total_tokens == 112


# -- HTTP API --------------------------------------------------------------


async def test_usage_overview_and_records(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ledger = UsageLedger(settings.resolve_usage_root())
    ledger.record(_event())
    ledger.record(
        _event(
            capability="gateway",
            provider="fake-openai",
            model="gpt-test",
            status="error",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )
    )

    async with _open(settings, lambda request: httpx.Response(500)) as client:
        overview = await client.get("/api/v1/usage/overview?days=7")
        assert overview.status_code == 200, overview.text
        body = overview.json()
        assert body["totals"]["requests"] == 2
        assert body["totals"]["errors"] == 1
        assert body["totals"]["estimated_requests"] == 1
        assert body["totals"]["unreported_requests"] == 1
        assert body["currency"] == "USD"
        assert body["prices"][0]["match"] == "deepseek-flash"
        assert len(body["by_day"]) == 8  # 7 days, inclusive of both ends

        records = await client.get("/api/v1/usage/records?days=7")
        assert records.status_code == 200, records.text
        page = records.json()
        assert page["total"] == 2
        by_capability = {
            item["capability"]: item for item in page["items"]
        }
        assert by_capability["assistant.chat"]["cost_source"] == "estimated"
        assert by_capability["gateway"]["cost_source"] == "unreported"

        filtered = await client.get(
            "/api/v1/usage/records?days=7&capability=gateway"
        )
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["capability"] == "gateway"

        paged = await client.get("/api/v1/usage/records?days=7&limit=1&offset=1")
        assert paged.json()["total"] == 2
        assert len(paged.json()["items"]) == 1


async def test_usage_endpoints_report_a_disabled_ledger(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, usage=UsageConfig(enabled=False, root=str(tmp_path / "usage"))
    )
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        response = await client.get("/api/v1/usage/overview")
    assert response.status_code == 503
    assert "usage.enabled" in response.json()["detail"]


# -- provider balances -----------------------------------------------------


def _balance_handler(calls: dict[str, int]):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        calls[host] = calls.get(host, 0) + 1
        assert request.headers["authorization"] == "Bearer secret-key"
        if host == "api.deepseek.com":
            return httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "CNY",
                            "total_balance": "110.00",
                            "granted_balance": "10.00",
                            "topped_up_balance": "100.00",
                        }
                    ],
                },
            )
        if host == "openrouter.ai":
            return httpx.Response(
                200, json={"data": {"total_credits": 100.5, "total_usage": 25.75}}
            )
        if host == "api.moonshot.ai":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "available_balance": 49.58894,
                        "voucher_balance": 46.58893,
                        "cash_balance": 3.00001,
                    },
                    "status": True,
                },
            )
        if host == "api.siliconflow.cn":
            return httpx.Response(
                200,
                json={
                    "code": 20000,
                    "message": "OK",
                    "data": {
                        "balance": "0.88",
                        "chargeBalance": "88.00",
                        "totalBalance": "88.88",
                    },
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    return handler


async def test_balances_query_the_four_supported_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, int] = {}
    settings = _settings(
        tmp_path,
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
    )
    for env in ("DEEPSEEK_KEY", "OPENROUTER_KEY", "MOONSHOT_KEY", "SILICONFLOW_KEY"):
        monkeypatch.setenv(env, "secret-key")

    async with _open(settings, _balance_handler(calls)) as client:
        inputs = [
            ("deepseek", "https://api.deepseek.com", "DEEPSEEK_KEY"),
            ("openrouter", "https://openrouter.ai/api", "OPENROUTER_KEY"),
            ("moonshot", "https://api.moonshot.ai", "MOONSHOT_KEY"),
            ("siliconflow", "https://api.siliconflow.cn", "SILICONFLOW_KEY"),
        ]
        for name, base_url, env in inputs:
            created = await client.post(
                "/api/v1/ai/integrations",
                json={
                    "name": name,
                    "label": name.title(),
                    "kind": "openai",
                    "base_url": base_url,
                    "api_key_env": env,
                    "default_model": "model-1",
                    "enabled": True,
                },
            )
            assert created.status_code == 201, created.text

        response = await client.get("/api/v1/usage/balances")
        assert response.status_code == 200, response.text
        balances = {item["name"]: item for item in response.json()["balances"]}
        assert balances["deepseek"]["total"] == "110.00"
        assert balances["deepseek"]["currency"] == "CNY"
        assert balances["deepseek"]["lines"][0] == {
            "label": "Granted balance",
            "value": "10.00",
        }
        assert balances["openrouter"]["total"] == "74.75"
        assert balances["openrouter"]["currency"] == "USD"
        assert balances["moonshot"]["total"] == "49.58894"
        assert balances["siliconflow"]["total"] == "88.88"
        assert balances["siliconflow"]["currency"] == "CNY"
        assert balances["fake-openai"]["supported"] is False
        assert balances["deepseek"]["cached"] is False

        cached = await client.get("/api/v1/usage/balances")
        cached_by_name = {
            item["name"]: item for item in cached.json()["balances"]
        }
        assert cached_by_name["deepseek"]["cached"] is True
        assert calls["api.deepseek.com"] == 1

        refreshed = await client.post("/api/v1/usage/balances/refresh")
        refreshed_by_name = {
            item["name"]: item for item in refreshed.json()["balances"]
        }
        assert refreshed_by_name["deepseek"]["cached"] is False
        assert calls["api.deepseek.com"] == 2


async def test_balances_report_a_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(
        tmp_path,
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
    )
    monkeypatch.delenv("MISSING_KEY", raising=False)
    async with _open(settings, _balance_handler({})) as client:
        await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "deepseek",
                "kind": "openai",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "MISSING_KEY",
                "default_model": "model-1",
            },
        )
        response = await client.get("/api/v1/usage/balances")
    item = next(
        balance
        for balance in response.json()["balances"]
        if balance["name"] == "deepseek"
    )
    assert item["supported"] is True
    assert item["total"] == ""
    assert "MISSING_KEY" in item["error"]


# -- dynamic adapters ------------------------------------------------------


async def test_dynamic_translator_records_the_bound_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(
        tmp_path,
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
    )
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    ai = AISettingsService(settings)
    ai.load()
    record = ai.create_integration(
        IntegrationDraft(
            name="bound-llm",
            label="Bound LLM",
            kind="openai",
            base_url="https://llm.example",
            api_key_env="TEST_LLM_KEY",
            default_model="deepseek-flash",
            gateway=False,
        )
    )
    ai.set_binding(
        "meetings.translate",
        BindingDraft(integration_id=record.id, model=""),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        lines = json.loads(payload["messages"][1]["content"])["lines"]
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {"message": {"content": json.dumps([f"ZH:{line}" for line in lines])}}
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 5},
            },
        )

    ledger = UsageLedger(settings.resolve_usage_root())
    translator = DynamicTranslator(
        ai,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ledger,
    )
    result = await translator.translate(["hello"], source="en", target="zh")
    assert result.texts == ["ZH:hello"]

    events = _read(settings.resolve_usage_root())
    assert len(events) == 1
    assert events[0].capability == "meetings.translate"
    assert events[0].provider == "bound-llm"
    assert events[0].model == "deepseek-flash"
    assert events[0].input_tokens == 25
