"""Blocking HTTP fetch with bounded retries, for the material collectors.

The collectors run inside a worker thread (the API is async but these sources
are simple GETs), so this module uses ``urllib`` rather than the shared async
client. Retries cover transport errors and unparseable bodies alike: a CDN that
answers 200 with an error page is the same failure as a connection reset, and
the callback is the only place that can tell.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"


class FetchError(RuntimeError):
    """The request or its parsing failed and the retries are exhausted."""


class ApiFatal(Exception):
    """The endpoint refused explicitly (for example it needs a subscription)."""


def fetch(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    retries: int = 3,
    parse: Callable[[bytes, str | None], Any] | None = None,
    label: str = "",
) -> Any:
    """Fetch ``url``, retrying transport errors and parse failures.

    ``parse(raw, charset)`` turns the body into the wanted value. Raising
    :class:`ApiFatal` from it aborts immediately: some endpoints answer with a
    message that says retrying is pointless, and a retry would only add delay.
    """
    merged_headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": ACCEPT_LANGUAGE,
        **(headers or {}),
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, data=data, headers=merged_headers, method=method
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                charset = (
                    response.headers.get_content_charset()
                    if response.headers
                    else None
                )
            if not raw:
                raise ValueError("empty response")
            return parse(raw, charset) if parse else raw
        except ApiFatal:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                wait = 1.5**attempt
                logger.warning(
                    "%s attempt %d failed: %s; retrying in %.1fs",
                    label or url,
                    attempt,
                    exc,
                    wait,
                )
                time.sleep(wait)

    if label:
        raise FetchError(f"{label} fetch failed: {last_error}")
    raise FetchError(f"fetch failed: {url} ({last_error})")
