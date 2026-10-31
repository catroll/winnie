"""Minimal capability registry used by the host application.

A capability owns one router and an optional URL prefix. The framework mounts
it without knowing the capability's implementation package; the host is the
only place that imports the concrete capabilities and wires their dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True)
class Capability:
    name: str
    router: APIRouter
    prefix: str = ""


class WinnieFramework:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._names: set[str] = set()

    def register(self, capability: Capability) -> None:
        if capability.name in self._names:
            raise ValueError(f"capability already registered: {capability.name}")
        self._names.add(capability.name)
        self.app.include_router(capability.router, prefix=capability.prefix)

    @property
    def capability_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._names))
