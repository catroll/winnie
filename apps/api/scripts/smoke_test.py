#!/usr/bin/env python3
"""冒烟测试：同一把 DeepSeek Key 测两种协议代理。

- deepseek-openai  → POST /deepseek-openai/v1/chat/completions
- deepseek-anthropic → POST /deepseek-anthropic/v1/messages

密钥：scripts/.env 中的 DEEPSEEK_API_KEY。

用法（先启动代理）：
  cd apps/api
  uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
MODEL = "deepseek-v4-flash"


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


def _resolve_key(*names: str, file_vars: dict[str, str]) -> str:
    for name in names:
        if val := os.environ.get(name, "").strip():
            return val
        if val := file_vars.get(name, "").strip():
            return val
    return ""


def _print_result(title: str, ok: bool, detail: str) -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {title}: {detail}")


def test_health(client: httpx.Client, base: str) -> bool:
    try:
        r = client.get(f"{base}/healthz")
        r.raise_for_status()
        data = r.json()
        _print_result("healthz", True, json.dumps(data, ensure_ascii=False))
        return True
    except Exception as exc:  # noqa: BLE001
        _print_result("healthz", False, str(exc))
        return False


def test_deepseek_openai(client: httpx.Client, base: str, api_key: str) -> bool:
    title = "deepseek-openai"
    if not api_key:
        _print_result(title, False, f"缺少 DEEPSEEK_API_KEY（写入 {ENV_FILE.name}）")
        return False
    url = f"{base}/deepseek-openai/v1/chat/completions"
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "只回复一个词：pong"}],
        "max_tokens": 64,
    }
    try:
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        data = r.json()
        if r.status_code >= 400:
            _print_result(title, False, f"HTTP {r.status_code} {data}")
            return False
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        ok = bool(text)
        _print_result(title, ok, f"HTTP {r.status_code} text={text[:120]!r}")
        return ok
    except Exception as exc:  # noqa: BLE001
        _print_result(title, False, str(exc))
        return False


def test_deepseek_anthropic(client: httpx.Client, base: str, api_key: str) -> bool:
    title = "deepseek-anthropic"
    if not api_key:
        _print_result(title, False, f"缺少 DEEPSEEK_API_KEY（写入 {ENV_FILE.name}）")
        return False
    url = f"{base}/deepseek-anthropic/v1/messages"
    body = {
        "model": MODEL,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "只回复一个词：pong"}],
    }
    try:
        r = client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
        )
        data = r.json()
        if r.status_code >= 400:
            _print_result(title, False, f"HTTP {r.status_code} {data}")
            return False
        blocks = data.get("content") or []
        texts: list[str] = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text"):
                texts.append(str(b["text"]))
            # 部分兼容实现可能把思考放在 thinking 块
            if b.get("type") == "thinking" and b.get("thinking"):
                texts.append(str(b["thinking"]))
        text = "".join(texts).strip()
        # 若 content 为空，仍可能有顶层字段
        if not text and isinstance(data.get("content"), str):
            text = data["content"].strip()
        ok = r.status_code == 200 and (
            bool(text) or data.get("type") == "message" or "id" in data
        )
        detail = f"HTTP {r.status_code} text={text[:120]!r}"
        if not text:
            detail += f" raw_keys={list(data.keys())}"
        _print_result(title, ok, detail)
        return ok
    except Exception as exc:  # noqa: BLE001
        _print_result(title, False, str(exc))
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Winnie proxy smoke test (DeepSeek OpenAI + Anthropic)"
    )
    parser.add_argument(
        "--base-url",
        default="http://[::1]:8000",
        help="proxy base URL",
    )
    parser.add_argument(
        "--only",
        choices=("openai", "anthropic", "both"),
        default="both",
        help="只跑某一协议（默认 both）",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    file_vars = _load_env_file(ENV_FILE)
    deepseek_key = _resolve_key("DEEPSEEK_API_KEY", "OPENAI_API_KEY", file_vars=file_vars)

    print(f"base_url={base}")
    print(f"env_file={ENV_FILE} exists={ENV_FILE.is_file()}")
    print(f"model={MODEL}")
    results: list[bool] = []
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        results.append(test_health(client, base))
        if args.only in ("openai", "both"):
            results.append(test_deepseek_openai(client, base, deepseek_key))
        if args.only in ("anthropic", "both"):
            results.append(test_deepseek_anthropic(client, base, deepseek_key))

    failed = sum(1 for x in results if not x)
    print(f"done: {len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
