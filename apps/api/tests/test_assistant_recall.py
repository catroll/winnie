"""The read side of memory: recall, the index fallback, and reindex on write."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import httpx

from winnie_assistant.conversations import ConversationStore
from winnie_assistant.index_client import IndexHit
from winnie_assistant.memory import MemoryStore
from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.tools import ToolBox


class _StubIndex:
    def __init__(self, hits: list[IndexHit] | None = None) -> None:
        self.hits = hits or []
        self.queries: list[str] = []
        self.reindexed: list[str] = []

    async def search(self, query: str, *, limit: int | None = None, **_) -> list[IndexHit]:
        self.queries.append(query)
        return self.hits

    async def reindex(self, path) -> dict[str, object]:
        self.reindexed.append(str(path))
        return {"indexed": {"path": str(path)}}


def _toolbox(
    tmp_path,
    *,
    index: _StubIndex | None = None,
    memory: MemoryStore | None = None,
) -> ToolBox:
    return ToolBox(
        search=FakeSearchProvider(),
        index=index,  # type: ignore[arg-type]
        memory=memory,
        scheduler=None,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        tasks_enabled=False,
    )


async def test_remember_indexes_the_note_immediately(tmp_path) -> None:
    """Five minutes of index lag was the difference between "remembered" and read."""
    index = _StubIndex()
    memory = MemoryStore(tmp_path / "profile", label="profile")
    toolbox = _toolbox(tmp_path, index=index, memory=memory)

    output = await toolbox.execute(
        "remember",
        {"category": "procedures", "text": "没有指定位置时默认查询武汉。"},
        conversation_id="c1",
    )

    assert output == "Remembered."
    assert index.reindexed == [str(tmp_path / "profile" / "procedures.md")]
    assert (tmp_path / "profile" / "procedures.md").is_file()


async def test_remember_survives_a_dead_index(tmp_path) -> None:
    class _DeadIndex(_StubIndex):
        async def reindex(self, path) -> dict[str, object]:
            raise RuntimeError("index is down")

    memory = MemoryStore(tmp_path / "profile", label="profile")
    toolbox = _toolbox(tmp_path, index=_DeadIndex(), memory=memory)

    output = await toolbox.execute(
        "remember", {"category": "people", "text": "孙秀是胡昂的联系人。"}, conversation_id="c"
    )
    assert output == "Remembered."
    assert (tmp_path / "profile" / "people.md").is_file()


async def test_a_miss_says_it_is_not_proof_of_absence(tmp_path) -> None:
    toolbox = _toolbox(tmp_path, index=_StubIndex())

    output = await toolbox.execute(
        "search_documents", {"query": "默认天气位置"}, conversation_id="c"
    )

    assert "not evidence" in output
    assert "could not find it" in output


async def test_a_loose_hit_is_labelled(tmp_path) -> None:
    hit = IndexHit(
        path="Winnie/profile/procedures.md",
        root="Documents/Mine",
        heading="Procedures",
        start_line=9,
        end_line=9,
        snippet="……否则默认查询武汉的天气。",
        score=-12.0,
        mtime="2026-11-01T15:06:20+00:00",
        loose=True,
    )
    toolbox = _toolbox(tmp_path, index=_StubIndex([hit]))

    output = await toolbox.execute(
        "search_documents", {"query": "哪里的天气"}, conversation_id="c"
    )

    assert "keyword" in output
    assert "Winnie/profile/procedures.md:9-9" in output


async def test_the_agent_feeds_recalled_memory_into_the_turn(tmp_path) -> None:
    from winnie_chat import ChatResult

    from winnie_assistant.agent import AssistantAgent

    class _RecordingEngine:
        def __init__(self) -> None:
            self.turns = []

        async def run(self, turn, *, on_tool=None):
            self.turns.append(turn)
            return ChatResult(text="ok")

    memory = MemoryStore(tmp_path / "profile", label="profile")
    memory.append(
        "projects",
        "Winnie 的天气查询在未指定地点时默认查询武汉的天气。",
        source="chat:1",
    )
    engine = _RecordingEngine()
    agent = AssistantAgent(
        store=ConversationStore(tmp_path / "conversations"),
        memory=memory,
        toolbox=_toolbox(tmp_path, memory=memory),
        engine=engine,  # type: ignore[arg-type]
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    reply = await agent.chat("天气默认查哪里？")
    assert reply.text == "ok"
    assert "默认查询武汉" in engine.turns[0].recalled_memory


async def test_the_second_turn_recalls_through_the_conversation(tmp_path) -> None:
    """Every turn searches memory against the exchange, not just the last line."""
    from winnie_chat import ChatResult

    from winnie_assistant.agent import AssistantAgent

    class _Engine:
        def __init__(self) -> None:
            self.turns = []

        async def run(self, turn, *, on_tool=None):
            self.turns.append(turn)
            return ChatResult(text="ok")

    memory = MemoryStore(tmp_path / "profile", label="profile")
    memory.append("people", "图图是胡昂需要接送的孩子。", source="chat:1")
    engine = _Engine()
    agent = AssistantAgent(
        store=ConversationStore(tmp_path / "conversations"),
        memory=memory,
        toolbox=_toolbox(tmp_path, memory=memory),
        engine=engine,  # type: ignore[arg-type]
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    first = await agent.chat("明天带图图去上课是几点？")
    await agent.chat("那呢？", conversation_id=first.conversation_id)

    assert "图图" in engine.turns[0].recalled_memory
    # "那呢？" shares nothing on its own; the exchange supplies the subject.
    assert "图图" in engine.turns[1].recalled_memory


async def test_recall_can_be_disabled_by_its_limit(tmp_path) -> None:
    from winnie_chat import ChatResult

    from winnie_assistant.agent import AssistantAgent

    class _Engine:
        def __init__(self) -> None:
            self.turns = []

        async def run(self, turn, *, on_tool=None):
            self.turns.append(turn)
            return ChatResult(text="ok")

    memory = MemoryStore(tmp_path / "profile", label="profile")
    memory.append("people", "图图是胡昂需要接送的孩子。", source="chat:1")
    engine = _Engine()
    agent = AssistantAgent(
        store=ConversationStore(tmp_path / "conversations"),
        memory=memory,
        toolbox=_toolbox(tmp_path, memory=memory),
        engine=engine,  # type: ignore[arg-type]
        timezone=ZoneInfo("Asia/Shanghai"),
        recall_limit=0,
    )

    await agent.chat("明天带图图去上课是几点？")

    assert engine.turns[0].recalled_memory == ""
