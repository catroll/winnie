"""Winnie chat engine, skill registry and prompt assembly."""

from winnie_chat.engine import ChatEngine, ChatModel, ChatModelError, parse_arguments
from winnie_chat.models import (
    ChatReply,
    ChatResult,
    ChatTurn,
    Skill,
    SkillContext,
    SkillManifest,
    SkillView,
)
from winnie_chat.prompt import build_system_prompt
from winnie_chat.skill import SkillRegistry

__all__ = [
    "ChatEngine",
    "ChatModel",
    "ChatModelError",
    "ChatReply",
    "ChatResult",
    "ChatTurn",
    "Skill",
    "SkillContext",
    "SkillManifest",
    "SkillRegistry",
    "SkillView",
    "build_system_prompt",
    "parse_arguments",
]
