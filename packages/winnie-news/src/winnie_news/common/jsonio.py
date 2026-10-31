"""JSON/JSONL/CSV output helpers used by the stage command-line entry points.

Each stage is usable from Python and from a shell. The shell path writes a file
(or stdout) in a format inferred from the extension, so a debugging run can
produce a JSON document that the next stage reads back unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, TextIO


def infer_format(
    out_path: str | None,
    *,
    formats: Iterable[str] = ("json", "jsonl", "csv"),
    default: str = "json",
) -> str:
    if not out_path:
        return default
    lower = out_path.lower()
    for fmt in formats:
        if lower.endswith("." + fmt):
            return fmt
    return default


@contextmanager
def output_stream(path: str | None) -> Iterator[TextIO]:
    """Write to ``path`` as UTF-8, or to stdout when it is empty."""
    if path:
        handle = open(path, "w", encoding="utf-8", newline="")
        try:
            yield handle
        finally:
            handle.close()
    else:
        yield sys.stdout


def dump_json(obj: Any, stream: TextIO, *, compact: bool = False) -> None:
    if compact:
        json.dump(obj, stream, ensure_ascii=False, separators=(",", ":"))
    else:
        json.dump(obj, stream, ensure_ascii=False, indent=2)
    stream.write("\n")


def dump_jsonl(items: Iterable[dict[str, Any]], stream: TextIO) -> None:
    for item in items:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def dump_csv(
    items: Iterable[dict[str, Any]], stream: TextIO, fieldnames: list[str]
) -> None:
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow({key: item.get(key, "") for key in fieldnames})


def add_output_args(
    parser: argparse.ArgumentParser,
    *,
    formats: tuple[str, ...] = ("json", "jsonl", "csv"),
) -> None:
    parser.add_argument("-o", "--out", help="output file; stdout when omitted")
    parser.add_argument(
        "--format",
        choices=formats,
        default=None,
        help="output format (inferred from -o's extension by default)",
    )
    parser.add_argument("--compact", action="store_true", help="do not indent JSON")


def add_request_args(
    parser: argparse.ArgumentParser,
    *,
    timeout: float = 20.0,
    retries: int | None = None,
) -> None:
    parser.add_argument(
        "--timeout",
        type=float,
        default=timeout,
        help=f"per-request timeout in seconds (default {timeout:g})",
    )
    if retries is not None:
        parser.add_argument(
            "--retries",
            type=int,
            default=retries,
            help=f"request retries (default {retries})",
        )
