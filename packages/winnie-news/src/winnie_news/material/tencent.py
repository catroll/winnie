"""Fetch the Tencent News hot list by invoking ``tencent-news-cli``.

``tencent-news-cli hot`` only prints human-readable text, so this module:

  1. locates the CLI (``--cli`` / ``$TENCENT_NEWS_CLI`` / PATH /
     ``~/.tencent-news-cli/bin``);
  2. runs ``hot --limit N``, treating a timeout or a non-zero exit as failure;
  3. parses the ranked entries from the text into rank / title / summary /
     media / published_at / url fields;
  4. writes JSON (``--flat`` writes only the item array), JSONL or CSV. With
     ``--material`` it writes one document in the ``winnie.news.material/v1``
     format instead (see ``docs/news.md``).

Default output::

    {
      "source": "tencent-news",
      "channel": "hot",
      "generated_at": "2026-10-20 23:21",
      "count": 10,
      "items": [
        {
          "rank": 1,
          "title": "...",
          "summary": "...",
          "media": "Xinhua",
          "published_at": "2026-10-20 20:11",
          "url": "https://view.inews.qq.com/a/..."
        }
      ]
    }

Fields the CLI did not return (a summary of ``...``, a missing source) are
omitted rather than filled with an empty string. Progress and warnings go to
the log; stdout carries data only.

Requires an installed and configured ``tencent-news-cli``; this module itself
uses the standard library only.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
from typing import Any

from winnie_news.common.jsonio import (
    add_output_args,
    dump_csv,
    dump_json,
    dump_jsonl,
    infer_format,
    output_stream,
)
from winnie_news.common.material import items_from_tencent_hot, make_document
from winnie_news.common.timeutil import normalize_time

logger = logging.getLogger(__name__)

CLI_NAME = "tencent-news-cli"
CLI_ENV = "TENCENT_NEWS_CLI"
DEFAULT_CLI_PATH = os.path.expanduser("~/.tencent-news-cli/bin/tencent-news-cli")
CSV_FIELDS = ["rank", "title", "summary", "media", "published_at", "url"]

HEADER_RE = re.compile(r"^【腾讯新闻\s*[-－]\s*热点榜】\s*(?P<generated_at>.*)$")
ITEM_RE = re.compile(r"^\s*(?P<rank>\d+)\.\s*标题\s*[:：]\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^\s+(?P<key>摘要|来源|发布时间|链接)\s*[:：]\s*(?P<value>.*)$")
FOOTER_RE = re.compile(r"^共\s*(?P<count>\d+)\s*条")

FIELD_MAP = {
    "摘要": "summary",
    "来源": "media",
    "发布时间": "published_at",
    "链接": "url",
}
EMPTY_PLACEHOLDERS = {"...", "…", "。。。"}


class WrapperError(RuntimeError):
    """The CLI call failed or its output could not be parsed."""


def resolve_cli(explicit: str | None = None) -> str:
    """Find the CLI: argument, then environment variable, PATH, install path."""
    if explicit:
        path = os.path.expanduser(explicit)
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            raise WrapperError(
                f"the file given to --cli does not exist or is not executable: {path}"
            )
        return path

    env_path = os.environ.get(CLI_ENV)
    if env_path:
        path = os.path.expanduser(env_path)
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            raise WrapperError(
                f"the file in ${CLI_ENV} does not exist or is not executable: {path}"
            )
        return path

    found = shutil.which(CLI_NAME)
    if found:
        return found

    if os.path.isfile(DEFAULT_CLI_PATH) and os.access(DEFAULT_CLI_PATH, os.X_OK):
        return DEFAULT_CLI_PATH

    raise WrapperError(
        f"{CLI_NAME} not found. Install it, or pass a path with --cli / ${CLI_ENV}."
    )


def run_hot(cli: str, limit: int, timeout: float) -> str:
    """Run ``hot --limit N`` and return its stdout text."""
    command = [cli, "hot", "--limit", str(limit)]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise WrapperError(f"calling {CLI_NAME} timed out (>{timeout:g}s)") from exc
    except OSError as exc:
        raise WrapperError(f"could not execute {CLI_NAME}: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            raise WrapperError(
                f"{CLI_NAME} hot exited with code {proc.returncode}: {detail}"
            )
        raise WrapperError(f"{CLI_NAME} hot exited with code {proc.returncode}")

    if proc.stderr.strip():
        logger.info("%s", proc.stderr.strip())

    return proc.stdout


def _clean(value: str) -> str:
    value = value.strip()
    return "" if value in EMPTY_PLACEHOLDERS else value


def parse_hot(text: str) -> dict[str, Any]:
    """Parse the text output of ``hot`` into the shared structure."""
    generated_at = ""
    expected_count: int | None = None
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        header = HEADER_RE.match(line)
        if header:
            generated_at = header.group("generated_at").strip()
            continue

        item_match = ITEM_RE.match(line)
        if item_match:
            current = {"rank": int(item_match.group("rank"))}
            title = _clean(item_match.group("title"))
            if title:
                current["title"] = title
            items.append(current)
            continue

        field_match = FIELD_RE.match(line)
        if field_match and current is not None:
            key = FIELD_MAP[field_match.group("key")]
            value = _clean(field_match.group("value"))
            if value:
                current[key] = value
            continue

        footer = FOOTER_RE.match(line.strip())
        if footer:
            expected_count = int(footer.group("count"))
            continue

    if not items:
        if expected_count == 0:
            pass
        else:
            raise WrapperError(
                "parsed no hot entries; tencent-news-cli's output format may have changed"
            )

    if expected_count is not None and expected_count != len(items):
        logger.warning(
            "CLI reported %d entries but %d were parsed",
            expected_count,
            len(items),
        )

    return {
        "source": "tencent-news",
        "channel": "hot",
        "generated_at": generated_at,
        "count": len(items),
        "items": items,
    }


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the Tencent News hot list via tencent-news-cli and print it as JSON."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --limit 20 -o hot.json\n"
            "  %(prog)s --flat --compact | jq '.[].title'\n"
            "  %(prog)s --format jsonl -o hot.jsonl\n"
        ),
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=10,
        help="number of entries to return (default 10, matching the CLI)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--flat",
        action="store_true",
        help="print only the item array, without source / channel / generated_at",
    )
    group.add_argument(
        "--material",
        action="store_true",
        help="emit one document in the winnie.news.material/v1 format",
    )
    parser.add_argument(
        "--cli",
        metavar="PATH",
        help=f"path to the CLI (default: ${CLI_ENV}, PATH, then ~/.tencent-news-cli/bin)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds before the CLI call times out (default 30)",
    )
    add_output_args(parser, formats=("json", "jsonl", "csv"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fmt = args.format or infer_format(args.out)
    if fmt not in ("json", "jsonl", "csv"):
        fmt = "json"

    try:
        cli = resolve_cli(args.cli)
        text = run_hot(cli, args.limit, args.timeout)
        document = parse_hot(text)
    except WrapperError as exc:
        logger.error("%s", exc)
        return 1

    items = document["items"]
    with output_stream(args.out) as out:
        if args.material:
            extra = {}
            generated_at = normalize_time(document.get("generated_at"))
            if generated_at:
                extra["list_generated_at"] = generated_at
            material = make_document(
                feed="tencent-news-hot",
                generator="winnie_news.material/tencent.py",
                items=items_from_tencent_hot(document),
                extra=extra,
            )
            dump_json(material, out, compact=args.compact)
        elif fmt == "jsonl":
            dump_jsonl(items, out)
        elif fmt == "csv":
            dump_csv(items, out, CSV_FIELDS)
        else:
            dump_json(items if args.flat else document, out, compact=args.compact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
