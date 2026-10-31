"""Reset a password from the host without the current password.

This is the recovery path for a single-user deployment with no email service::

    uv run python -m winnie_api.cli.reset_password alice
    uv run python -m winnie_api.cli.reset_password --generate alice

All sessions are revoked. The account name is not exposed over HTTP; whoever
can run this command already has access to the database and config.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys

from winnie_core.auth.errors import AuthError
from winnie_api.runtime import build_runtime
from winnie_api.settings import get_settings


def _read_password(generate: bool) -> str:
    if generate:
        return secrets.token_urlsafe(18)
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat new password: ")
    if first != second:
        raise ValueError("passwords do not match")
    return first


async def run(username: str, password: str) -> None:
    runtime = build_runtime(get_settings())
    if runtime is None or runtime.auth_service is None:
        raise RuntimeError("auth.enabled must be true to reset a password")
    await runtime.startup()
    try:
        await runtime.auth_service.reset_password(
            username=username,
            new_password=password,
        )
    finally:
        await runtime.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="generate a random password and print it once",
    )
    args = parser.parse_args(argv)

    try:
        password = _read_password(args.generate)
        asyncio.run(run(args.username, password))
    except (AuthError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("aborted", file=sys.stderr)
        return 1

    print(f"password reset for {args.username}; all sessions revoked")
    if args.generate:
        print(f"new password: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
