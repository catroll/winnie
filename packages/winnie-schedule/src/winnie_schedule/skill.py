"""Install the ``winnie-schedule`` Agent Skill where DSH reads skills.

The skill itself is ``SKILL.md`` at the package root: it documents the tree, the
event grammar and the commands, and it is the file an agent reads before
touching the schedule. This module only copies it to
``~/.agents/skills/winnie-schedule/SKILL.md`` and can report whether the
installed copy still matches.
"""

from __future__ import annotations

import shutil
from pathlib import Path

SKILL_FILE = "SKILL.md"
DEFAULT_TARGET = Path("~/.agents/skills/winnie-schedule")


def source_file() -> Path:
    """Return the packaged skill document."""
    return Path(__file__).resolve().parents[2] / SKILL_FILE


def install(target: Path | str | None = None) -> Path:
    """Copy the skill into ``target`` and return the installed path."""
    destination_dir = Path(target).expanduser() if target else DEFAULT_TARGET.expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / SKILL_FILE
    shutil.copyfile(source_file(), destination)
    destination.chmod(0o644)
    return destination


def check(target: Path | str | None = None) -> tuple[bool, Path]:
    """Return whether the installed copy equals the packaged one."""
    destination_dir = Path(target).expanduser() if target else DEFAULT_TARGET.expanduser()
    destination = destination_dir / SKILL_FILE
    if not destination.is_file():
        return False, destination
    return destination.read_text(encoding="utf-8") == source_file().read_text(encoding="utf-8"), destination
