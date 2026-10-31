"""Delete expired bearer sessions.

Run directly with cron or a systemd timer::

    uv run python -m winnie_api.cli.cleanup_sessions

The arq worker also schedules the same operation daily. This entry point is for
deployments that do not run a worker yet.
"""

from __future__ import annotations

import asyncio
import logging

from winnie_api.runtime import build_runtime
from winnie_api.settings import get_settings

logger = logging.getLogger(__name__)


async def run() -> int:
    runtime = build_runtime(get_settings())
    if runtime is None or runtime.auth_service is None:
        raise RuntimeError("auth.enabled must be true to clean up sessions")
    await runtime.startup()
    try:
        return await runtime.auth_service.cleanup_expired_sessions()
    finally:
        await runtime.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    deleted = asyncio.run(run())
    logger.info("deleted %s expired sessions", deleted)


if __name__ == "__main__":
    main()
