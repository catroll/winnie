"""Translate the host's YAML into the registry's first-start input.

The registry lives in ``winnie-llm``; this host-only helper is the one place
that knows both a ``Settings`` object and the registry's seed shape.
"""

from __future__ import annotations

from winnie_llm import SeedInput, SeedRoute

from winnie_api.settings import Settings


def seed_input(settings: Settings) -> SeedInput:
    return SeedInput(
        routes=tuple(
            SeedRoute(
                name=name,
                protocol=route.protocol,
                base_url=route.base_url,
            )
            for name, route in settings.routes.items()
        ),
        asr=settings.meetings.volcengine,
        bind_asr=settings.meetings.asr_provider == "volcengine",
    )
