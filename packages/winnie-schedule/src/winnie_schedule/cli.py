"""Command line over the schedule parser and editor.

Every command takes the date it is about; the week file is derived from it, so
``winnie-schedule list 2026-10-05`` never needs a path. Dates accept
``today``, ``tomorrow``, ``yesterday``, ``+3d``, ``YYYY-MM-DD``, ``YYYYMMDD``
and ``MM/DD`` (current year); inside a week that is already open, ``MM/DD`` is
resolved against that week's seven days.

Ids printed by ``show``/``list`` are ``<stem>#<MM/DD>#<n>`` for a day event and
``<stem>#week#<n>`` for an item of a week-level section. They are positional:
after an insert or delete the numbers of later events in the same block shift,
so re-read the ids instead of storing them, or use ``--match``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from . import editor, layout, skill
from .editor import EditResult, ScheduleError, select_event
from .model import DAY_SCOPE, WEEK_SCOPE, Event, Week
from .parser import ParseError, parse_file

DATE_RE = re.compile(r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})$")
COMPACT_RE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")
MONTH_DAY_RE = re.compile(r"^(?P<month>\d{1,2})[-/](?P<day>\d{1,2})$")
OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<count>\d+)(?P<unit>[dwm]?)$")
WEEKDAY_NAMES = {label: index for index, label in enumerate(layout.WEEKDAY_LABELS)}


def parse_day(value: str, today: date | None = None) -> date:
    """Parse a date argument into a :class:`~datetime.date`."""
    text = value.strip()
    lowered = text.lower()
    today = today or date.today()
    if lowered == "today":
        return today
    if lowered == "tomorrow":
        return today + timedelta(days=1)
    if lowered == "yesterday":
        return today - timedelta(days=1)
    if lowered in WEEKDAY_NAMES:
        # The named weekday of the current Monday-to-Sunday week.
        return today - timedelta(days=today.weekday()) + timedelta(days=WEEKDAY_NAMES[lowered])
    if offset := OFFSET_RE.match(text):
        count = int(offset["count"]) * (-1 if offset["sign"] == "-" else 1)
        unit = offset["unit"] or "d"
        if unit == "d":
            return today + timedelta(days=count)
        if unit == "w":
            return today + timedelta(weeks=count)
        return today + timedelta(days=30 * count)
    for pattern, needs_year in ((DATE_RE, True), (COMPACT_RE, True), (MONTH_DAY_RE, False)):
        if match := pattern.match(text):
            year = int(match["year"]) if needs_year else today.year
            try:
                return date(year, int(match["month"]), int(match["day"]))
            except ValueError as exc:
                raise ScheduleError(f"{value!r} is not a real date: {exc}") from exc
    raise ScheduleError(f"cannot read {value!r} as a date; use YYYY-MM-DD, YYYYMMDD, MM/DD or today")


def resolve_day(week: Week, value: str) -> date:
    """Resolve a day selector inside an open week (``MM/DD`` against its days)."""
    if match := MONTH_DAY_RE.match(value.strip()):
        month, day = int(match["month"]), int(match["day"])
        for candidate in week.weeks.days:
            if candidate.month == month and candidate.day == day:
                return candidate
        raise ScheduleError(f"{value!r} is not one of the seven days of {week.stem}")
    return parse_day(value)


def _load(args: argparse.Namespace, day: date) -> Week:
    root = layout.resolve_root(args.root)
    return parse_file(layout.find_week(root, day))


def _week_from_id(args: argparse.Namespace) -> tuple[Week, str]:
    if not args.event:
        raise ScheduleError("pass an event id, or --week <date> together with --match")
    stem = args.event.split("#", 1)[0]
    try:
        weeks = layout.parse_stem(stem)
    except layout.LayoutError as exc:
        raise ScheduleError(f"{args.event!r} is not an event id: {exc}") from exc
    root = layout.resolve_root(args.root)
    path = layout.month_dir(root, weeks.monday) / f"{weeks.stem}.md"
    if not path.exists():
        raise ScheduleError(f"{path} does not exist")
    return parse_file(path), args.event


def _target(args: argparse.Namespace) -> tuple[Week, str | None]:
    """Return the week and the event id the command should act on."""
    if args.match:
        if not args.week:
            raise ScheduleError("--match needs --week <date> to know which week to search")
        week = _load(args, parse_day(args.week))
        day = resolve_day(week, args.at) if args.at else None
        event = select_event(
            week,
            match=args.match,
            day=day,
            scope=WEEK_SCOPE if args.week_items else None,
        )
        return week, event.id
    week, event_id = _week_from_id(args)
    return week, event_id


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
# Human output
# --------------------------------------------------------------------------- #


def _event_line(event: Event) -> str:
    flags = []
    if event.every:
        flags.append("每天")
    if event.weekday_tag:
        flags.append(event.weekday_tag)
    if event.time_range:
        flags.append(event.time_range)
    flags.extend(f"#{tag}" for tag in event.tags)
    index = int(event.id.rsplit("#", 1)[1])
    detail = f"  {' '.join(flags)}" if flags else ""
    return f"  [{'x' if event.done else ' '}] {index}{detail}  {event.text}"


def print_week(
    week: Week, *, only_day: date | None = None, pending: bool = False, done: bool = False
) -> None:
    print(f"{layout.human_range(week.weeks)}  {week.weeks.label}  {week.path}  ({week.layout})")
    if week.title:
        print(f"title: {week.title}")
    for warning in week.warnings:
        print(f"warning: {warning}")
    if week.layout == "legacy":
        print("note: no '### MM/DD' day sections (pre-2024 layout); only week-level items are listed")

    def wanted(event: Event) -> bool:
        if pending and event.done:
            return False
        if done and not event.done:
            return False
        return True

    for section in week.sections:
        events = [event for event in section.events if wanted(event)]
        if not events:
            continue
        print(f"\n## {section.title}")
        for event in events:
            print(_event_line(event))

    days = week.days
    if only_day is not None:
        days = [day for day in days if day.date == only_day]
        if not days:
            raise ScheduleError(f"{only_day:%m/%d} is not a day section of {week.path.name}")
    for day in days:
        events = [event for event in day.events if wanted(event)]
        print(f"\n{day.date:%m/%d} {day.label}")
        if not events:
            print("  (empty)")
        for event in events:
            print(_event_line(event))

    if week.days:
        print(f"\nids look like {week.stem}#{week.days[0].date:%m/%d}#1 or {week.stem}#week#1")


def print_edit(result: EditResult) -> None:
    print(f"{'would change' if result.dry_run else 'changed'}: {result.message} in {result.path}")
    if result.event is not None:
        print(f"  id: {result.event.id}")
        print(f"  {result.event.lines[0]}")
    if result.diff:
        sys.stdout.write(result.diff)
    elif not result.changed:
        print("  (nothing to change)")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_weeks(args: argparse.Namespace) -> int:
    root = layout.resolve_root(args.root)
    files = layout.week_files(root)
    if args.json:
        payload = []
        for path in files:
            weeks = layout.parse_stem(path.stem)
            payload.append(
                {
                    "file": str(path),
                    "stem": path.stem,
                    "week": weeks.to_dict(),
                    "layout": "canonical" if _has_days(path) else "legacy",
                }
            )
        _print_json(payload)
        return 0
    for path in files[-args.limit:]:
        weeks = layout.parse_stem(path.stem)
        kind = "canonical" if _has_days(path) else "legacy"
        print(f"{weeks.monday.isoformat()} ~ {weeks.sunday.isoformat()}  {weeks.label:10s} {kind:9s} {path}")
    print(f"\n{len(files)} week files under {root / 'work'}")
    return 0


def _has_days(path: Path) -> bool:
    try:
        return bool(parse_file(path).days)
    except ParseError:
        return False


def cmd_show(args: argparse.Namespace) -> int:
    day = parse_day(args.date)
    week = _load(args, day)
    if args.json:
        _print_json(week.to_dict(include_lines=not args.no_lines))
        return 0
    print_week(week, only_day=day if args.day else None, pending=args.pending, done=args.done)
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    week = _load(args, parse_day(args.date))
    _print_json(week.to_dict(include_lines=not args.no_lines))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    day = parse_day(args.date)
    week = _load(args, day)
    only_day = None
    if args.day:
        only_day = resolve_day(week, args.day)
    elif args.day_only:
        only_day = day
    if args.json:
        payload = week.to_dict(include_lines=False)
        if only_day is not None:
            payload["days"] = [item for item in payload["days"] if item["date"] == only_day.isoformat()]
        _print_json(payload)
        return 0
    print_week(week, only_day=only_day, pending=args.pending, done=args.done)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    day = parse_day(args.date)
    week = _load(args, day)
    target = None if args.week_scope else (resolve_day(week, args.at) if args.at else day)
    start = end = None
    if args.time:
        start, end = editor.normalize_time(args.time)
    result = editor.add_event(
        week,
        text=args.text,
        day=target,
        start=start or None,
        end=end,
        weekday=args.weekday,
        every=args.every,
        tags=tuple(args.tag),
        done=args.done,
        after=args.after,
        at_top=args.top,
        dry_run=args.dry_run,
    )
    _report(result, args)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    if args.done and args.undone:
        raise ScheduleError("pass --done or --undone, not both")
    week, event_id = _target(args)
    assert event_id is not None
    start: object = editor.UNSET
    end: object = editor.UNSET
    if args.clear_time:
        start, end = "", None
    elif args.time is not None:
        start, end = editor.normalize_time(args.time)
    done: object = editor.UNSET
    if args.done:
        done = True
    if args.undone:
        done = False
    every: object = editor.UNSET
    if args.every:
        every = True
    if args.no_every:
        every = False
    result = editor.update_event(
        week,
        event_id,
        text=args.text if args.text is not None else editor.UNSET,
        start=start,
        end=end,
        every=every,
        tags_add=tuple(args.tag),
        tags_remove=tuple(args.untag),
        done=done,
        note=args.note,
        move_to=resolve_day(week, args.move) if args.move else None,
        dry_run=args.dry_run,
    )
    _report(result, args)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    week, event_id = _target(args)
    assert event_id is not None
    result = editor.delete_event(week, event_id, dry_run=args.dry_run)
    if args.json:
        _print_json(result.to_dict())
        return 0
    print(f"{'would delete' if result.dry_run else 'deleted'}: {event_id} from {result.path}")
    for line in result.removed:
        print(f"  {line}")
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    if args.action == "install":
        path = skill.install(args.target)
        print(f"installed: {path}")
        return 0
    current, path = skill.check(args.target)
    print(f"{'up to date' if current else 'out of date'}: {path}")
    return 0 if current else 1


def _report(result: EditResult, args: argparse.Namespace) -> None:
    if args.json:
        _print_json(result.to_dict())
        return
    print_edit(result)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="winnie-schedule",
        description="Parse and edit the weekly schedule files kept in the WLB work/ tree.",
    )
    parser.add_argument("--root", default=None, help="schedule root (default: ~/Projects/Mine/WLB)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a human summary")
    sub = parser.add_subparsers(dest="command", required=True)

    weeks = sub.add_parser("weeks", help="list week files")
    weeks.add_argument("--limit", type=int, default=20, help="how many to print (default: 20)")
    weeks.set_defaults(func=cmd_weeks)

    show = sub.add_parser("show", help="show a week, or one day of it")
    show.add_argument("date", help="any date in the week")
    show.add_argument("--day", action="store_true", help="show only the given day")
    show.add_argument("--pending", action="store_true", help="only unchecked events")
    show.add_argument("--done", action="store_true", help="only checked events")
    show.add_argument("--no-lines", action="store_true", help="omit raw lines from --json")
    show.set_defaults(func=cmd_show)

    parse = sub.add_parser("parse", help="dump the parsed week as JSON")
    parse.add_argument("date", help="any date in the week")
    parse.add_argument("--no-lines", action="store_true", help="omit raw lines from the output")
    parse.set_defaults(func=cmd_parse)

    listing = sub.add_parser("list", help="list the events of a week or a day")
    listing.add_argument("date", help="any date in the week")
    listing.add_argument("--day", dest="day", help="only this day of the week (MM/DD)")
    listing.add_argument("--day-only", action="store_true", help="only the day given as date")
    listing.add_argument("--pending", action="store_true", help="only unchecked events")
    listing.add_argument("--done", action="store_true", help="only checked events")
    listing.set_defaults(func=cmd_list)

    add = sub.add_parser("add", help="add an event to a day, or to the week-level list")
    add.add_argument("date", help="the day the event belongs to")
    add.add_argument("text", help="event text; newlines become indented continuation lines")
    add.add_argument("--at", help="target another day inside the same week (MM/DD)")
    add.add_argument("--week-scope", action="store_true", help="add to ## 工作事项 instead of a day")
    add.add_argument("--time", help="HH:MM or HH:MM~HH:MM")
    add.add_argument("--tag", action="append", default=[], help="extra [tag], repeatable")
    add.add_argument("--weekday", action="store_true", help="include the [周X] tag")
    add.add_argument("--every", action="store_true", help="include the [每天] tag")
    add.add_argument("--done", action="store_true", help="insert as already checked")
    add.add_argument("--after", help="insert after this event id")
    add.add_argument("--top", action="store_true", help="insert before the other events of the block")
    add.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    add.set_defaults(func=cmd_add)

    update = sub.add_parser("update", help="change one event")
    update.add_argument("event", nargs="?", help="event id, as printed by show/list")
    update.add_argument("--week", help="the week to search when using --match")
    update.add_argument("--match", help="substring of the event text (must be unique)")
    update.add_argument("--at", help="restrict --match to one day (MM/DD)")
    update.add_argument("--week-items", action="store_true", help="restrict --match to week-level items")
    update.add_argument("--text", help="replace the event text")
    update.add_argument("--time", help="set HH:MM or HH:MM~HH:MM")
    update.add_argument("--clear-time", action="store_true", help="remove the time tag")
    update.add_argument("--done", action="store_true", help="check the event")
    update.add_argument("--undone", action="store_true", help="uncheck the event")
    update.add_argument("--tag", action="append", default=[], help="add a [tag]")
    update.add_argument("--untag", action="append", default=[], help="remove a [tag]")
    update.add_argument("--note", help="append indented lines to the event; pass real newlines with $'a\\nb'")
    update.add_argument("--move", help="move the event to another day of the week")
    update.add_argument("--every", action="store_true", help="add the [每天] marker")
    update.add_argument("--no-every", action="store_true", help="remove the [每天] marker")
    update.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    update.set_defaults(func=cmd_update)

    delete = sub.add_parser("delete", help="delete one event")
    delete.add_argument("event", nargs="?", help="event id, as printed by show/list")
    delete.add_argument("--week", help="the week to search when using --match")
    delete.add_argument("--match", help="substring of the event text (must be unique)")
    delete.add_argument("--at", help="restrict --match to one day (MM/DD)")
    delete.add_argument("--week-items", action="store_true", help="restrict --match to week-level items")
    delete.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    delete.set_defaults(func=cmd_delete)

    skill_parser = sub.add_parser("skill", help="install or check the Agent Skill document")
    skill_parser.add_argument("action", choices=("install", "check"), help="what to do")
    skill_parser.add_argument("--target", help="install directory (default: ~/.agents/skills/winnie-schedule)")
    skill_parser.set_defaults(func=cmd_skill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except (ParseError, layout.LayoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
