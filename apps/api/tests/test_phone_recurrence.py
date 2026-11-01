"""Calendar recurrence: what the phone stores versus what a read returns.

The sync stores one row per `Events` row because that id is what the write path
updates and deletes. A read expands the row's RRULE into the requested window, so
a weekly class first held in August appears this week and a birthday stored with
its birth year appears every year. EXDATE instants are dropped; a rule that
cannot be parsed falls back to the base occurrence instead of hiding the event.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import JobsConfig
from winnie_phone.config import PhoneConfig, PhoneRetentionConfig
from winnie_phone.recurrence import expand, last_occurrence
from tests.test_phone import iso, register, sync

SHANGHAI = "Asia/Shanghai"

# 2026-08-21 01:30 UTC is 09:30 in Shanghai, a Friday.
SERIES_START = datetime(2026, 8, 21, 1, 30)
SERIES_END = SERIES_START + timedelta(hours=1)
WINDOW_SINCE = datetime(2026, 10, 28, 0, 0)
WINDOW_UNTIL = datetime(2026, 11, 20, 0, 0)


def test_weekly_rule_expands_into_the_window() -> None:
    occurrences = expand(
        starts_at=SERIES_START,
        ends_at=SERIES_END,
        timezone=SHANGHAI,
        rrule="FREQ=WEEKLY;BYDAY=FR",
        since=WINDOW_SINCE,
        until=WINDOW_UNTIL,
    )

    # Fridays inside the window: 10-30, 11-06, 11-13. The 11-20 occurrence is
    # 01:30 UTC, after the window's 00:00 UTC end.
    assert [row.starts_at for row in occurrences] == [
        datetime(2026, 10, 30, 1, 30),
        datetime(2026, 11, 6, 1, 30),
        datetime(2026, 11, 13, 1, 30),
    ]
    # The one-hour duration travels with every occurrence, not the series end.
    assert {row.ends_at for row in occurrences} == {
        datetime(2026, 10, 30, 2, 30),
        datetime(2026, 11, 6, 2, 30),
        datetime(2026, 11, 13, 2, 30),
    }


def test_a_birthday_stored_with_its_birth_year_appears_this_year() -> None:
    # The row the sync stores for "吴倩的生日": all-day, DTSTART = birth date.
    occurrences = expand(
        starts_at=datetime(1990, 11, 1, 0, 0),
        ends_at=datetime(1990, 11, 2, 0, 0),
        timezone=SHANGHAI,
        rrule="FREQ=YEARLY",
        since=datetime(2026, 10, 28, 0, 0),
        until=datetime(2026, 11, 5, 0, 0),
    )

    assert len(occurrences) == 1
    # Android stores an all-day event's DTSTART as UTC midnight of the local
    # date, so the yearly occurrence is 2026-11-01T00:00Z: the same date, and
    # still "all day" for a reader that formats in the phone's zone.
    assert occurrences[0].starts_at == datetime(2026, 11, 1, 0, 0)
    assert occurrences[0].ends_at == datetime(2026, 11, 2, 0, 0)


def test_until_bounds_the_series() -> None:
    occurrences = expand(
        starts_at=SERIES_START,
        ends_at=SERIES_END,
        timezone=SHANGHAI,
        # 2026-11-06 09:59:59Z is after the 11-06 09:30 occurrence, so it stays.
        rrule="FREQ=WEEKLY;BYDAY=FR;UNTIL=20261106T095959Z",
        since=WINDOW_SINCE,
        until=WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == [
        datetime(2026, 10, 30, 1, 30),
        datetime(2026, 11, 6, 1, 30),
    ]


def test_exdate_in_utc_drops_that_occurrence() -> None:
    occurrences = expand(
        starts_at=SERIES_START,
        ends_at=SERIES_END,
        timezone=SHANGHAI,
        rrule="FREQ=WEEKLY;BYDAY=FR",
        exdate="20261106T013000Z",
        since=WINDOW_SINCE,
        until=WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == [
        datetime(2026, 10, 30, 1, 30),
        datetime(2026, 11, 13, 1, 30),
    ]


def test_exdate_in_local_time_drops_that_occurrence() -> None:
    occurrences = expand(
        starts_at=SERIES_START,
        ends_at=SERIES_END,
        timezone=SHANGHAI,
        rrule="FREQ=WEEKLY;BYDAY=FR",
        # Android providers write either form; 09:30 local is 01:30 UTC.
        exdate="20261113T093000",
        since=WINDOW_SINCE,
        until=WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == [
        datetime(2026, 10, 30, 1, 30),
        datetime(2026, 11, 6, 1, 30),
    ]


def test_a_local_start_is_expanded_in_local_wall_clock() -> None:
    # 23:30 in Shanghai is 15:30 UTC the same day; a daily rule must repeat at
    # 15:30 UTC, not at 23:30 UTC.
    occurrences = expand(
        starts_at=datetime(2026, 10, 1, 15, 30),
        ends_at=datetime(2026, 10, 1, 16, 0),
        timezone=SHANGHAI,
        rrule="FREQ=DAILY",
        since=datetime(2026, 10, 30, 0, 0),
        until=datetime(2026, 11, 1, 23, 59),
    )

    assert [row.starts_at for row in occurrences] == [
        datetime(2026, 10, 30, 15, 30),
        datetime(2026, 10, 31, 15, 30),
        datetime(2026, 11, 1, 15, 30),
    ]


def test_an_unparseable_rule_keeps_the_base_occurrence() -> None:
    occurrences = expand(
        starts_at=SERIES_START,
        ends_at=SERIES_END,
        timezone=SHANGHAI,
        rrule="FREQ=NONSENSE",
        since=datetime(2026, 8, 1, 0, 0),
        until=datetime(2026, 8, 31, 0, 0),
    )

    assert [row.starts_at for row in occurrences] == [SERIES_START]


def test_without_a_window_nothing_is_expanded() -> None:
    # "Everything the server holds" has no window to expand into, and inventing
    # one here would walk a daily rule forever.
    occurrences = expand(
        starts_at=SERIES_START,
        ends_at=SERIES_END,
        timezone=SHANGHAI,
        rrule="FREQ=DAILY",
        since=None,
        until=None,
    )

    assert [row.starts_at for row in occurrences] == [SERIES_START]


def test_last_occurrence_of_a_bounded_rule() -> None:
    assert last_occurrence(
        starts_at=SERIES_START,
        timezone=SHANGHAI,
        rrule="FREQ=WEEKLY;BYDAY=FR;UNTIL=20261106T095959Z",
    ) == datetime(2026, 11, 6, 1, 30)


def test_an_endless_rule_has_no_last_occurrence() -> None:
    # Retention must keep these rows; walking them to find an end would never
    # return, which is why the text is checked first.
    assert (
        last_occurrence(
            starts_at=SERIES_START,
            timezone=SHANGHAI,
            rrule="FREQ=WEEKLY;BYDAY=FR",
        )
        is None
    )


def _event(external_id: str, title: str, **overrides) -> dict:
    return {
        "external_id": external_id,
        "calendar_name": "Phone",
        "title": title,
        "timezone": SHANGHAI,
        "starts_at": SERIES_START.isoformat(),
        "ends_at": SERIES_END.isoformat(),
        **overrides,
    }


@pytest.mark.asyncio
async def test_a_read_expands_the_series_inside_its_window(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "calendar",
        [_event("901", "琪琪钢琴课", recurrence="FREQ=WEEKLY;BYDAY=FR")],
        mode="full",
    )

    listing = (
        await phone_client.get(
            "/api/v1/phone/calendar",
            params={
                "since": WINDOW_SINCE.isoformat(),
                "until": WINDOW_UNTIL.isoformat(),
            },
        )
    ).json()

    assert len(listing) == 3
    # Every occurrence names the same series, which is the id the write path
    # needs to update or delete it.
    assert {row["id"] for row in listing} == {"901"}
    assert [row["title"] for row in listing] == ["琪琪钢琴课"] * 3
    starts = [datetime.fromisoformat(row["starts_at"]) for row in listing]
    assert starts == sorted(starts)
    assert starts[0] == datetime(2026, 10, 30, 1, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_read_drops_exdates(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "calendar",
        [
            _event(
                "902",
                "琪琪钢琴课",
                recurrence="FREQ=WEEKLY;BYDAY=FR",
                exdate="20261106T013000Z",
            )
        ],
        mode="full",
    )

    listing = (
        await phone_client.get(
            "/api/v1/phone/calendar",
            params={
                "since": WINDOW_SINCE.isoformat(),
                "until": WINDOW_UNTIL.isoformat(),
            },
        )
    ).json()

    assert len(listing) == 2
    assert all(
        datetime.fromisoformat(row["starts_at"]) != datetime(2026, 11, 6, 1, 30, tzinfo=UTC)
        for row in listing
    )


@pytest.mark.asyncio
async def test_a_read_without_a_window_returns_the_series_row_once(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "calendar",
        [_event("903", "Standup", recurrence="FREQ=DAILY")],
        mode="full",
    )

    listing = (await phone_client.get("/api/v1/phone/calendar")).json()

    assert len(listing) == 1
    assert listing[0]["id"] == "903"


@pytest.mark.asyncio
async def test_ids_include_a_series_whose_start_is_outside_the_window(
    phone_client: httpx.AsyncClient,
) -> None:
    """The client diffs these ids against what it just read.

    It now uploads every repeating event, so a series that started before the
    window must be reconcilable; otherwise a deleted series would never be
    reported and would stay on the server.
    """
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "calendar",
        [
            _event("904", "Old series", recurrence="FREQ=WEEKLY;BYDAY=FR"),
            # A one-off well outside the window: only the repeating row is
            # expected to survive the window filter on /ids.
            _event(
                "905",
                "Last year",
                starts_at="2025-06-01T01:00:00+00:00",
                ends_at="2025-06-01T02:00:00+00:00",
            ),
        ],
        mode="full",
    )

    ids = (
        await phone_client.get(
            f"/api/v1/phone/devices/{device['id']}/ids",
            params={
                "kind": "calendar",
                "since": WINDOW_SINCE.isoformat(),
                "until": WINDOW_UNTIL.isoformat(),
            },
        )
    ).json()["external_ids"]

    assert "904" in ids
    assert "905" not in ids


@pytest.mark.asyncio
async def test_retention_keeps_a_live_series_and_drops_a_finished_one(
    tmp_path: Path,
) -> None:
    """A series row is not history: its occurrences may still be ahead.

    Without this distinction a birthday stored with its birth year would be
    deleted by the retention pass of the very sync that uploaded it.
    """
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        phone=PhoneConfig(
            enabled=True,
            retention=PhoneRetentionConfig(calendar_days=30),
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'recurrence.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            device = await register(client)
            await sync(
                client,
                device["id"],
                "calendar",
                [
                    _event("906", "Weekly", recurrence="FREQ=WEEKLY;BYDAY=FR"),
                    _event(
                        "907",
                        "Finished",
                        recurrence="FREQ=WEEKLY;BYDAY=FR;UNTIL=20260901T000000Z",
                    ),
                    _event("908", "Old one-off"),
                ],
                mode="full",
            )

            listing = (await client.get("/api/v1/phone/calendar")).json()
            titles = {row["title"] for row in listing}
            # The live series keeps its occurrence in the window even though its
            # first occurrence is older than the retention window.
            assert "Weekly" in titles
            assert "Finished" not in titles
            assert "Old one-off" not in titles
    await http.aclose()


# -- Lunar yearly rules ------------------------------------------------------
#
# A Chinese calendar provider writes a lunar birthday as a Gregorian DTSTART for
# one occurrence plus an RRULE whose BYMONTH/BYMONTHDAY are the *lunar* month
# and day. The rows below are the real ones from data/winnie.db; the expected
# instants were derived with borax directly (LunarDate(y, month, day) ->
# to_solar_date(), no winnie code) and cross-checked against lunardate, so this
# table is not the implementation asserting itself.

LUNAR_WINDOW_SINCE = datetime(2026, 11, 1, 0, 0)
LUNAR_WINDOW_UNTIL = datetime(2027, 12, 31, 0, 0)

LUNAR_ROWS = [
    # title, stored DTSTART (naive UTC), zone, RRULE, occurrences in the window
    (
        "圆圆生日",
        datetime(2024, 12, 12),
        "UTC",
        "FREQ=YEARLY;INTERVAL=1;WKST=MO;BYMONTHDAY=12;BYMONTH=11",
        [datetime(2026, 12, 20), datetime(2027, 12, 9)],
    ),
    (
        "老婆生日",
        datetime(2010, 10, 17, 22, 0),
        "Asia/Shanghai",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=11;BYMONTH=9",
        [datetime(2027, 10, 9, 22, 0)],
    ),
    (
        "自己的生日",
        datetime(2011, 9, 14, 2, 0),
        "Asia/Shanghai",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=17;BYMONTH=8",
        [datetime(2027, 9, 17, 2, 0)],
    ),
    (
        "舅舅生日",
        datetime(2012, 1, 14, 12, 0),
        "Asia/Shanghai",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=21;BYMONTH=12",
        [datetime(2027, 1, 28, 12, 0)],
    ),
    (
        "张文庆生日",
        datetime(2012, 8, 26, 3, 0),
        "Asia/Shanghai",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=10;BYMONTH=7",
        [datetime(2027, 8, 11, 3, 0)],
    ),
    (
        "外婆生日",
        datetime(2013, 3, 26, 22, 0),
        "Asia/Shanghai",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=16;BYMONTH=2",
        [datetime(2027, 3, 22, 22, 0)],
    ),
    (
        "爸爸生日",
        datetime(2013, 5, 2, 23, 0),
        "Asia/Shanghai",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=24;BYMONTH=3",
        [datetime(2027, 4, 29, 23, 0)],
    ),
    (
        # The Gregorian row in the same database: the rule matches its solar
        # date, so dateutil's expansion is already correct.
        "琪琪的生日",
        datetime(2023, 10, 31),
        "UTC",
        "FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=31;BYMONTH=10",
        [datetime(2027, 10, 31)],
    ),
    (
        "农历新年",
        datetime(2026, 2, 17),
        "UTC",
        "FREQ=YEARLY;INTERVAL=1;WKST=MO;BYMONTHDAY=1;BYMONTH=1",
        [datetime(2027, 2, 6)],
    ),
    (
        "农历小年",
        datetime(2027, 1, 31),
        "UTC",
        "FREQ=YEARLY;INTERVAL=1;WKST=MO;BYMONTHDAY=24;BYMONTH=12",
        [datetime(2027, 1, 31)],
    ),
    (
        "农历月半（元宵节）",
        datetime(2027, 2, 20),
        "UTC",
        "FREQ=YEARLY;INTERVAL=1;WKST=MO;BYMONTHDAY=15;BYMONTH=1",
        [datetime(2027, 2, 20)],
    ),
]


@pytest.mark.parametrize("title,starts_at,zone_name,rrule,expected", LUNAR_ROWS)
def test_lunar_yearly_rows_expand_to_the_real_dates(
    title: str,
    starts_at: datetime,
    zone_name: str,
    rrule: str,
    expected: list[datetime],
) -> None:
    occurrences = expand(
        starts_at=starts_at,
        ends_at=None,
        timezone=zone_name,
        rrule=rrule,
        since=LUNAR_WINDOW_SINCE,
        until=LUNAR_WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == expected, title


def test_a_lunar_rule_is_not_expanded_as_gregorian() -> None:
    """The old answer: 圆圆 on 2026-11-12, five weeks before the real date."""
    occurrences = expand(
        starts_at=datetime(2024, 12, 12),
        ends_at=None,
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12",
        since=LUNAR_WINDOW_SINCE,
        until=LUNAR_WINDOW_UNTIL,
    )

    assert datetime(2026, 11, 12) not in [row.starts_at for row in occurrences]
    assert occurrences[0].starts_at == datetime(2026, 12, 20)


def test_only_a_rule_that_matches_its_lunar_date_is_decoded() -> None:
    from winnie_phone.lunar import detect

    decoded = detect("FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12", datetime(2024, 12, 12))
    assert decoded is not None
    assert (decoded.month, decoded.day) == (11, 12)

    # Gregorian: the rule matches the start's solar date.
    assert detect("FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=31", datetime(2023, 10, 31)) is None
    # Not a yearly month/day rule.
    assert detect("FREQ=WEEKLY;BYDAY=FR", datetime(2024, 12, 12)) is None
    # Extra selectors mean a shape this decoder does not claim.
    assert (
        detect("FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12;BYDAY=MO", datetime(2024, 12, 12))
        is None
    )
    # A rule that matches neither reading is left to RFC 5545.
    assert detect("FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=3", datetime(2024, 12, 12)) is None


def test_lunar_occurrence_keeps_the_event_duration() -> None:
    occurrences = expand(
        starts_at=datetime(2024, 12, 12),
        ends_at=datetime(2024, 12, 13),
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12",
        since=LUNAR_WINDOW_SINCE,
        until=LUNAR_WINDOW_UNTIL,
    )

    assert occurrences[0].starts_at == datetime(2026, 12, 20)
    assert occurrences[0].ends_at == datetime(2026, 12, 21)


def test_lunar_rule_until_stops_the_series() -> None:
    occurrences = expand(
        starts_at=datetime(2024, 12, 12),
        ends_at=None,
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12;UNTIL=20261220T000000Z",
        since=LUNAR_WINDOW_SINCE,
        until=LUNAR_WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == [datetime(2026, 12, 20)]


def test_lunar_rule_count_counts_from_the_series_start() -> None:
    # Three occurrences: 2024-12-12, 2025-12-01, 2026-12-20. Only the last is
    # inside the window, which is also what makes the count semantics visible.
    occurrences = expand(
        starts_at=datetime(2024, 12, 12),
        ends_at=None,
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12;COUNT=3",
        since=LUNAR_WINDOW_SINCE,
        until=LUNAR_WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == [datetime(2026, 12, 20)]


def test_lunar_rule_expands_with_exdate() -> None:
    occurrences = expand(
        starts_at=datetime(2024, 12, 12),
        ends_at=None,
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12",
        exdate="20261220T000000Z",
        since=LUNAR_WINDOW_SINCE,
        until=LUNAR_WINDOW_UNTIL,
    )

    assert [row.starts_at for row in occurrences] == [datetime(2027, 12, 9)]


def test_a_thirtieth_birthday_falls_back_to_the_twenty_ninth() -> None:
    """A lunar month has 29 or 30 days; the 30th does not occur every year.

    lunar 2025-10-30 is 2025-12-19, and lunar 2029 has no 10-30, so the 2029
    occurrence is 2029-10-29 = 2029-12-04. Dropping the year would mean a
    birthday the owner is never told about.
    """
    occurrences = expand(
        starts_at=datetime(2025, 12, 19),
        ends_at=None,
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=30",
        since=datetime(2029, 1, 1),
        until=datetime(2029, 12, 31),
    )

    assert [row.starts_at for row in occurrences] == [datetime(2029, 12, 4)]


def test_last_occurrence_of_a_bounded_lunar_rule() -> None:
    assert last_occurrence(
        starts_at=datetime(2024, 12, 12),
        timezone="UTC",
        rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12;UNTIL=20271231T000000Z",
    ) == datetime(2027, 12, 9)
    # An unbounded lunar series never ends, so retention keeps the row.
    assert (
        last_occurrence(
            starts_at=datetime(2024, 12, 12),
            timezone="UTC",
            rrule="FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12",
        )
        is None
    )
