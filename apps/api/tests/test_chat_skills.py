from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from winnie_chat import (
    ChatEngine,
    ChatModelError,
    ChatTurn,
    SkillManifest,
    SkillRegistry,
)
from winnie_llm import ChatResult, ToolCall
from winnie_core.types import Usage


class _FakeSkill:
    def __init__(
        self,
        *,
        name: str = "echo",
        tool: str = "echo",
        instructions: str = "Use echo for tests.",
        available: bool = True,
        scheduled_safe: bool = True,
        toggleable: bool = True,
    ) -> None:
        self.manifest = SkillManifest(
            name=name,
            title=name.title(),
            description=f"{name} test skill",
            instructions=instructions,
            tools=(tool,),
            permissions=("test",),
            scheduled_safe=scheduled_safe,
            toggleable=toggleable,
        )
        self.available = available
        self.unavailable_reason = "" if available else "not configured"
        self.calls: list[dict[str, Any]] = []

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": self.manifest.tools[0],
                    "description": "echo text",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    async def execute(self, tool, arguments, context) -> str:
        self.calls.append({"tool": tool, "arguments": arguments, "context": context})
        return f"echo:{arguments['text']}"


def _turn(
    *, scheduled: bool = False, pending: str = "", modality: str = "text"
) -> ChatTurn:
    return ChatTurn(
        conversation_id="conv",
        user_message="say hi",
        history=[{"role": "user", "content": "say hi"}],
        now=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
        timezone="UTC",
        pending=pending,
        scheduled=scheduled,
        modality=modality,
    )


def _tool_result(name: str, arguments: dict[str, Any]) -> ChatResult:
    encoded = json.dumps(arguments)
    return ChatResult(
        text="",
        tool_calls=(ToolCall(id="call-1", name=name, arguments=encoded),),
        model="scripted",
        usage=Usage(provider="scripted", unit="token", quantity=0),
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": name, "arguments": encoded},
                }
            ],
        },
    )


def _text(value: str) -> ChatResult:
    return ChatResult(
        text=value,
        tool_calls=(),
        model="scripted",
        usage=Usage(provider="scripted", unit="token", quantity=0),
        message={"role": "assistant", "content": value},
    )


class _ScriptedModel:
    name = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return _tool_result("echo", {"text": "hi"})
        return _text("done")


async def test_engine_dispatches_enabled_skill_and_records_trace() -> None:
    skill = _FakeSkill()
    registry = SkillRegistry([skill])
    engine = ChatEngine(registry=registry, model=_ScriptedModel())
    traced: list[tuple[str, str]] = []

    async def on_tool(name: str, output: str) -> None:
        traced.append((name, output))

    result = await engine.run(_turn(), on_tool=on_tool)
    assert result.text == "done"
    assert result.tools_used == ("echo",)
    assert skill.calls and skill.calls[0]["arguments"] == {"text": "hi"}
    assert skill.calls[0]["context"].conversation_id == "conv"
    assert traced == [("echo", "echo:hi")]


async def test_disabled_skill_is_not_offered_or_dispatched() -> None:
    skill = _FakeSkill()
    registry = SkillRegistry([skill])
    registry.set_enabled("echo", False)
    assert registry.schemas() == []
    assert registry.skill_for_tool("echo") is None
    engine = ChatEngine(registry=registry, model=_ScriptedModel())
    traced: list[tuple[str, str]] = []

    async def on_tool(name: str, output: str) -> None:
        traced.append((name, output))

    result = await engine.run(_turn(), on_tool=on_tool)
    assert result.tools_used == ("echo",)
    assert traced and traced[0][1].startswith("ERROR:")
    assert "not enabled" in traced[0][1]
    assert skill.calls == []


async def test_scheduled_turn_excludes_scheduled_unsafe_skills() -> None:
    safe = _FakeSkill(name="safe", tool="safe")
    unsafe = _FakeSkill(name="unsafe", tool="unsafe", scheduled_safe=False)
    registry = SkillRegistry([safe, unsafe])
    scheduled = registry.enabled_skills(scheduled=True)
    assert [skill.manifest.name for skill in scheduled] == ["safe"]
    assert registry.skill_for_tool("unsafe", scheduled=True) is None
    assert [item["function"]["name"] for item in registry.schemas(scheduled=True)] == [
        "safe"
    ]


def test_registry_persists_toggles_and_rejects_bad_names(tmp_path: Path) -> None:
    state = tmp_path / "skills" / "skills.json"
    first = SkillRegistry([_FakeSkill(name="echo")], state_path=state)
    first.set_enabled("echo", False)
    assert state.is_file()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["skills"]["echo"]["enabled"] is False

    second = SkillRegistry([_FakeSkill(name="echo")], state_path=state)
    assert second.views()[0].enabled is False
    second.set_enabled("echo", True)
    assert second.views()[0].enabled is True

    with pytest.raises(KeyError):
        second.set_enabled("missing", True)


def test_registry_rejects_duplicate_tool_names() -> None:
    with pytest.raises(ValueError):
        SkillRegistry([_FakeSkill(name="a", tool="same"), _FakeSkill(name="b", tool="same")])


def test_non_toggleable_and_unavailable_skills() -> None:
    core = _FakeSkill(name="core", tool="", toggleable=False)
    unavailable = _FakeSkill(name="off", tool="off", available=False)
    registry = SkillRegistry([core, unavailable])
    views = {view.name: view for view in registry.views()}
    assert views["core"].enabled and not views["core"].toggleable
    assert views["off"].enabled and not views["off"].available
    assert views["off"].unavailable_reason == "not configured"
    assert registry.enabled_skills() == [core]
    with pytest.raises(ValueError):
        registry.set_enabled("core", False)


async def test_model_error_becomes_chat_model_error() -> None:
    class _Broken:
        name = "broken"

        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    engine = ChatEngine(
        registry=SkillRegistry([_FakeSkill()]), model=_Broken()
    )
    with pytest.raises(ChatModelError, match="provider down"):
        await engine.run(_turn())


async def test_tool_budget_stops_the_loop() -> None:
    class _AlwaysTool:
        name = "loop"

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            return _tool_result("echo", {"text": str(self.calls)})

    model = _AlwaysTool()
    engine = ChatEngine(
        registry=SkillRegistry([_FakeSkill()]),
        model=model,
        max_tool_rounds=2,
    )
    result = await engine.run(_turn())
    assert model.calls == 3
    assert result.tools_used == ("echo", "echo")
    assert "tool-call budget" in result.text


def test_prompt_lists_only_enabled_skills_and_pending() -> None:
    skill = _FakeSkill(instructions="UNIQUE INSTRUCTION")
    registry = SkillRegistry([skill])
    from winnie_chat import build_system_prompt

    prompt = build_system_prompt(
        _turn(pending="run_command abc: run echo"),
        registry.enabled_skills(),
    )
    assert "UNIQUE INSTRUCTION" in prompt
    assert "run_command abc" in prompt
    registry.set_enabled("echo", False)
    prompt = build_system_prompt(_turn(), registry.enabled_skills())
    assert "UNIQUE INSTRUCTION" not in prompt


async def test_skills_api_lists_toggles_and_affects_chat(
    assistant_client: httpx.AsyncClient,
) -> None:
    listing = await assistant_client.get("/api/v1/assistant/skills")
    assert listing.status_code == 200, listing.text
    skills = {item["name"]: item for item in listing.json()["skills"]}
    assert {"core", "web", "documents", "memory", "tasks", "local"} <= set(skills)
    assert skills["core"]["toggleable"] is False
    assert skills["documents"]["available"] is False
    assert skills["local"]["available"] is False
    assert skills["web"]["tools"] == ["web_search", "fetch_page"]
    assert skills["documents"]["tools"] == ["search_documents", "read_document"]
    assert skills["local"]["scheduled_safe"] is False
    assert "instructions" in skills["documents"]
    toggled = await assistant_client.put(
        "/api/v1/assistant/skills/web", json={"enabled": False}
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["enabled"] is False

    core = await assistant_client.put(
        "/api/v1/assistant/skills/core", json={"enabled": False}
    )
    assert core.status_code == 409
    missing = await assistant_client.put(
        "/api/v1/assistant/skills/nope", json={"enabled": False}
    )
    assert missing.status_code == 404

    # The fake model only calls tools it is offered, so with web disabled the
    # chat is a plain reply and no web_search tool block appears.
    chat = await assistant_client.post(
        "/api/v1/assistant/chat", json={"message": "搜索 winnie"}
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["tools_used"] == []
    detail = await assistant_client.get(
        f"/api/v1/assistant/conversations/{chat.json()['conversation_id']}"
    )
    assert not any(
        message["role"] == "tool" for message in detail.json()["messages"]
    )

    enabled = await assistant_client.put(
        "/api/v1/assistant/skills/web", json={"enabled": True}
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    chat = await assistant_client.post(
        "/api/v1/assistant/chat", json={"message": "搜索 winnie"}
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["tools_used"] == ["web_search"]
    detail = await assistant_client.get(
        f"/api/v1/assistant/conversations/{chat.json()['conversation_id']}"
    )
    tool_blocks = [
        message for message in detail.json()["messages"] if message["role"] == "tool"
    ]
    assert tool_blocks and "https://example.com/fake" in tool_blocks[0]["text"]


def test_prompt_puts_recalled_memory_beside_the_pinned_digest() -> None:
    from winnie_chat import build_system_prompt

    prompt = build_system_prompt(
        ChatTurn(
            conversation_id="conv",
            user_message="天气默认查哪里？",
            history=[],
            now=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
            timezone="UTC",
            memory_digest="### procedures\n- 没有指定位置时默认查询武汉。",
            recalled_memory="- 武汉天气的提醒设置（profile/projects.md:4）",
        ),
        [],
    )
    assert "默认查询武汉" in prompt
    assert "profile/projects.md:4" in prompt
    assert "matched by wording" in prompt


def test_prompt_omits_the_recall_section_when_nothing_matched() -> None:
    from winnie_chat import build_system_prompt

    prompt = build_system_prompt(_turn(), [])
    assert "matched by wording" not in prompt


def test_prompt_asks_for_a_spoken_answer_only_on_a_voice_turn() -> None:
    from winnie_chat import build_system_prompt

    typed = build_system_prompt(_turn(), [])
    assert "read aloud" not in typed

    voice = build_system_prompt(_turn(modality="voice"), [])
    assert "This message was dictated and the reply will be read aloud." in voice
    assert "two or three short sentences" in voice
    assert "recognition errors" in voice
