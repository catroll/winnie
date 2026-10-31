"""Skill registry and its persisted enable/disable state."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from winnie_chat.models import Skill, SkillView

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(
        self,
        skills: list[Skill],
        *,
        state_path: Path | None = None,
    ) -> None:
        self._skills: dict[str, Skill] = {}
        tool_owner: dict[str, str] = {}
        for skill in skills:
            name = skill.manifest.name
            if name in self._skills:
                raise ValueError(f"duplicate skill name: {name}")
            self._skills[name] = skill
            for tool in skill.manifest.tools:
                if tool in tool_owner:
                    raise ValueError(
                        f"tool {tool!r} is claimed by both "
                        f"{tool_owner[tool]!r} and {name!r}"
                    )
                tool_owner[tool] = name
        self.state_path = state_path
        self._enabled = self._load()

    # -- state -----------------------------------------------------------

    def _load(self) -> dict[str, bool]:
        enabled = {name: True for name in self._skills}
        if self.state_path is None or not self.state_path.is_file():
            return enabled
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            stored = data.get("skills", {}) if isinstance(data, dict) else {}
            if isinstance(stored, dict):
                for name, value in stored.items():
                    if name not in enabled or not isinstance(value, dict):
                        continue
                    flag = value.get("enabled")
                    if isinstance(flag, bool):
                        enabled[name] = flag
        except (OSError, ValueError):
            logger.warning("could not read skill state %s", self.state_path)
        return enabled

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "skills": {
                name: {"enabled": self._is_enabled(self._skills[name])}
                for name in self._skills
            },
        }
        fd, temp_name = tempfile.mkstemp(
            dir=self.state_path.parent,
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.state_path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _is_enabled(self, skill: Skill) -> bool:
        if not skill.manifest.toggleable:
            return True
        return self._enabled.get(skill.manifest.name, True)

    # -- views and selection ---------------------------------------------

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def views(self) -> list[SkillView]:
        return [self._view(skill) for skill in self._skills.values()]

    def _view(self, skill: Skill) -> SkillView:
        return SkillView(
            name=skill.manifest.name,
            title=skill.manifest.title,
            description=skill.manifest.description,
            instructions=skill.manifest.instructions,
            tools=skill.manifest.tools,
            permissions=skill.manifest.permissions,
            enabled=self._is_enabled(skill),
            available=skill.available,
            unavailable_reason=skill.unavailable_reason,
            toggleable=skill.manifest.toggleable,
            scheduled_safe=skill.manifest.scheduled_safe,
        )

    def enabled_skills(self, *, scheduled: bool = False) -> list[Skill]:
        return [
            skill
            for skill in self._skills.values()
            if self._is_enabled(skill)
            and skill.available
            and (not scheduled or skill.manifest.scheduled_safe)
        ]

    def schemas(self, *, scheduled: bool = False) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for skill in self.enabled_skills(scheduled=scheduled):
            schemas.extend(skill.schemas())
        return schemas

    def skill_for_tool(
        self, tool: str, *, scheduled: bool = False
    ) -> Skill | None:
        for skill in self.enabled_skills(scheduled=scheduled):
            if tool in skill.manifest.tools:
                return skill
        return None

    def set_enabled(self, name: str, enabled: bool) -> SkillView:
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(name)
        if not skill.manifest.toggleable:
            raise ValueError(f"skill {name!r} is not toggleable")
        self._enabled[name] = enabled
        self._save()
        return self._view(skill)
