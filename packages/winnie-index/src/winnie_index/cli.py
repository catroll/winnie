"""Command line entry point for the local index."""

from __future__ import annotations

import argparse
import errno
import json
import logging
import sys
from pathlib import Path
from typing import Any

from winnie_index import db, httpapi, skill
from winnie_index.config import Config, ConfigError, config_template, load_config
from winnie_index.scan import scan
from winnie_index.search import get_document, search, status


def _config_from(args: argparse.Namespace) -> Config:
    overrides: dict[str, Any] = {}
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = args.data_dir
    if getattr(args, "host", None) or getattr(args, "port", None):
        serve = {}
        if getattr(args, "host", None):
            serve["http"] = f"{args.host}:{args.port or 8790}"
        overrides["serve"] = serve
    return load_config(getattr(args, "config", None), overrides=overrides or None)


def _open(config: Config):
    conn = db.connect(config.db_path)
    db.ensure_schema(conn)
    return conn


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config or "~/.config/winnie-index/config.yaml").expanduser()
    if path.exists() and not args.force:
        print(f"config already exists: {path} (use --force to overwrite)", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config_template(), encoding="utf-8")
    path.chmod(0o600)
    print(path)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = _config_from(args)
    if not config.roots:
        print("no roots configured; add at least one under `roots:` in the config", file=sys.stderr)
        return 2
    conn = _open(config)
    try:
        stats = scan(conn, config, dry_run=args.dry_run, progress=print)
    finally:
        conn.close()
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    for error in stats.errors[:10]:
        print(error, file=sys.stderr)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = _config_from(args)
    conn = _open(config)
    try:
        hits = search(
            conn,
            config,
            args.query,
            roots=args.root or None,
            since=args.since,
            limit=args.limit,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return 0
    if not hits:
        print("no match")
        return 0
    for index, hit in enumerate(hits, start=1):
        heading = f" — {hit['heading']}" if hit["heading"] else ""
        print(f"{index}. {hit['root']}:{hit['path']}:{hit['start_line']}-{hit['end_line']}{heading}")
        print(f"   {hit['snippet']}")
        if hit["also_at"]:
            print(f"   also: {', '.join(hit['also_at'])}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    config = _config_from(args)
    conn = _open(config)
    try:
        document = get_document(
            conn,
            config,
            args.path,
            from_line=args.from_line,
            lines=args.lines,
        )
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0
    print(document["text"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _config_from(args)
    conn = _open(config)
    try:
        data = status(conn, config)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"database: {data['db_path']} ({data['db_bytes'] / 1024:.0f} KB)")
    print(f"last sweep: {data['last_sweep_at'] or 'never'}")
    print(f"documents: {data['documents']}")
    print(f"chunks: {data['chunks']}")
    for root in data["roots"]:
        print(f"  {root['name']}: {root['documents']} documents, {root['chunks']} chunks — {root['path']}")
    for failure in data["failures"]:
        print(f"  failed: {failure['path']}: {failure['error']}", file=sys.stderr)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    config = _config_from(args)
    if not config.roots:
        print(
            "no roots configured yet: serving an empty index. Add roots in the "
            "console or in the config file, then run a build.",
            file=sys.stderr,
        )
        if config.source is None:
            print(
                f"no config file at {Path('~/.config/winnie-index/config.yaml').expanduser()}; "
                "the console will create it on the first save, or run `winnie-index init`.",
                file=sys.stderr,
            )
    try:
        httpapi.serve(config)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"port {config.http_port} is already in use; stop that process or "
                "set serve.http in the config",
                file=sys.stderr,
            )
            return 1
        raise
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    config = _config_from(args)
    if args.stdio:
        httpapi.run_stdio(config)
        return 0
    httpapi.serve(config)
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    config = _config_from(args)
    target = Path(args.target).expanduser() if args.target else None
    if args.action == "check":
        ok, path = skill.check(config, target)
        print(f"{'ok' if ok else 'drift'}: {path}")
        return 0 if ok else 1
    path = skill.install(config, target)
    print(path)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config_from(args)
    problems: list[str] = []
    conn = _open(config)
    try:
        fts = conn.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]
    finally:
        conn.close()
    if not fts:
        problems.append("this SQLite build has no FTS5")
    try:
        b"\xbe\xa9".decode("gb18030")
    except Exception:
        problems.append("the gb18030 codec is unavailable")
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        probe = config.data_dir / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        problems.append(f"data dir not writable: {exc}")
    for root in config.roots:
        if not root.path.is_dir():
            problems.append(f"root {root.name}: not a directory: {root.path}")
    ok, path = skill.check(config)
    if not ok:
        problems.append(f"skill missing or drifted: {path}")
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print("ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="winnie-index", description="Local text and Markdown index")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--data-dir", help="override data_dir")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write an annotated config file")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    for name in ("scan", "build"):
        p = sub.add_parser(name, help="sweep roots and index what changed")
        p.add_argument("--dry-run", action="store_true", help="report only; write nothing")
        p.set_defaults(func=cmd_scan)

    p = sub.add_parser("search", help="search the index")
    p.add_argument("query")
    p.add_argument("--root", action="append", help="restrict to a root name")
    p.add_argument("--since", help="7d, 24h, 2w or an ISO date")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", help="read a document, revalidated against the file")
    p.add_argument("path")
    p.add_argument("--from", dest="from_line", type=int, default=1)
    p.add_argument("--lines", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("status", help="show coverage and failures")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("serve", help="run the daemon: sweep plus HTTP and MCP")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="serve MCP over stdio or HTTP")
    p.add_argument("--stdio", action="store_true", help="stdio transport instead of HTTP")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("skill", help="install or check the Agent Skill")
    p.add_argument("action", choices=["install", "check"])
    p.add_argument("--target")
    p.set_defaults(func=cmd_skill)

    p = sub.add_parser("doctor", help="check the environment")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
