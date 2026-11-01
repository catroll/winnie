#!/usr/bin/env python3
"""Smoke-test the gateway with one Winnie key against both wire protocols.

- ``deepseek-openai``    -> POST /deepseek-openai/v1/chat/completions
- ``deepseek-anthropic`` -> POST /deepseek-anthropic/v1/messages

The caller only needs a Winnie gateway key; the vendor credential is injected by
the server from ``packages/winnie-llm/.env``. The key is read from
``WINNIE_GATEWAY_KEY``, ``scripts/.env``, or an interactive prompt.

Usage (the API must be running):
  cd apps/api
  uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
KEY_NAME = "WINNIE_GATEWAY_KEY"
DEFAULT_MODEL = "deepseek-chat"


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'").strip('"')
        if key and val:
            out[key] = val
    return out


def _gateway_key(file_vars: dict[str, str]) -> str:
    value = os.environ.get(KEY_NAME, "").strip() or file_vars.get(KEY_NAME, "").strip()
    if value:
        return value
    if sys.stdin.isatty():
        return getpass.getpass(f"{KEY_NAME}: ").strip()
    return ""


def _print_result(title: str, ok: bool, detail: str) -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {title}: {detail}")


def test_health(client: httpx.Client, base: str) -> bool:
    try:
        response = client.get(f"{base}/healthz")
        response.raise_for_status()
        _print_result("healthz", True, json.dumps(response.json(), ensure_ascii=False))
        return True
    except Exception as exc:  # noqa: BLE001
        _print_result("healthz", False, str(exc))
        return False


def test_deepseek_openai(
    client: httpx.Client, base: str, key: str, model: str
) -> bool:
    title = "deepseek-openai"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "max_tokens": 64,
    }
    try:
        response = client.post(
            f"{base}/deepseek-openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        data = response.json()
        if response.status_code >= 400:
            _print_result(title, False, f"HTTP {response.status_code} {data}")
            return False
        message = (data.get("choices") or [{}])[0].get("message") or {}
        text = (message.get("content") or message.get("reasoning_content") or "").strip()
        _print_result(title, bool(text), f"HTTP {response.status_code} text={text[:120]!r}")
        return bool(text)
    except Exception as exc:  # noqa: BLE001
        _print_result(title, False, str(exc))
        return False


def test_deepseek_anthropic(
    client: httpx.Client, base: str, key: str, model: str
) -> bool:
    title = "deepseek-anthropic"
    body = {
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
    }
    try:
        response = client.post(
            f"{base}/deepseek-anthropic/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
        )
        data = response.json()
        if response.status_code >= 400:
            _print_result(title, False, f"HTTP {response.status_code} {data}")
            return False
        texts: list[str] = []
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                texts.append(str(block["text"]))
            if block.get("type") == "thinking" and block.get("thinking"):
                texts.append(str(block["thinking"]))
        text = "".join(texts).strip()
        ok = response.status_code == 200 and (
            bool(text) or data.get("type") == "message" or "id" in data
        )
        detail = f"HTTP {response.status_code} text={text[:120]!r}"
        if not text:
            detail += f" raw_keys={list(data.keys())}"
        _print_result(title, ok, detail)
        return ok
    except Exception as exc:  # noqa: BLE001
        _print_result(title, False, str(exc))
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Winnie gateway smoke test (DeepSeek OpenAI + Anthropic)"
    )
    parser.add_argument(
        "--base-url", default="http://[::1]:8000", help="gateway base URL"
    )
    parser.add_argument(
        "--only",
        choices=("openai", "anthropic", "both"),
        default="both",
        help="run one protocol only (default: both)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model id to request")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    key = _gateway_key(_load_env_file(ENV_FILE))

    print(f"base_url={base}")
    print(f"model={args.model}")
    if not key:
        print(
            f"missing {KEY_NAME}: create a key on the Gateway routes page and "
            f"export it, or put it in {ENV_FILE}",
            file=sys.stderr,
        )
        return 1

    results: list[bool] = []
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        results.append(test_health(client, base))
        if args.only in ("openai", "both"):
            results.append(test_deepseek_openai(client, base, key, args.model))
        if args.only in ("anthropic", "both"):
            results.append(test_deepseek_anthropic(client, base, key, args.model))

    failed = sum(1 for ok in results if not ok)
    print(f"done: {len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
