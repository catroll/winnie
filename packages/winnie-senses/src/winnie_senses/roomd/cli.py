"""Command line entry point for `winnie-roomd` / `python -m winnie_senses.roomd`."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path

from .api import make_server
from .config import Config
from .service import RoomService


def build_parser() -> argparse.ArgumentParser:
    # Defaults come from Config so a flag and the dataclass cannot drift apart.
    defaults = Config()
    parser = argparse.ArgumentParser(
        prog="winnie-roomd",
        description="Capture the room camera at 3 fps, keeping one video per changed minute.",
    )
    parser.add_argument(
        "--device", default=defaults.device, help="V4L2 device (default: %(default)s)"
    )
    parser.add_argument("--width", type=int, default=defaults.width)
    parser.add_argument("--height", type=int, default=defaults.height)
    parser.add_argument(
        "--target-fps",
        type=float,
        default=defaults.target_fps,
        help="frames evaluated per second",
    )
    parser.add_argument(
        "--change-threshold",
        type=float,
        default=defaults.change_threshold,
        help="mean absolute difference (0-255) that counts as a change",
    )
    parser.add_argument("--out-dir", type=Path, default=defaults.out_dir)
    parser.add_argument("--bind", default=defaults.bind)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument(
        "--duration", type=float, default=0.0, help="stop after N seconds (0 = forever)"
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    config = Config(
        device=args.device,
        width=args.width,
        height=args.height,
        target_fps=args.target_fps,
        change_threshold=args.change_threshold,
        out_dir=args.out_dir,
        bind=args.bind,
        port=args.port,
    )

    service = RoomService(config)
    server = make_server(service, config.bind, config.port)
    # serve_forever runs on its own thread so this thread stays free for
    # signal handling; shutdown() from the serving thread would deadlock.
    threading.Thread(target=server.serve_forever, name="roomd-http", daemon=True).start()
    service.start()

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    if args.duration > 0:
        threading.Timer(args.duration, stop.set).start()

    logging.getLogger("winnie_senses.roomd").info(
        "listening on http://%s:%d", config.bind, config.port
    )
    try:
        stop.wait()
    finally:
        service.stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
