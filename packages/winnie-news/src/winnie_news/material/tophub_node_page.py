"""Parse a tophub node page (the Zhihu hot list by default) and reconcile it.

Page:    GET https://tophub.today/n/mproPpoq6O  (server-rendered HTML, no cookie)
Endpoint: POST https://tophub.today/node-items-by-date (see tophub_node_items.py)

Each page row looks like::

    <tr>
      <td align="center">1.</td>
      <td class="al" align="center"><img ...></td>
      <td class="al">
        <div><a href="..." itemid="251080486">Title</a></div>
        <div class="item-desc">11.23 million heat</div>
      </td>
      ...
    </tr>

Reconciliation: ``itemid`` (the endpoint's ``ID``) is the primary key. The page
shows the current top 50, while the endpoint lists every entry that ranked that
day, so normally the page is a subset of the endpoint; the extra endpoint rows
are entries that ranked earlier and have since dropped out of the top 50. Only
title / url / heat text are compared.

Examples::

    # Parse and reconcile by default (nodeid is read from the page)
    python -m winnie_news.material.tophub_node_page

    # Page data only, without calling the endpoint
    python -m winnie_news.material.tophub_node_page --no-compare

    # JSON output (items and comparison)
    python -m winnie_news.material.tophub_node_page --format json -o zhihu.json

    # Shared material format (winnie.news.material/v1, no reconciliation call)
    python -m winnie_news.material.tophub_node_page --material \
        -o zhihu.material.json
"""

from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime
from html import unescape

from winnie_news.common.http import ApiFatal, fetch
from winnie_news.common.jsonio import (
    add_request_args,
    dump_json,
    dump_jsonl,
    output_stream,
)
from winnie_news.common.material import items_from_node_page, make_document
from winnie_news.common.text import decode_text, strip_tags
from winnie_news.common.timeutil import today

from .tophub_node_items import fetch_all

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://tophub.today/n/mproPpoq6O"

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
RANK_RE = re.compile(r'<td[^>]*align="center"[^>]*>\s*(\d+)\.\s*</td>')
LINK_RE = re.compile(
    r'<a\s+href="([^"]+)"[^>]*?itemid="(\d+)"[^>]*>(.*?)</a>', re.S
)
DESC_RE = re.compile(r'<div class="item-desc">(.*?)</div>', re.S)
NODEID_RE = re.compile(r'nodeid="(\d+)"')
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


def fetch_html(url: str, *, timeout: float = 20.0, retries: int = 3) -> str:
    return fetch(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        },
        timeout=timeout,
        retries=retries,
        parse=decode_text,
        label="page",
    )


def parse_items(html: str, limit: int = 0) -> list[dict]:
    items: list[dict] = []
    for row in ROW_RE.findall(html):
        rank_m = RANK_RE.search(row)
        link_m = LINK_RE.search(row)
        if not (rank_m and link_m):
            continue
        desc_m = DESC_RE.search(row)
        items.append(
            {
                "rank": int(rank_m.group(1)),
                "id": link_m.group(2),
                "title": strip_tags(link_m.group(3)),
                "url": unescape(link_m.group(1)),
                "heat": strip_tags(desc_m.group(1)) if desc_m else "",
            }
        )
    items.sort(key=lambda x: x["rank"])
    if limit > 0:
        items = items[:limit]
    return items


def compare(page_items: list[dict], api_items: list[dict]) -> dict:
    api_by_id = {str(i.get("ID")): i for i in api_items}
    page_by_id = {str(i["id"]): i for i in page_items}
    common = [pid for pid in page_by_id if pid in api_by_id]
    page_only = [page_by_id[pid] for pid in page_by_id if pid not in api_by_id]
    api_only = [api_by_id[pid] for pid in api_by_id if pid not in page_by_id]

    title_ok = url_ok = heat_ok = 0
    heat_diffs = []
    for pid in common:
        p, a = page_by_id[pid], api_by_id[pid]
        if p["title"] == a.get("title"):
            title_ok += 1
        if p["url"] == a.get("url"):
            url_ok += 1
        if p["heat"] == a.get("extra"):
            heat_ok += 1
        elif len(heat_diffs) < 3:
            heat_diffs.append(
                {
                    "id": pid,
                    "title": p["title"][:40],
                    "page": p["heat"],
                    "api": a.get("extra"),
                }
            )

    page_count = len(page_items)
    return {
        "page_count": page_count,
        "api_count": len(api_items),
        "overlap": len(common),
        "overlap_rate": round(len(common) / page_count * 100, 1) if page_count else 0.0,
        "page_only": [
            {"rank": p["rank"], "id": p["id"], "title": p["title"], "heat": p["heat"]}
            for p in page_only
        ],
        "api_only_count": len(api_only),
        "field_match": {
            "title": title_ok,
            "url": url_ok,
            "heat": heat_ok,
            "common": len(common),
        },
        "heat_diff_samples": heat_diffs,
    }


def print_table(items: list[dict], comparison: dict | None, meta: dict) -> None:
    print(f"# {meta['page_title']}  nodeid={meta['nodeid']}  {len(items)} items")
    print()
    print(f"{'#':>3}  {'itemid':<12} {'heat':<12} title")
    for it in items:
        print(f"{it['rank']:>3}  {it['id']:<12} {it['heat']:<12} {it['title']}")
    if comparison is None:
        return
    c = comparison
    print()
    print(f"## Reconcile with node-items-by-date (nodeid={meta['nodeid']}, date={meta['date']})")
    print(
        f"- page {c['page_count']} items / api {c['api_count']} items / "
        f"overlap {c['overlap']} ({c['overlap_rate']}%)"
    )
    if c["page_only"]:
        print(f"- page only: {len(c['page_only'])} items missing from the api list:")
        for p in c["page_only"]:
            print(f"    rank {p['rank']} / {p['id']} / {p['heat']} / {p['title'][:50]}")
    else:
        print("- page only: 0 items")
    print(
        f"- api only: {c['api_only_count']} items (older entries that dropped off "
        "the page; expected)"
    )
    fm = c["field_match"]
    print(
        f"- shared fields agree: title {fm['title']}/{fm['common']}, "
        f"url {fm['url']}/{fm['common']}, "
        f"heat {fm['heat']}/{fm['common']} "
        "(both fetched at one moment; heat changes between fetches)"
    )
    for d in c["heat_diff_samples"]:
        print(f"    heat differs: {d['id']} page {d['page']} / api {d['api']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Parse a tophub node page (the Zhihu hot list by default) and "
            "reconcile it with node-items-by-date"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --no-compare\n"
            "  %(prog)s --format json -o zhihu.json\n"
        ),
    )
    ap.add_argument(
        "--url", default=DEFAULT_URL, help=f"node page URL (default {DEFAULT_URL})"
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=50,
        help="keep at most the first N items (0=all, default 50)",
    )
    ap.add_argument(
        "--nodeid",
        default=None,
        help='endpoint nodeid for reconciliation (default: from nodeid="N" on the page)',
    )
    ap.add_argument(
        "--date",
        default=None,
        help="reconciliation date as YYYY-MM-DD (default: today in Asia/Shanghai)",
    )
    ap.add_argument(
        "--compare",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="whether to reconcile with node-items-by-date (default: yes)",
    )
    add_request_args(ap, timeout=20.0)
    ap.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="seconds between endpoint pages (default 0.5)",
    )
    ap.add_argument(
        "--format",
        choices=("table", "json", "jsonl"),
        default="table",
        help="output format (default table)",
    )
    ap.add_argument(
        "--material",
        action="store_true",
        help="emit one document in the winnie.news.material/v1 format",
    )
    ap.add_argument(
        "-o", "--out", help="output file (default stdout; ignored by the table format)"
    )
    args = ap.parse_args(argv)

    try:
        html = fetch_html(args.url, timeout=args.timeout)
    except Exception as exc:
        logger.error("%s", exc)
        return 1

    title_m = TITLE_RE.search(html)
    nodeid_m = NODEID_RE.search(html)
    page_items = parse_items(html, limit=args.limit)
    if not page_items:
        logger.error("parsed no items from the page; the HTML structure may have changed")
        return 1

    meta = {
        "url": args.url,
        "page_title": strip_tags(title_m.group(1)) if title_m else "",
        "nodeid": args.nodeid or (nodeid_m.group(1) if nodeid_m else ""),
        "date": args.date or today(),
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    comparison = None
    # --material emits material only and carries no comparison, so skip the API.
    if args.compare and not args.material and meta["nodeid"]:
        try:
            api_items = fetch_all(
                meta["nodeid"], meta["date"], delay=args.delay, timeout=args.timeout
            )
            comparison = compare(page_items, api_items)
        except ApiFatal as exc:
            logger.warning("skipping reconciliation: %s", exc)
        except Exception as exc:
            logger.warning("reconciliation failed: %s", exc)
    elif args.compare and not args.material:
        logger.warning(
            "no nodeid found on the page; skipping reconciliation "
            "(pass --nodeid to set one)"
        )

    if args.material:
        source = meta["page_title"].split(" - ", 1)[0].strip() or "tophub.today"
        document = make_document(
            feed="tophub-node-page",
            generator="winnie_news.material/tophub_node_page.py",
            feed_url=meta["url"],
            items=items_from_node_page(
                page_items, source=source, nodeid=meta["nodeid"]
            ),
            extra={
                "nodeid": meta["nodeid"],
                "page_title": meta["page_title"],
                "date": meta["date"],
            },
        )
        with output_stream(args.out) as out:
            dump_json(document, out)
        return 0

    if args.format == "table":
        print_table(page_items, comparison, meta)
        return 0

    doc = {
        "page_url": meta["url"],
        "page_title": meta["page_title"],
        "nodeid": meta["nodeid"],
        "date": meta["date"],
        "fetched_at": meta["fetched_at"],
        "count": len(page_items),
        "items": page_items,
        "comparison": comparison,
    }
    with output_stream(args.out) as out:
        if args.format == "jsonl":
            dump_jsonl(page_items, out)
        else:
            dump_json(doc, out)

    if comparison and args.out:
        logger.info(
            "[ok] page %d items, overlap %d (%.1f%%), page-only %d",
            comparison["page_count"],
            comparison["overlap"],
            comparison["overlap_rate"],
            len(comparison["page_only"]),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
