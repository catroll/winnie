"""Delete meeting audio past the configured retention window.

Run directly with cron or a systemd timer::

    uv run python -m winnie_api.cli.cleanup_audio

Transcripts and minutes are kept. The arq worker also schedules this daily.
"""

from __future__ import annotations

import asyncio
import logging

from winnie_api.runtime import build_runtime
from winnie_api.settings import get_settings

logger = logging.getLogger(__name__)


async def run() -> int:
    runtime = build_runtime(get_settings())
    if runtime is None or runtime.meeting_service is None:
        raise RuntimeError("meetings.enabled must be true to clean up audio")
    await runtime.startup()
    try:
        return await runtime.meeting_service.purge_expired_audio()
    finally:
        await runtime.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    purged = asyncio.run(run())
    logger.info("purged audio for %s meetings", purged)


if __name__ == "__main__":
    main()
