"""The only place that reads a vendor API key.

Every model credential belongs to ``packages/winnie-llm/.env``. Callers name a
supplier (``deepseek``, ``volcengine``, ...); this module maps that id to one
environment variable name and returns the value. No capability package and no
host module stores a key, a key variable name or a vendor request header.
"""

from __future__ import annotations

import os
import re
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
}

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


class MissingApiKey(RuntimeError):
    """A supplier has no usable key in the winnie-llm environment."""


def env_name_for(vendor: str) -> str:
    """The variable a supplier's key is read from."""
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


@dataclass(frozen=True)
class ApiKeys:
    """Keys loaded from one environment file, with process overrides."""

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
        # A one-off shell export wins over the file, which keeps CI overrides
        # possible without copying a key into a second managed location.
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
        """The key for a supplier, or ``""`` when it is not configured."""
        return self.values.get(self.env_name(vendor), "").strip()

    def require(self, vendor: str) -> str:
        """The key for a supplier, or a message naming the file to edit."""
        name = self.env_name(vendor)
        value = self.get(vendor)
        if not value:
            raise MissingApiKey(
                f"{name} is not set for supplier {vendor!r}; add it to {self.path}"
            )
        return value

    def present(self, vendor: str) -> bool:
        return bool(self.get(vendor))


def load_keys() -> ApiKeys:
    """Load the package environment with the default path and precedence."""
    return ApiKeys.load()
