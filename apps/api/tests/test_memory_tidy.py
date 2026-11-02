"""The nightly memory pass: what it merges, what it refuses, and how it proves it.

The pass writes the owner's memory unattended, so these tests are mostly about
the refusals: a merge that drops a detail, a retirement whose quote is not in
the entry, a retirement of yesterday's note, and a rewrite that would not
survive its own verification.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from winnie_assistant.config import MemoryTidyConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.memory import MemoryStore
from winnie_assistant.memory_tidy import MemoryTidy
from winnie_llm import ChatResult
from winnie_core.types import Usage

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 3, 4, 0, tzinfo=TZ)


class ScriptedModel:
    """Answers the merge prompt and the sweep prompt separately."""

    name = "scripted"

    def __init__(
        self,
        *,
        merge: str = '{"distinct": true}',
        retire: str = '{"retire": []}',
        fail: bool = False,
    ) -> None:
        self.merge = merge
        self.retire = retire
        self.fail = fail
        self.prompts: list[list[dict]] = []

    async def chat(self, messages, tools=None) -> ChatResult:
        system = str(messages[0].get("content") or "")
        self.prompts.append(messages)
        if self.fail:
            raise RuntimeError("model unavailable")
        text = self.merge if "Return JSON only, one of" in system else self.retire
        return ChatResult(
            text=text,
            tool_calls=(),
            model="scripted",
            usage=Usage(provider="scripted", unit="token", quantity=0),
            message={"role": "assistant", "content": text},
        )


def profile(root: Path, name: str, category: str, bullets: list[str]) -> Path:
    header = (
        "---\n"
        f"category: {category}\n"
        f"updated: 2026-10-01\n"
        "---\n"
        f"# {category.title()}\n"
    )
    path = root / "profile" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "".join(f"- {line}\n" for line in bullets), encoding="utf-8")
    return path


def build(
    tmp_path: Path, model, *, config: MemoryTidyConfig | None = None
) -> tuple[MemoryTidy, Path, ConversationStore]:
    root = tmp_path / "assistant"
    root.mkdir(parents=True, exist_ok=True)
    store = ConversationStore(root / "conversations")
    tidy = MemoryTidy(
        config=config or MemoryTidyConfig(),
        memory=MemoryStore(root / "profile", label="profile"),
        model=model,
        store=store,
        notifier=None,
        timezone=TZ,
        state_path=root / ".state" / "memory-tidy.json",
        backup_dir=root / ".state" / "memory-backups",
        clock=lambda: NOW,
    )
    return tidy, root, store


def entries(path: Path) -> list[str]:
    from winnie_assistant.memory import parse_memory

    text = path.read_text(encoding="utf-8")
    parsed, _ = parse_memory(text, relpath=f"profile/{path.name}", stem=path.stem)
    return [entry.text for entry in parsed]


WUHAN_COPIES = [
    "[2026-10-20] 用户未指定位置时，天气默认查询武汉。 <!-- source: chat:1 -->",
    "[2026-10-25] 若未指定位置，默认查询武汉的天气。 <!-- source: chat:2 -->",
]


async def test_a_duplicate_pair_becomes_one_note(tmp_path) -> None:
    model = ScriptedModel(
        merge='{"merged": "用户未指定位置时，天气默认查询武汉。"}',
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(root, "procedures", "procedures", WUHAN_COPIES)

    result = await tidy.run_now()

    assert result.status == "ok"
    assert (result.merges, result.retired) == (1, 0)
    assert entries(path) == ["用户未指定位置时，天气默认查询武汉。"]
    text = path.read_text(encoding="utf-8")
    # The merged note keeps the most recent date and says it was tidied.
    assert "[2026-10-25]" in text
    assert "source: tidy:merge:2026-11-03" in text

    # The pre-image is on disk, with the untouched original wording.
    backups = list((root / ".state" / "memory-backups").iterdir())
    assert len(backups) == 1
    assert entries(backups[0] / "procedures.md") == [
        entry.split("] ", 1)[1].split(" <!--")[0] for entry in WUHAN_COPIES
    ]
    plan = json.loads((backups[0] / "plan.json").read_text(encoding="utf-8"))
    assert plan["actions"][0]["kind"] == "merge"
    assert len(plan["actions"][0]["sources"]) == 2


BIRTHDAYS = [
    "[2026-10-30] 琪琪的生日是10月31日，胡昂手机日历中有相关记录。 <!-- source: phone -->",
    "[2026-10-30] 段钊的生日是10月31日，记录在胡昂手机日历的生日日历中。 <!-- source: phone -->",
    "[2026-10-30] 胡珍妮的生日是10月31日，记录在胡昂手机日历的生日日历中。 <!-- source: phone -->",
]


async def test_a_merge_cannot_forget_one_of_the_names(tmp_path) -> None:
    """Three notes that differ only by a name overlap heavily; a similarity
    threshold alone would accept a sentence that names one of them."""
    model = ScriptedModel(
        merge='{"merged": "琪琪的生日是10月31日，记录在胡昂手机日历中。"}',
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(root, "people", "people", BIRTHDAYS)

    result = await tidy.run_now()

    assert result.status == "no_change"
    assert result.rejected == 1
    assert len(entries(path)) == 3


async def test_a_reworded_merge_is_accepted(tmp_path) -> None:
    """Both wordings' content survives, so the sentence may be rewritten."""
    model = ScriptedModel(
        merge='{"merged": "用户未指定位置时默认查询武汉的天气。"}',
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(root, "procedures", "procedures", WUHAN_COPIES)

    result = await tidy.run_now()

    assert result.status == "ok"
    assert entries(path) == ["用户未指定位置时默认查询武汉的天气。"]


async def test_a_merge_cannot_collapse_two_days_into_one(tmp_path) -> None:
    """The same appointment on two days is two occurrences, not one fact: a
    merged note can only carry one date, so the digits must survive."""
    model = ScriptedModel(
        merge=(
            '{"merged": "胡昂在11月2日15:50有「带图图去知了上课」的日程，'
            '地点在光谷金融港。"}'
        ),
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(
        root,
        "episodes",
        "episodes",
        [
            "[2026-11-02] 胡昂在11月2日15:50有「带图图去知了上课」的日程，"
            "地点在光谷金融港。 <!-- source: phone -->",
            "[2026-11-03] 胡昂在11月3日15:50有「带图图去知了上课」的日程，"
            "地点在光谷金融港。 <!-- source: phone -->",
        ],
    )

    result = await tidy.run_now()

    assert result.rejected == 1
    assert len(entries(path)) == 2


async def test_a_merge_that_drops_a_detail_is_refused(tmp_path) -> None:
    model = ScriptedModel(merge='{"merged": "用户在武汉生活。"}')
    tidy, root, _ = build(tmp_path, model)
    path = profile(root, "procedures", "procedures", WUHAN_COPIES)

    result = await tidy.run_now()

    assert result.status == "no_change"
    assert result.rejected == 1
    assert len(entries(path)) == 2
    assert not (root / ".state" / "memory-backups").exists()


async def test_a_retirement_must_quote_the_entry(tmp_path) -> None:
    model = ScriptedModel(
        retire=json.dumps(
            {
                "retire": [
                    {
                        "index": 1,
                        "reason": "test_artifact",
                        "evidence": "this phrase is not in the note",
                    }
                ]
            }
        )
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(
        root,
        "preferences",
        "preferences",
        [
            "[2026-10-01] Fake fact extracted from the conversation. <!-- source: x -->",
            "[2026-10-02] 用户喜欢简短回答。 <!-- source: chat:1 -->",
        ],
    )

    result = await tidy.run_now()

    assert result.status == "no_change"
    assert result.rejected == 1
    assert len(entries(path)) == 2


async def test_a_quoted_test_artifact_is_retired(tmp_path) -> None:
    model = ScriptedModel(
        retire=json.dumps(
            {
                "retire": [
                    {
                        "index": 1,
                        "reason": "test_artifact",
                        "evidence": "Fake fact extracted",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(
        root,
        "preferences",
        "preferences",
        [
            "[2026-10-01] Fake fact extracted from the conversation. <!-- source: x -->",
            "[2026-10-02] 用户喜欢简短回答。 <!-- source: chat:1 -->",
        ],
    )

    result = await tidy.run_now()

    assert result.status == "ok"
    assert result.retired == 1
    assert entries(path) == ["用户喜欢简短回答。"]
    message = (root / ".state" / "memory-tidy.json").read_text(encoding="utf-8")
    assert "memory-backups" in json.loads(message)["last_summary"]


async def test_yesterdays_note_is_never_retired_unattended(tmp_path) -> None:
    model = ScriptedModel(
        retire=json.dumps(
            {
                "retire": [
                    {
                        "index": 1,
                        "reason": "obsolete",
                        "evidence": "Fake fact extracted",
                    }
                ]
            }
        )
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(
        root,
        "preferences",
        "preferences",
        [
            "[2026-11-02] Fake fact extracted from the conversation. <!-- source: x -->",
            "[2026-10-02] 用户喜欢简短回答。 <!-- source: chat:1 -->",
        ],
    )

    result = await tidy.run_now()

    assert result.rejected == 1
    assert len(entries(path)) == 2


async def test_a_duplicate_retirement_must_name_the_survivor(tmp_path) -> None:
    model = ScriptedModel(
        retire=json.dumps(
            {
                "retire": [
                    {
                        "index": 1,
                        "reason": "duplicate",
                        "evidence": "Fake fact extracted",
                    }
                ]
            }
        )
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(
        root,
        "preferences",
        "preferences",
        [
            "[2026-10-01] Fake fact extracted from the conversation. <!-- source: x -->",
            "[2026-10-02] 用户喜欢简短回答。 <!-- source: chat:1 -->",
        ],
    )

    result = await tidy.run_now()

    assert result.rejected == 1
    assert len(entries(path)) == 2


async def test_a_quiet_memory_is_swept_once_and_then_costs_nothing(tmp_path) -> None:
    model = ScriptedModel()
    tidy, root, _ = build(tmp_path, model)
    profile(root, "preferences", "preferences", ["[2026-10-01] 用户喜欢简短回答。"])
    profile(root, "projects", "projects", ["[2026-10-01] 正在做 Winnie 助理。"])

    first = await tidy.run_now()
    assert first.status == "no_change"
    assert len(model.prompts) == 2  # one sweep per file, no merge call

    second = await tidy.run_now()
    assert second.status == "no_change"
    assert len(model.prompts) == 2  # unchanged files are not asked again


async def test_a_distinct_answer_is_not_asked_again(tmp_path) -> None:
    """The model said the pair is two facts; offering it nightly is waste."""
    model = ScriptedModel()  # answers {"distinct": true}
    tidy, root, _ = build(tmp_path, model)
    profile(root, "procedures", "procedures", WUHAN_COPIES)

    await tidy.run_now()
    assert len([p for p in model.prompts if "Return JSON only, one of" in str(p[0]["content"])]) == 1

    await tidy.run_now()
    assert len(model.prompts) == 2  # one merge call and one sweep, then nothing


async def test_a_failed_sweep_stays_pending(tmp_path) -> None:
    model = ScriptedModel(fail=True)
    tidy, root, _ = build(tmp_path, model)
    profile(root, "preferences", "preferences", ["[2026-10-01] 用户喜欢简短回答。"])

    failed = await tidy.run_now()
    assert failed.status == "partial"
    assert "model call" in failed.error

    model.fail = False
    await tidy.run_now()
    assert len(model.prompts) == 2  # the file was asked about again


async def test_a_rewrite_that_loses_a_note_is_never_written(tmp_path, monkeypatch) -> None:
    model = ScriptedModel(
        merge='{"merged": "用户未指定位置时，天气默认查询武汉。"}',
    )
    tidy, root, _ = build(tmp_path, model)
    path = profile(root, "procedures", "procedures", WUHAN_COPIES)
    before = path.read_text(encoding="utf-8")

    # A renderer that silently drops the entries stands in for any future bug
    # in the line surgery: the verification must catch it, not the user.
    monkeypatch.setattr(
        "winnie_assistant.memory_tidy._render",
        lambda state, edits, stamp: (
            "---\ncategory: procedures\n---\n# Procedures\n"
        ),
    )

    result = await tidy.run_now()

    assert result.status == "error"
    assert "would not survive" in result.error
    assert path.read_text(encoding="utf-8") == before
    assert not (root / ".state" / "memory-backups").exists()


async def test_a_merge_moves_the_fact_into_the_pinned_file(tmp_path) -> None:
    model = ScriptedModel(
        merge='{"merged": "打电话给家人时用普通话，语速放慢。"}',
    )
    tidy, root, _ = build(tmp_path, model)
    procedures = profile(
        root,
        "procedures",
        "procedures",
        ["[2026-10-20] 打电话给家人时用普通话，语速放慢。 <!-- source: chat:1 -->"],
    )
    episodes = profile(
        root,
        "episodes",
        "episodes",
        ["[2026-10-30] 打电话给家人用普通话，语速要放慢。 <!-- source: chat:2 -->"],
    )

    result = await tidy.run_now()

    assert result.status == "ok"
    assert entries(procedures) == ["打电话给家人时用普通话，语速放慢。"]
    assert entries(episodes) == []


async def test_a_scheduled_run_leaves_a_notice(tmp_path) -> None:
    model = ScriptedModel(
        merge='{"merged": "用户未指定位置时，天气默认查询武汉。"}',
    )
    tidy, root, store = build(tmp_path, model)
    profile(root, "procedures", "procedures", WUHAN_COPIES)
    await tidy.start()
    try:
        result = await tidy.tick(datetime(2026, 11, 4, 4, 0, tzinfo=TZ))
    finally:
        await tidy.stop()

    assert result is not None
    assert result.conversation_id
    conversation = store.get(result.conversation_id)
    assert conversation.source == "memory"
    assert "记忆整理" in conversation.title
    assert "合并 1 组" in conversation.messages[0].text


async def test_the_tidy_endpoints_report_and_run(assistant_client) -> None:
    status = await assistant_client.get("/api/v1/assistant/memory/tidy")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is True
    assert body["schedule"] == "daily 04:00"
    assert body["last_run"] is None

    run = await assistant_client.post("/api/v1/assistant/memory/tidy")
    assert run.status_code == 200
    result = run.json()
    assert result["status"] == "no_change"
    assert result["merges"] == 0

    after = await assistant_client.get("/api/v1/assistant/memory/tidy")
    assert after.json()["last_status"] == "no_change"


@pytest.mark.parametrize("value", [0.0, 1.5, -1])
def test_similarity_is_bounded(value: float) -> None:
    with pytest.raises(ValueError):
        MemoryTidyConfig(similarity=value)
