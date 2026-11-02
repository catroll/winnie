"""The model names the platform publishes, and where each one is served.

A caller never names a supplier. It names a *platform model* — ``deepseek-flash``
— and the platform decides which supplier endpoint answers and which upstream
model id travels on. That decision lives in the registry (a virtual model: one
name, several accepted spellings, one target) and is copied here as plain data,
so a gateway-only deployment keeps working without importing the registry.

The catalog also owns the two answers the gateway must give before forwarding a
body: "is this name one of ours?" and "which supplier serves it?". Both are pure
lookups; nothing here performs I/O, which is what makes the data plane's
behaviour testable without a registry or a network.

Two shapes of rule, deliberately separate:

* an **alias** is another spelling of the same platform model and always follows
  that model's target;
* an **override** is a per-key redirection of a name to a different supplier
  model. It is policy, not naming, so it lives with the key that holds it — see
  :mod:`winnie_gateway.keystore`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ModelTarget:
    """Where one call should be served: a supplier and its upstream model id."""

    vendor: str
    model: str

    def key(self) -> str:
        return f"{self.vendor}/{self.model}"

    @classmethod
    def parse(cls, value: str) -> "ModelTarget | None":
        """Read ``vendor/model`` as written in a key's override map.

        The vendor cannot contain ``/``, so the first slash is the separator and
        a model id that contains one (``meta-llama/Llama-3-70B``) survives.
        """
        vendor, slash, model = value.strip().partition("/")
        if not slash or not vendor.strip() or not model.strip():
            return None
        return cls(vendor=vendor.strip(), model=model.strip())


@dataclass(frozen=True)
class ModelSpec:
    """One platform model: its published name, its aliases and its target."""

    id: str
    vendor: str
    model: str
    label: str = ""
    aliases: tuple[str, ...] = ()

    def target(self) -> ModelTarget:
        return ModelTarget(vendor=self.vendor, model=self.model)

    def names(self) -> tuple[str, ...]:
        return (self.id, *self.aliases)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label or self.id,
            "aliases": list(self.aliases),
            "vendor": self.vendor,
            "model": self.model,
        }


@dataclass
class ModelCatalog:
    """Every published platform model, indexed by id and by alias."""

    specs: tuple[ModelSpec, ...] = ()
    _index: dict[str, ModelSpec] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._index = {}
        for spec in self.specs:
            for name in spec.names():
                self._index[name] = spec

    @classmethod
    def from_entries(cls, entries: Iterable[Mapping[str, Any]]) -> "ModelCatalog":
        """Build a catalog from registry views (``virtual_models`` rows).

        A row missing a name or a target is skipped rather than raising: the
        gateway must keep serving the routes it can, and the registry already
        validates its own file.
        """
        specs: list[ModelSpec] = []
        for entry in entries:
            name = str(entry.get("id") or "").strip()
            vendor = str(entry.get("vendor") or "").strip()
            model = str(entry.get("model") or "").strip()
            if not name or not vendor or not model:
                continue
            aliases = tuple(
                str(alias).strip()
                for alias in entry.get("aliases") or ()
                if str(alias).strip()
            )
            specs.append(
                ModelSpec(
                    id=name,
                    vendor=vendor,
                    model=model,
                    label=str(entry.get("label") or ""),
                    aliases=aliases,
                )
            )
        return cls(specs=tuple(sorted(specs, key=lambda spec: spec.id)))

    @property
    def empty(self) -> bool:
        return not self.specs

    @property
    def names(self) -> list[str]:
        """Every accepted call name, sorted, for an error message."""
        return sorted(self._index)

    def resolve(self, name: str) -> ModelSpec | None:
        """The platform model a client asked for, by id or by alias."""
        return self._index.get(name.strip())

    def target_for(
        self, name: str, overrides: Mapping[str, str] | None = None
    ) -> ModelTarget | None:
        """The target for one call, applying a key's override when it has one.

        The name must still be a published model: an override changes where a
        name is served, never whether the name exists, so "only configured
        models are callable" holds with or without overrides.
        """
        spec = self.resolve(name)
        if spec is None:
            return None
        for key in (name.strip(), spec.id):
            raw = (overrides or {}).get(key)
            if raw is None:
                continue
            parsed = ModelTarget.parse(str(raw))
            if parsed is not None:
                return parsed
        return spec.target()

    def view(self) -> list[dict[str, Any]]:
        return [spec.view() for spec in self.specs]
