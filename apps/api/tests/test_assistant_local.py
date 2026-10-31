from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_chat import (
    ChatEngine,
    ChatTurn,
    SkillManifest,
    SkillRegistry,
    build_system_prompt,
)

from winnie_assistant.agent import AssistantAgent
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.skills import LOCAL, ToolboxSkill
from winnie_local import (
    AuditLog,
    CommandRunner,
    LLMReviewer,
    LocalExecutor,
    LocalToolError,
    PendingActions,
    ReviewDecision,
    Whitelist,
    Workspace,
    is_confirmation,
)
from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.tools import ToolBox
from winnie_core.llm.base import ChatResult, ToolCall
from winnie_core.types import Usage


def _workspace(tmp_path: Path, **kwargs) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    secret = root / "secret"
    secret.mkdir(exist_ok=True)
    return Workspace(
        root=root,
        protected=[secret],
        audit=AuditLog(tmp_path / "state" / "files.jsonl"),
        backup_dir=tmp_path / "state" / "backups",
        **kwargs,
    )


def _runner(tmp_path: Path, *, allow: list[str] | None = None) -> CommandRunner:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return CommandRunner(
        roots=[root],
        workdir=root,
        timeout_seconds=2.0,
        max_output_chars=1_000,
        allow=allow,
        audit=AuditLog(tmp_path / "state" / "exec.jsonl"),
    )


class _AllowReviewer:
    def __init__(self) -> None:
        self.calls = 0
        self.last_prompt = ""

    async def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        self.last_prompt = user
        return '{"allow": true, "risk": "low", "reason": "test action"}'


def _engine(toolbox: ToolBox, model) -> ChatEngine:
    manifest = SkillManifest(
        name="local",
        title="Local files and commands",
        description="test",
        instructions=LOCAL.instructions,
        tools=(
            "list_files",
            "read_file",
            "write_file",
            "run_command",
            "confirm_action",
        ),
        scheduled_safe=False,
    )
    registry = SkillRegistry(
        [ToolboxSkill(manifest, toolbox, allow_local=True)],
        state_path=None,
    )
    return ChatEngine(
        registry=registry,
        model=model,
        max_tool_rounds=4,
        max_tool_output_chars=4_000,
    )


def _local(
    tmp_path: Path,
    *,
    workspace: Workspace | None = None,
    commands: CommandRunner | None = None,
    reviewer: _AllowReviewer | None = None,
    review_enabled: bool = True,
    whitelist: Whitelist | None = None,
) -> LocalExecutor:
    allow = reviewer or _AllowReviewer()
    return LocalExecutor(
        workspace=workspace,
        commands=commands,
        reviewer=LLMReviewer(
            complete=allow,
            model="test-reviewer",
            timeout_seconds=5.0,
            max_action_chars=4_000,
        ),
        review_enabled=review_enabled,
        review_audit=AuditLog(tmp_path / "state" / "review.jsonl"),
        whitelist=whitelist
        or Whitelist(
            operations=["list_files", "read_file"],
            commands=["pwd", "ls", "cat", "head", "tail", "wc"],
            git_subcommands=["status", "rev-parse", "ls-files"],
        ),
        pending=PendingActions(ttl_seconds=300),
        model_name="test",
        max_review_detail_chars=4_000,
    )


def _toolbox(
    tmp_path: Path,
    *,
    workspace: Workspace | None = None,
    commands: CommandRunner | None = None,
    reviewer: _AllowReviewer | None = None,
    review_enabled: bool = True,
    whitelist: Whitelist | None = None,
) -> ToolBox:
    local = None
    if workspace is not None or commands is not None:
        local = _local(
            tmp_path,
            workspace=workspace,
            commands=commands,
            reviewer=reviewer,
            review_enabled=review_enabled,
            whitelist=whitelist,
        )
    return ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
        local=local,
    )


def _sha(read_output: str) -> str:
    match = re.search(r"^sha256: ([0-9a-f]{64})$", read_output, re.MULTILINE)
    assert match, read_output
    return match.group(1)


def _pending_id(output: str) -> str:
    match = re.search(r"PENDING ([0-9a-f]+):", output)
    assert match, output
    return match.group(1)


def test_workspace_create_read_overwrite_and_backup(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.root / "notes").mkdir()
    plan = workspace.plan_write("notes/todo.md", "hello\n", mode="create")
    assert not plan.requires_confirmation
    assert "Wrote" in workspace.write(plan, "conv-1")

    read = workspace.read("notes/todo.md")
    assert "hello" in read
    digest = _sha(read)

    with pytest.raises(LocalToolError, match="expected_sha256"):
        workspace.plan_write("notes/todo.md", "new\n", mode="overwrite")
    with pytest.raises(LocalToolError, match="file changed"):
        workspace.plan_write(
            "notes/todo.md", "new\n", mode="overwrite", expected_sha256="0" * 64
        )

    overwrite = workspace.plan_write(
        "notes/todo.md", "new\n", mode="overwrite", expected_sha256=digest
    )
    assert overwrite.requires_confirmation
    workspace.write(overwrite, "conv-1")
    assert (workspace.root / "notes/todo.md").read_text(encoding="utf-8") == "new\n"

    backups = [
        path
        for path in (tmp_path / "state" / "backups").rglob("*")
        if path.is_file()
    ]
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "hello\n"
    audit = (tmp_path / "state" / "files.jsonl").read_text(encoding="utf-8")
    assert '"mode": "create"' in audit
    assert '"mode": "overwrite"' in audit


def test_workspace_rejects_escapes_protected_and_credentials(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(LocalToolError, match="outside the configured roots"):
        workspace.read("../outside.txt")
    with pytest.raises(LocalToolError, match="protected"):
        workspace.read("secret/nothing")
    link = workspace.root / "link"
    link.symlink_to(outside)
    with pytest.raises(LocalToolError, match="outside the configured roots"):
        workspace.read("link")
    (workspace.root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    with pytest.raises(LocalToolError, match="credential"):
        workspace.read(".env")
    (workspace.root / ".git").mkdir()
    with pytest.raises(LocalToolError, match=".git"):
        workspace.plan_write(".git/config", "x", mode="create")
    listing = workspace.list(".")
    assert "[credential] .env" in listing
    assert "[protected] secret" in listing


def test_workspace_rejects_binary_and_oversized_files(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, max_read_bytes=16)
    (workspace.root / "binary").write_bytes(b"a\x00b")
    with pytest.raises(LocalToolError, match="binary"):
        workspace.read("binary")
    (workspace.root / "large").write_text("x" * 32, encoding="utf-8")
    with pytest.raises(LocalToolError, match="over the 16 byte limit"):
        workspace.read("large")


async def test_command_runner_runs_times_out_and_audits(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    plan = runner.plan(
        [sys.executable, "-c", "print('hello')"], ""
    )
    assert "hello" in await runner.run(plan, "conv-1")

    timeout = runner.plan(
        [sys.executable, "-c", "import time; time.sleep(5)"], ""
    )
    runner.timeout_seconds = 0.2
    output = await runner.run(timeout, "conv-1")
    assert "exit: -9 (timed out" in output

    audit = (tmp_path / "state" / "exec.jsonl").read_text(encoding="utf-8")
    assert '"kind": "command"' in audit
    assert '"timed_out": true' in audit

    with pytest.raises(LocalToolError, match="outside the configured roots"):
        runner.plan([sys.executable, "-c", "pass"], "..")


async def test_command_runner_respects_allow_and_scrubs_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner(tmp_path, allow=["echo"])
    with pytest.raises(LocalToolError, match="not in exec.allow"):
        runner.plan([sys.executable, "-c", "pass"])

    monkeypatch.setenv("WINNIE_TEST_SECRET", "super-secret")
    # The allowlist is empty here so the interpreter can run.
    unrestricted = _runner(tmp_path)
    plan = unrestricted.plan(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('WINNIE_TEST_SECRET'))",
        ],
        "",
    )
    output = await unrestricted.run(plan, "conv-1")
    assert "super-secret" not in output
    assert "None" in output


async def test_pending_action_needs_a_later_confirmed_turn() -> None:
    pending = PendingActions(ttl_seconds=300)
    called: list[str] = []

    async def action() -> str:
        called.append("ran")
        return "done"

    pending.begin_turn("c", "please run it")
    created = pending.put(
        "c", kind="run_command", summary="run echo hi", execute=action
    )
    with pytest.raises(LocalToolError, match="created in this turn"):
        pending.take("c", created.token)

    pending.begin_turn("c", "what will it do?")
    with pytest.raises(LocalToolError, match="no pending action"):
        pending.take("c", created.token)

    pending.begin_turn("c", "please run it")
    created = pending.put(
        "c", kind="run_command", summary="run echo hi", execute=action
    )
    pending.begin_turn("c", "确认")
    taken = pending.take("c", created.token)
    assert await taken.execute() == "done"
    assert called == ["ran"]


async def test_toolbox_confirmation_flow(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    commands = _runner(tmp_path)
    toolbox = _toolbox(tmp_path, workspace=workspace, commands=commands)
    (workspace.root / "notes").mkdir()
    toolbox.begin_turn("c", "create a note")
    created = await toolbox.execute(
        "write_file",
        {"path": "notes/todo.md", "content": "hello\n", "mode": "create"},
        conversation_id="c",
    )
    assert "Wrote" in created
    read = await toolbox.execute(
        "read_file", {"path": "notes/todo.md"}, conversation_id="c"
    )
    digest = _sha(read)

    toolbox.begin_turn("c", "change the note")
    pending = await toolbox.execute(
        "write_file",
        {
            "path": "notes/todo.md",
            "content": "new\n",
            "mode": "overwrite",
            "expected_sha256": digest,
        },
        conversation_id="c",
    )
    token = _pending_id(pending)
    same_turn = await toolbox.execute(
        "confirm_action", {"pending_id": token}, conversation_id="c"
    )
    assert same_turn.startswith("ERROR:")

    toolbox.begin_turn("c", "确认")
    confirmed = await toolbox.execute(
        "confirm_action", {"pending_id": token}, conversation_id="c"
    )
    assert "Wrote" in confirmed
    assert (workspace.root / "notes/todo.md").read_text(encoding="utf-8") == "new\n"

    toolbox.begin_turn("c", "run a command")
    pending = await toolbox.execute(
        "run_command",
        {"argv": [sys.executable, "-c", "print('from-command')"]},
        conversation_id="c",
    )
    token = _pending_id(pending)
    toolbox.begin_turn("c", "确认")
    output = await toolbox.execute(
        "confirm_action", {"pending_id": token}, conversation_id="c"
    )
    assert "from-command" in output


async def test_toolbox_disables_local_tools_for_tasks_and_schemas(tmp_path: Path) -> None:
    toolbox = _toolbox(tmp_path, workspace=_workspace(tmp_path), commands=_runner(tmp_path))
    names = {item["function"]["name"] for item in toolbox.schemas(local_tools=True)}
    assert {"read_file", "run_command", "confirm_action"} <= names
    task_names = {
        item["function"]["name"]
        for item in toolbox.schemas(task_management=False, local_tools=False)
    }
    assert "run_command" not in task_names
    assert "schedule_task" not in task_names

    denied = await toolbox.execute(
        "read_file", {"path": "anything"}, conversation_id="c", allow_local=False
    )
    assert denied.startswith("ERROR:") and "unavailable" in denied

    workspace_only = _toolbox(tmp_path, workspace=_workspace(tmp_path))
    file_names = {
        item["function"]["name"] for item in workspace_only.schemas(local_tools=True)
    }
    assert "read_file" in file_names
    assert "run_command" not in file_names

    commands_only = _toolbox(tmp_path, commands=_runner(tmp_path))
    command_names = {
        item["function"]["name"] for item in commands_only.schemas(local_tools=True)
    }
    assert "run_command" in command_names
    assert "read_file" not in command_names


def test_confirmation_phrases_are_short_and_explicit() -> None:
    assert is_confirmation("确认")
    assert is_confirmation("  Confirm. ")
    assert not is_confirmation("确认，然后再创建一个文件")
    assert not is_confirmation("yes please delete everything")


class _LocalPromptSkill:
    manifest = SkillManifest(
        name="local",
        title="Local tools",
        description="test",
        instructions=LOCAL.instructions,
        tools=("run_command",),
    )
    available = True
    unavailable_reason = ""

    def schemas(self):
        return []


def test_system_prompt_mentions_local_rules_and_pending() -> None:
    turn = ChatTurn(
        conversation_id="c",
        user_message="run it",
        history=[],
        now=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
        timezone="UTC",
        pending="run_command abc123: run echo hi",
    )
    prompt = build_system_prompt(turn, [_LocalPromptSkill()])
    assert "confirm_action" in prompt
    assert "Never put file contents" in prompt
    assert "abc123" in prompt
    scheduled = ChatTurn(
        conversation_id="c",
        user_message="brief",
        history=[],
        now=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
        timezone="UTC",
        scheduled=True,
    )
    task = build_system_prompt(scheduled, [])
    assert "Local file and command tools are not available" in task


class _ScriptedModel:
    name = "scripted"

    def __init__(self) -> None:
        self.calls = 0
        self.token = ""

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return _tool_call(
                "run_command",
                {"argv": [sys.executable, "-c", "print('agent-command')"]},
            )
        if self.calls == 2:
            for message in reversed(messages):
                if message.get("role") == "tool":
                    match = re.search(r"PENDING ([0-9a-f]+):", str(message.get("content")))
                    if match:
                        self.token = match.group(1)
            return _text("I need your confirmation before running that.")
        if self.calls == 3:
            return _tool_call("confirm_action", {"pending_id": self.token})
        return _text("The command ran.")


def _tool_call(name: str, arguments: dict) -> ChatResult:
    encoded = json.dumps(arguments)
    return ChatResult(
        text="",
        tool_calls=(ToolCall(id=f"call-{name}", name=name, arguments=encoded),),
        model="scripted",
        usage=Usage(provider="scripted", unit="token", quantity=0),
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{name}",
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


async def test_agent_runs_a_command_only_after_the_confirmation_turn(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    commands = _runner(tmp_path)
    toolbox = _toolbox(tmp_path, workspace=workspace, commands=commands)
    store = ConversationStore(tmp_path / "conversations")
    model = _ScriptedModel()
    agent = AssistantAgent(
        store=store,
        memory=None,
        toolbox=toolbox,
        engine=_engine(toolbox, model),
        max_history_messages=30,
        timezone=ZoneInfo("UTC"),
    )

    first = await agent.chat("run the command", channel="admin")
    assert first.conversation_id
    blocks = store.get(first.conversation_id).messages
    assert [message.role for message in blocks] == ["user", "tool", "assistant"]
    assert "--- stdout ---" not in blocks[1].text
    assert "PENDING" in blocks[1].text
    audit_path = tmp_path / "state" / "exec.jsonl"
    assert not audit_path.exists() or "agent-command" not in audit_path.read_text(
        encoding="utf-8"
    )

    second = await agent.chat(
        "确认", conversation_id=first.conversation_id, channel="admin"
    )
    assert second.text == "The command ran."
    text = store.path_for(first.conversation_id).read_text(encoding="utf-8")
    assert "agent-command" in text
    assert "agent-command" in audit_path.read_text(encoding="utf-8")


class _OneShotToolModel:
    name = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return _tool_call(
                "run_command",
                {"argv": [sys.executable, "-c", "print('must-not-run')"]},
            )
        return _text("I cannot run commands in a task run.")


async def test_task_run_never_gets_local_tools(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    commands = _runner(tmp_path)
    toolbox = _toolbox(tmp_path, workspace=workspace, commands=commands)
    store = ConversationStore(tmp_path / "conversations")
    agent = AssistantAgent(
        store=store,
        memory=None,
        toolbox=toolbox,
        engine=_engine(toolbox, _OneShotToolModel()),
        max_history_messages=30,
        timezone=ZoneInfo("UTC"),
    )

    reply = await agent.chat("run it unattended", source="task", channel="task")
    conversation = store.get(reply.conversation_id)
    assert conversation.source == "task"
    tool_blocks = [message for message in conversation.messages if message.role == "tool"]
    assert tool_blocks and tool_blocks[0].text.startswith("ERROR:")
    assert "not enabled" in tool_blocks[0].text
    assert not (tmp_path / "state" / "exec.jsonl").exists()
