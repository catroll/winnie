"""Fetch the tophub.today "daily morning/evening digest" news list.

Flow:

  1. request https://tophub.today/daily and read the ``data-hash`` attribute of
     the ``#ai-summary-content`` element;
  2. request the JSONP endpoint https://idaily.today/tophub/share with that
     ``data-hash``;
  3. parse the JSONP response and print a JSON array; with ``--material`` print
     one document in the ``winnie.news.material/v1`` format instead (see
     ``docs/news.md``).

Only the Python standard library is required. Shared request, cleaning and
output logic lives in ``winnie_news.common``.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import string
import urllib.parse

from winnie_news.common.http import FetchError, fetch
from winnie_news.common.jsonio import (
    add_request_args,
    dump_json,
    output_stream,
)
from winnie_news.common.material import items_from_tophub_daily, make_document
from winnie_news.common.text import clean_text, decode_text

logger = logging.getLogger(__name__)

DAILY_URL = "https://tophub.today/daily"
SHARE_URL = "https://idaily.today/tophub/share"

LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
STRONG_RE = re.compile(r"<strong[^>]*>(.*?)</strong>", re.S | re.I)
JSONP_RE = re.compile(r"^[\w$.]+\s*\((.*)\)\s*;?\s*$", re.S)


def http_get(url, referer=None, timeout=15, retries=3):
    """GET a URL, retrying on failure, and return the decoded text."""
    headers = {}
    if referer:
        headers["Referer"] = referer
    return fetch(
        url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        parse=decode_text,
    )


def extract_data_hash(page):
    """Extract the ``data-hash`` of ``#ai-summary-content`` from the digest page."""
    for tag in re.finditer(r"<[^>]*\bid=\"ai-summary-content\"[^>]*>", page):
        match = re.search(r"\bdata-hash=\"([^\"]+)\"", tag.group(0))
        if match and match.group(1).strip():
            return match.group(1).strip()

    # Fallback: if the page structure changed, take the first non-empty data-hash.
    match = re.search(r"\bdata-hash=\"([^\"]+)\"", page)
    if match and match.group(1).strip():
        return match.group(1).strip()

    raise FetchError(
        "could not extract data-hash from the page; its structure may have changed"
    )


def parse_jsonp(text):
    """Parse a callback-wrapped JSONP response and return the JSON array."""
    match = JSONP_RE.match(text.strip())
    if not match:
        raise FetchError(f"not a valid JSONP response: {text[:120]!r}")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise FetchError(f"failed to parse the JSONP payload: {exc}") from exc
    if not isinstance(payload, list):
        raise FetchError("the JSONP response is not an array")
    return payload


def parse_sections(payload):
    """Turn ``[{title, html}]`` into ``[{title, items:[{title, summary}]}]``."""
    sections = []
    for section in payload:
        if not isinstance(section, dict):
            continue
        section_title = clean_text(str(section.get("title") or ""))
        items = []
        for raw_item in LI_RE.findall(str(section.get("html") or "")):
            strong = STRONG_RE.search(raw_item)
            if strong:
                item_title = clean_text(strong.group(1))
                summary = clean_text(raw_item[strong.end():]).lstrip("：: ")
            else:
                item_title = clean_text(raw_item)
                summary = ""
            if not item_title:
                continue
            item = {"title": item_title}
            if summary:
                item["summary"] = summary
            items.append(item)
        sections.append({"title": section_title, "items": items})
    return sections


def fetch_news(data_hash=None, timeout=15, retries=3):
    """Fetch and return the structured news list; derive data_hash if absent."""
    if not data_hash:
        page = http_get(DAILY_URL, timeout=timeout, retries=retries)
        data_hash = extract_data_hash(page)

    callback = "tophub_" + "".join(
        random.choices(string.ascii_lowercase + string.digits, k=12)
    )
    query = urllib.parse.urlencode({"callback": callback, "hash": data_hash})
    body = http_get(
        f"{SHARE_URL}?{query}",
        referer=DAILY_URL,
        timeout=timeout,
        retries=retries,
    )

    sections = parse_sections(parse_jsonp(body))
    if not sections:
        raise FetchError(
            "the endpoint returned no data; the data-hash may have expired "
            "or the API may have changed"
        )
    return sections


def flatten(sections):
    """Flatten the sections into one news array, tagging each item's section."""
    return [
        {"section": section["title"], **item}
        for section in sections
        for item in section["items"]
    ]


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the tophub.today daily morning/evening digest and print a JSON array."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --flat --out news.json\n"
            "  %(prog)s --hash 5898YGL... --compact\n"
        ),
    )
    parser.add_argument(
        "--hash",
        metavar="DATA_HASH",
        help="use this data-hash directly and skip the digest page request",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--flat",
        action="store_true",
        help=(
            "print one flat news array (each item carries a section field) "
            "instead of grouping by digest section"
        ),
    )
    group.add_argument(
        "--material",
        action="store_true",
        help="emit one document in the winnie.news.material/v1 format",
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="write to a file instead of stdout (UTF-8)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact JSON (default: indent of 2 spaces)",
    )
    add_request_args(parser, timeout=15, retries=3)
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    try:
        sections = fetch_news(
            data_hash=args.hash, timeout=args.timeout, retries=args.retries
        )
    except FetchError as exc:
        logger.error("%s", exc)
        return 1

    result = flatten(sections) if args.flat else sections
    with output_stream(args.out) as out:
        if args.material:
            document = make_document(
                feed="tophub-daily",
                generator="winnie_news.material/tophub_daily.py",
                items=items_from_tophub_daily(sections),
            )
            dump_json(document, out, compact=args.compact)
        else:
            dump_json(result, out, compact=args.compact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
