"""The env file that mirrors supplier credentials.

The registry (``data/ai.yaml``) owns every supplier key: a key is stored on the
supplier record next to its model catalog, and an internal model call reads it
from there. This module owns the *backup* copy of those keys,
``packages/winnie-llm/.env``, and nothing else reads a key from the process
environment.

The console writes one line per supplier into the backup whenever an
administrator saves a key, so the file still holds a working copy for a human
to inspect. It is never the source of truth: a change made only to the file is
ignored by the registry. The one exception is the first start after upgrading
from a registry that stored no keys, which imports the backup once; see
:class:`winnie_llm.registry.store.ProviderRegistry`.

A gateway-only deployment (``ai.enabled: false``) has no registry, so the route
table falls back to reading this file. That is the only read path left.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ``src/winnie_llm/keys.py`` -> ``packages/winnie-llm/.env``
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PACKAGE_ROOT / ".env"
ENV_FILE_VARIABLE = "WINNIE_LLM_ENV_FILE"

# The conventional variable for each supplier the console knows about. A
# supplier that is not listed maps to ``<SUPPLIER>_API_KEY`` so a second account
# for the same vendor (``deepseek-work``) still has an obvious name.
VENDOR_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "volcengine": "VOLCENGINE_ASR_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
}

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


class MissingApiKey(RuntimeError):
    """A supplier has no usable key in the env backup file."""


def env_name_for(vendor: str) -> str:
    """The variable a supplier's key is mirrored into."""
    known = VENDOR_KEY_ENV.get(vendor.strip().lower())
    if known:
        return known
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", vendor.strip()).strip("_").upper()
    return f"{cleaned or 'LLM'}_API_KEY"


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a small ``.env`` file.

    Supports blank lines, ``#`` comments, an optional ``export`` prefix and
    single- or double-quoted values. It deliberately does not expand variables:
    a key containing ``$`` must survive untouched.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:  # pragma: no cover - unreadable path is a host issue
        raise MissingApiKey(f"cannot read the winnie-llm environment at {path}: {exc}") from exc

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(raw)
        if match is None:
            continue
        name, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            # An unquoted trailing comment is not part of the value.
            comment = value.find(" #")
            if comment >= 0:
                value = value[:comment]
        values[name] = value.strip()
    return values


def _render_value(value: str) -> str:
    """A value as it can be pasted into a ``.env`` line.

    API keys have no spaces, so the common case is written bare. A value that
    contains whitespace or starts a comment is quoted, because the reader
    trims and stops at `` #``.
    """
    if not value:
        return ""
    if any(character.isspace() for character in value) or " #" in value:
        if '"' in value:
            raise ValueError("an API key cannot contain both whitespace and a quote")
        return f'"{value}"'
    return value


def write_env_value(path: Path, name: str, value: str) -> None:
    """Upsert one variable in the env backup, preserving every other line.

    An existing assignment is replaced in place, including an ``export``
    prefix; a new one is appended. Comments, ordering and unrelated variables
    are left alone, so an operator's annotated file survives a console save.
    The write is atomic and the file is mode ``0600`` because it holds
    credentials.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    lines = text.splitlines()
    rendered = f"{name}={_render_value(value)}"
    for index, raw in enumerate(lines):
        match = _LINE.match(raw)
        if match is None or match.group(1) != name:
            continue
        indent = raw[: len(raw) - len(raw.lstrip())]
        prefix = "export " if raw.lstrip().startswith("export ") else ""
        lines[index] = f"{indent}{prefix}{rendered}"
        break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(rendered)

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write("\n".join(lines).rstrip("\n") + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ApiKeys:
    """The env backup, loaded once, with process overrides for one-off runs."""

    values: dict[str, str]
    path: Path

    @classmethod
    def load(
        cls,
        path: Path | str | None = None,
        *,
        environ: dict[str, str] | None = None,
    ) -> ApiKeys:
        env = os.environ if environ is None else environ
        resolved = Path(
            path or env.get(ENV_FILE_VARIABLE, "").strip() or DEFAULT_ENV_PATH
        ).expanduser()
        values = parse_env_file(resolved)
        # A one-off shell export still wins for a gateway-only deployment,
        # which is the remaining reader. The registry ignores it.
        values.update(
            {
                name: value
                for name, value in env.items()
                if value and (name.endswith("_API_KEY") or name in VENDOR_KEY_ENV.values())
            }
        )
        return cls(values=values, path=resolved)

    def env_name(self, vendor: str) -> str:
        return env_name_for(vendor)

    def get(self, vendor: str) -> str:
        """The backup value for a supplier, or ``""`` when it is not set."""
        return self.values.get(self.env_name(vendor), "").strip()

    def require(self, vendor: str) -> str:
        """The backup value, or a message naming the file that holds it."""
        name = self.env_name(vendor)
        value = self.get(vendor)
        if not value:
            raise MissingApiKey(
                f"{name} is not set for supplier {vendor!r}; add it to {self.path}"
            )
        return value

    def present(self, vendor: str) -> bool:
        return bool(self.get(vendor))

    def set(self, vendor: str, value: str) -> None:
        """Mirror one supplier key into the file and into this snapshot.

        Called after the registry has accepted the key. The write is what makes
        the backup true; nothing here can fail the registry save beyond an I/O
        error, which the caller logs.
        """
        name = self.env_name(vendor)
        cleaned = value.strip()
        write_env_value(self.path, name, cleaned)
        self.values[name] = cleaned


def load_keys() -> ApiKeys:
    """Load the backup with the default path and precedence."""
    return ApiKeys.load()
