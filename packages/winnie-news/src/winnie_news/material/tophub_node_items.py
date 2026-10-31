"""Fetch one node's ranked items for a chosen date from tophub.today.

Endpoint: POST https://tophub.today/node-items-by-date
Form:     p=<page>&date=<YYYY-MM-DD>&nodeid=<numeric node id>
Response: {"status":200,"data":{"items":[...],"pagesize":30,"nowpage":1},"error":""}

Item fields: ID / title / description / thumbnail / url / md5 / extra / time /
nodeids / topicid / domain / sitename / logo

Examples::

    # Zhihu hot list (nodeid=6) today, all pages, JSON to the terminal
    python -m winnie_news.material.tophub_node_items --nodeid 6

    # Weibo hot search (nodeid=1) for a given date, saved as CSV
    python -m winnie_news.material.tophub_node_items --nodeid 1 \
        --date 2026-10-20 -o wb.csv

    # Only the first 2 pages as JSONL (one item per line, easy for jq/grep)
    python -m winnie_news.material.tophub_node_items --nodeid 6 \
        --pages 2 --format jsonl

    # The shared material format (winnie.news.material/v1)
    python -m winnie_news.material.tophub_node_items --nodeid 6 \
        --material -o zhihu.material.json

Notes:

- Pages are requested as p=1,2,3...; an empty page ends the walk, and items are
  deduplicated by ID across pages.
- The endpoint currently works without a cookie. If a node needs a login, pass
  one with ``--cookie`` or the ``TOPHUB_COOKIE`` environment variable (never
  commit it to the script or the repository).
- For historical dates the endpoint often answers
  ``{"status":100,"data":{},"error":true,"msg":"会员专享"}`` (members only).
  The script reports the message verbatim and exits with code 3, without
  retrying.
- Progress and warnings go to the log; stdout carries data only, so it can be
  redirected.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
import urllib.parse
from datetime import datetime
from typing import Any

from winnie_news.common.http import ApiFatal, fetch
from winnie_news.common.jsonio import (
    add_output_args,
    add_request_args,
    dump_csv,
    dump_json,
    dump_jsonl,
    infer_format,
    output_stream,
)
from winnie_news.common.material import items_from_node_items, make_document
from winnie_news.common.text import decode_json
from winnie_news.common.timeutil import today

logger = logging.getLogger(__name__)

API_URL = "https://tophub.today/node-items-by-date"
PAGE_SIZE_FALLBACK = 30
CSV_FIELDS = [
    "ID",
    "time",
    "title",
    "url",
    "extra",
    "sitename",
    "domain",
    "nodeids",
    "topicid",
    "description",
    "thumbnail",
    "md5",
    "logo",
]


def _parse_page(raw: bytes, charset: str | None) -> dict[str, Any]:
    payload = decode_json(raw, charset)
    if payload.get("status") != 200:
        msg = payload.get("msg") or payload.get("error")
        raise ApiFatal(f"endpoint returned status={payload.get('status')!r} msg={msg!r}")
    return payload


def fetch_page(
    nodeid: str,
    date: str,
    page: int,
    *,
    cookie: str = "",
    referer: str = "https://tophub.today/",
    timeout: float = 20.0,
    retries: int = 3,
) -> dict[str, Any]:
    body = urllib.parse.urlencode({"p": page, "date": date, "nodeid": nodeid}).encode()
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://tophub.today",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie:
        headers["Cookie"] = cookie

    return fetch(
        API_URL,
        method="POST",
        data=body,
        headers=headers,
        timeout=timeout,
        retries=retries,
        parse=_parse_page,
        label=f"page {page}",
    )


def fetch_all(
    nodeid: str,
    date: str,
    *,
    max_pages: int = 200,
    delay: float = 0.5,
    cookie: str = "",
    referer: str = "https://tophub.today/",
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for page in range(1, max_pages + 1):
        payload = fetch_page(
            nodeid, date, page, cookie=cookie, referer=referer, timeout=timeout
        )
        data = payload.get("data") or {}
        page_items = data.get("items") or []
        pagesize = data.get("pagesize") or PAGE_SIZE_FALLBACK
        fresh = 0
        for item in page_items:
            key = item.get("ID")
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            items.append(item)
            fresh += 1
        logger.info(
            "p=%d returned %d items (%d new), %d total",
            page,
            len(page_items),
            fresh,
            len(items),
        )

        if not page_items or len(page_items) < pagesize:
            break
        if page < max_pages and delay:
            time.sleep(delay)
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch one tophub.today node's items for a given date",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --nodeid 6\n"
            "  %(prog)s --nodeid 1 --date 2026-10-20 -o weibo.csv\n"
            "  %(prog)s --nodeid 6 --pages 2 --format jsonl\n"
        ),
    )
    ap.add_argument(
        "--nodeid",
        required=True,
        help="numeric node id, e.g. 1=Weibo hot search, 6=Zhihu hot list",
    )
    ap.add_argument(
        "--date",
        default=None,
        help="date as YYYY-MM-DD (default: today in Asia/Shanghai)",
    )
    ap.add_argument(
        "--pages", type=int, default=0, help="maximum pages to fetch (0=until empty)"
    )
    ap.add_argument(
        "--max-pages", type=int, default=200, help="safety limit (default 200)"
    )
    ap.add_argument(
        "--delay", type=float, default=0.5, help="seconds between pages (default 0.5)"
    )
    ap.add_argument(
        "--cookie",
        default=os.environ.get("TOPHUB_COOKIE", ""),
        help=(
            "optional cookie string; defaults to the TOPHUB_COOKIE environment "
            "variable (may be empty when no login is needed)"
        ),
    )
    ap.add_argument(
        "--referer",
        default="https://tophub.today/",
        help="request Referer (default: the site home page)",
    )
    add_output_args(ap)
    ap.add_argument(
        "--material",
        action="store_true",
        help="emit one document in the winnie.news.material/v1 format",
    )
    add_request_args(ap, timeout=20.0)
    args = ap.parse_args(argv)

    date = args.date or today()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        logger.error("--date must be YYYY-MM-DD, got %r", date)
        return 2

    fmt = args.format or infer_format(args.out)
    max_pages = args.pages if args.pages > 0 else args.max_pages
    logger.info("nodeid=%s date=%s max_pages=%d", args.nodeid, date, max_pages)

    try:
        items = fetch_all(
            args.nodeid,
            date,
            max_pages=max_pages,
            delay=args.delay,
            cookie=args.cookie,
            referer=args.referer,
            timeout=args.timeout,
        )
    except ApiFatal as exc:
        logger.error("%s", exc)
        logger.info(
            "hint: historical dates usually require a subscription; "
            "today's data works without a cookie."
        )
        return 3
    except Exception as exc:
        logger.error("%s", exc)
        return 1

    if not items:
        logger.warning(
            "no items (date outside the available history / wrong nodeid / "
            "no data for today)"
        )

    with output_stream(args.out) as out:
        if args.material:
            document = make_document(
                feed="tophub-node-items",
                generator="winnie_news.material/tophub_node_items.py",
                items=items_from_node_items(items, date=date, nodeid=args.nodeid),
                extra={"nodeid": args.nodeid, "date": date},
            )
            dump_json(document, out, compact=args.compact)
        elif fmt == "jsonl":
            dump_jsonl(items, out)
        elif fmt == "csv":
            dump_csv(items, out, CSV_FIELDS)
        else:
            doc = {
                "nodeid": args.nodeid,
                "date": date,
                "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "count": len(items),
                "items": items,
            }
            dump_json(doc, out, compact=args.compact)

    logger.info("done: %d items", len(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
