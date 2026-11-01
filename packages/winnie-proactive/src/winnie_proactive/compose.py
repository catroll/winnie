"""Turn a finding into the words the owner sees.

The template is the floor: it needs no model, costs nothing, and must be
correct with no provider configured. Every date, name and count in it comes
from the finding, never from a model. A model may rewrite the body later; this
module is what still works when none is bound.
"""

from __future__ import annotations

from datetime import date

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def weekday(value: date) -> str:
    return WEEKDAYS[value.weekday()]


def birthday_heads_up(
    name: str, on: date, days: int, note: str
) -> tuple[str, str]:
    return (
        f"{name}生日还有 {days} 天",
        f"{on.isoformat()}（{weekday(on)}）是{name}的生日{note}。\n"
        "可以先想好礼物，需要我记一个提醒吗？",
    )


def birthday_prepare(name: str, on: date, days: int, note: str) -> tuple[str, str]:
    return (
        f"{name}生日还有 {days} 天",
        f"{on.isoformat()}（{weekday(on)}）是{name}的生日{note}。\n"
        "这周定下来：礼物、当天安排、要不要订蛋糕。",
    )


def birthday_confirm(name: str, on: date, days: int, note: str) -> tuple[str, str]:
    return (
        f"后天就是{name}的生日" if days == 2 else f"{name}生日还有 {days} 天",
        f"{on.isoformat()}（{weekday(on)}）{note}。"
        "礼物定了吗？要我把问候语先拟好，到时你改两个字就能发。",
    )


def birthday_day(name: str, on: date, note: str) -> tuple[str, str]:
    return (
        f"今天 · {name}的生日",
        f"今天是 {on.isoformat()}（{weekday(on)}），{name}的生日{note}。\n"
        "上午发条消息或打个电话都来得及；要我先拟一条吗？",
    )


def birthday_follow_up(name: str, on: date) -> tuple[str, str]:
    return (
        f"昨天是{name}的生日",
        f"{on.isoformat()} 的生日过了。要记一笔吗（送了什么、有没有通话）？"
        "记下来，明年提醒会更准。",
    )


def birthday_conflict(name: str, dates: list[date], notes: list[str]) -> tuple[str, str]:
    listed = "、".join(
        f"{on.isoformat()}（{weekday(on)}，{note}）"
        for on, note in zip(dates, notes)
    )
    return (
        f"{name}有两个生日日期",
        f"手机里有 {listed}。哪一个是你要我提醒的？"
        "回复一个日期，我就只按那个提醒。",
    )


def leave_by(text: str, on: date, start: str, minutes: int) -> tuple[str, str]:
    return (
        f"{start} {text}",
        f"{on.isoformat()}（{weekday(on)}）{start} 开始，还有约 {minutes} 分钟。"
        "现在出发比较稳。",
    )


def overdue(items: list[tuple[date, str]]) -> tuple[str, str]:
    lines = "\n".join(f"- {on.isoformat()} {text}" for on, text in items[:10])
    more = len(items) - 10
    body = f"这些事还没打勾：\n{lines}"
    if more > 0:
        body += f"\n…还有 {more} 件"
    return (f"有 {len(items)} 件没打勾", body)


def digest(entries: list[str], slot: str) -> tuple[str, str]:
    label = "早上好" if slot == "morning" else "晚上好"
    if not entries:
        return (f"{label} · 今天没有要紧的事", "需要我做什么，直接说。")
    body = "\n".join(f"- {entry}" for entry in entries[:12])
    more = len(entries) - 12
    if more > 0:
        body += f"\n…还有 {more} 条"
    return (f"{label} · {len(entries)} 件事", body)


__all__ = [
    "birthday_confirm",
    "birthday_conflict",
    "birthday_day",
    "birthday_follow_up",
    "birthday_heads_up",
    "birthday_prepare",
    "digest",
    "leave_by",
    "overdue",
    "weekday",
]
