from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_local import (
    AuditLog,
    CommandRunner,
    LLMReviewer,
    ListFiles,
    LocalExecutor,
    PendingActions,
    ReadFile,
    ReviewDecision,
    RunCommand,
    Whitelist,
    Workspace,
    WriteFile,
    redact_secrets,
)
from winnie_local.local import CommandPlan, WritePlan
from winnie_local.policy import Whitelist as Policy


def _workspace(tmp_path: Path, **kwargs) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return Workspace(
        root=root,
        protected=[root / "secret"],
        audit=AuditLog(tmp_path / "state" / "files.jsonl"),
        backup_dir=tmp_path / "state" / "backups",
        **kwargs,
    )


def _commands(tmp_path: Path, *, require_confirmation: bool = True) -> CommandRunner:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return CommandRunner(
        roots=[root],
        workdir=root,
        timeout_seconds=5.0,
        max_output_chars=4_000,
        require_confirmation=require_confirmation,
        audit=AuditLog(tmp_path / "state" / "exec.jsonl"),
    )


def _whitelist(
    *,
    operations: list[str] | None = None,
    commands: list[str] | None = None,
    git_subcommands: list[str] | None = None,
) -> Policy:
    return Policy(
        operations=operations or ["list_files", "read_file"],
        commands=commands or ["pwd", "ls", "cat", "head", "tail", "wc"],
        git_subcommands=git_subcommands or ["status", "rev-parse", "ls-files"],
    )


class _Recorder:
    def __init__(self, response: str = '{"allow": true, "risk": "low", "reason": "ok"}') -> None:
        self.response = response
        self.calls = 0
        self.last_prompt = ""
        self.last_system = ""

    async def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        self.last_system = system
        self.last_prompt = user
        return self.response


def _executor(
    tmp_path: Path,
    *,
    workspace: Workspace | None = None,
    commands: CommandRunner | None = None,
    recorder: _Recorder | None = None,
    review_enabled: bool = True,
    whitelist: Policy | None = None,
) -> LocalExecutor:
    reviewer = recorder or _Recorder()
    return LocalExecutor(
        workspace=workspace,
        commands=commands,
        reviewer=LLMReviewer(
            complete=reviewer,
            model="reviewer",
            timeout_seconds=5.0,
            max_action_chars=2_000,
        ),
        review_enabled=review_enabled,
        review_audit=AuditLog(tmp_path / "state" / "review.jsonl"),
        whitelist=whitelist or _whitelist(),
        pending=PendingActions(ttl_seconds=300),
        model_name="reviewer",
        max_review_detail_chars=2_000,
    )


async def test_reviewer_parses_json_and_fails_closed() -> None:
    allow = LLMReviewer(
        complete=_Recorder('```json\n{"allow": true, "risk": "low", "reason": "narrow"}\n```'),
        model="m",
    )
    decision = await allow.review(
        action="read_file",
        summary="read a",
        detail="path: a",
        user_request="read a",
        workspace_roots=["/tmp"],
    )
    assert decision.allow
    assert decision.risk == "low"

    deny = LLMReviewer(complete=_Recorder('{"allow": false, "risk": "high", "reason": "bad"}'), model="m")
    decision = await deny.review(
        action="run_command",
        summary="rm",
        detail="argv: rm",
        user_request="delete",
        workspace_roots=["/tmp"],
    )
    assert not decision.allow
    assert decision.source == "llm"

    malformed = LLMReviewer(complete=_Recorder("Fake reply: nope"), model="m")
    decision = await malformed.review(
        action="write_file",
        summary="write",
        detail="x",
        user_request="write",
        workspace_roots=["/tmp"],
    )
    assert not decision.allow
    assert decision.source == "error"


async def test_reviewer_timeout_denies() -> None:
    async def slow(system: str, user: str) -> str:
        await asyncio.sleep(0.2)
        return '{"allow": true, "risk": "low", "reason": "late"}'

    reviewer = LLMReviewer(complete=slow, model="m", timeout_seconds=0.05)
    decision = await reviewer.review(
        action="run_command",
        summary="sleep",
        detail="x",
        user_request="run",
        workspace_roots=["/tmp"],
    )
    assert not decision.allow
    assert "timed out" in decision.reason


async def test_reviewer_redacts_secrets_before_sending() -> None:
    recorder = _Recorder()
    reviewer = LLMReviewer(complete=recorder, model="m")
    await reviewer.review(
        action="write_file",
        summary="write",
        detail="password=supersecret\nkey sk-abcdefghijklmnopqrstuvwxyz",
        user_request="use password=supersecret",
        workspace_roots=["/tmp"],
    )
    assert "supersecret" not in recorder.last_prompt
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in recorder.last_prompt
    assert "<redacted>" in recorder.last_prompt
    assert "untrusted" in recorder.last_system.lower()
    assert redact_secrets("token=abc") == "token=<redacted>"


async def test_whitelist_read_skips_review_and_denied_write_does_not_run(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    (workspace.root / "note.txt").write_text("hello", encoding="utf-8")
    recorder = _Recorder()
    executor = _executor(tmp_path, workspace=workspace, recorder=recorder)
    executor.begin_turn("c", "read note")

    read = await executor.execute(ReadFile(path="note.txt"), conversation_id="c")
    assert read.status == "executed"
    assert "hello" in read.text
    assert recorder.calls == 0

    denied = _Recorder('{"allow": false, "risk": "high", "reason": "not allowed"}')
    executor = _executor(tmp_path, workspace=workspace, recorder=denied)
    executor.begin_turn("c", "write evil")
    outcome = await executor.execute(
        WriteFile(path="evil.txt", content="x"), conversation_id="c"
    )
    assert outcome.status == "denied"
    assert "REVIEW DENIED" in outcome.text
    assert not (workspace.root / "evil.txt").exists()
    audit = (tmp_path / "state" / "review.jsonl").read_text(encoding="utf-8")
    assert '"allow": false' in audit


async def test_allowed_write_is_reviewed_and_audited(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    recorder = _Recorder()
    executor = _executor(tmp_path, workspace=workspace, recorder=recorder)
    executor.begin_turn("c", "create a note")
    outcome = await executor.execute(
        WriteFile(path="note.txt", content="hello\n"), conversation_id="c"
    )
    assert outcome.status == "executed"
    assert (workspace.root / "note.txt").read_text(encoding="utf-8") == "hello\n"
    assert recorder.calls == 1
    audit = (tmp_path / "state" / "review.jsonl").read_text(encoding="utf-8")
    assert '"allow": true' in audit
    assert '"action": "write_file"' in audit


async def test_review_failure_or_disabled_denies_non_whitelisted(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    async def broken(system: str, user: str) -> str:
        raise RuntimeError("provider down")

    broken_reviewer = LLMReviewer(complete=broken, model="m")
    executor = LocalExecutor(
        workspace=workspace,
        commands=None,
        reviewer=broken_reviewer,
        review_enabled=True,
        review_audit=AuditLog(tmp_path / "state" / "review.jsonl"),
        whitelist=_whitelist(),
        pending=PendingActions(ttl_seconds=300),
        model_name="m",
    )
    executor.begin_turn("c", "write")
    outcome = await executor.execute(
        WriteFile(path="x.txt", content="x"), conversation_id="c"
    )
    assert outcome.status == "denied"
    assert "REVIEW UNAVAILABLE" in outcome.text
    assert not (workspace.root / "x.txt").exists()

    disabled = _executor(
        tmp_path, workspace=workspace, review_enabled=False
    )
    disabled.begin_turn("c", "write")
    outcome = await disabled.execute(
        WriteFile(path="y.txt", content="y"), conversation_id="c"
    )
    assert outcome.status == "denied"
    assert "REVIEW DISABLED" in outcome.text


async def test_whitelisted_command_skips_review_but_still_confirms(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    commands = _commands(tmp_path)
    recorder = _Recorder()
    executor = _executor(
        tmp_path, workspace=workspace, commands=commands, recorder=recorder
    )
    executor.begin_turn("c", "where am I")
    outcome = await executor.execute(RunCommand(argv=["pwd"]), conversation_id="c")
    assert outcome.status == "pending"
    assert recorder.calls == 0
    assert "whitelisted" in outcome.text

    executor.begin_turn("c", "确认")
    text = await executor.confirm("c", outcome.token)
    assert "exit: 0" in text
    assert str(workspace.root) in text


async def test_non_whitelisted_command_is_reviewed_and_denied(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    commands = _commands(tmp_path)
    denied = _Recorder('{"allow": false, "risk": "high", "reason": "not requested"}')
    executor = _executor(
        tmp_path, workspace=workspace, commands=commands, recorder=denied
    )
    executor.begin_turn("c", "run a command")
    outcome = await executor.execute(
        RunCommand(argv=[sys.executable, "-c", "print('should-not-run')"]),
        conversation_id="c",
    )
    assert outcome.status == "denied"
    assert denied.calls == 1
    assert "should-not-run" in denied.last_prompt  # the reviewer sees the argv
    assert not (tmp_path / "state" / "exec.jsonl").exists()


async def test_non_whitelisted_command_allowed_then_confirmed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    commands = _commands(tmp_path)
    allow = _Recorder()
    executor = _executor(
        tmp_path, workspace=workspace, commands=commands, recorder=allow
    )
    executor.begin_turn("c", "run a command")
    outcome = await executor.execute(
        RunCommand(argv=[sys.executable, "-c", "print('ran-after-review')"]),
        conversation_id="c",
    )
    assert outcome.status == "pending"
    assert allow.calls == 1
    assert "review allowed" in outcome.text

    executor.begin_turn("c", "确认")
    text = await executor.confirm("c", outcome.token)
    assert "ran-after-review" in text
    assert (tmp_path / "state" / "exec.jsonl").exists()


def test_whitelist_policy_paths_and_git(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.root / "note.txt").write_text("hello", encoding="utf-8")
    policy = _whitelist()

    def plan(argv: list[str]) -> CommandPlan:
        return CommandPlan(argv=argv, cwd=workspace.root, summary=" ".join(argv))

    assert policy.allows("list_files", None, workspace)
    assert policy.allows("read_file", None, workspace)
    assert not policy.allows("write_file", None, workspace)
    assert policy.allows("run_command", plan(["pwd"]), workspace)
    assert policy.allows("run_command", plan(["ls"]), workspace)
    assert policy.allows("run_command", plan(["cat", "note.txt"]), workspace)
    assert not policy.allows("run_command", plan(["cat", "/etc/passwd"]), workspace)
    assert not policy.allows("run_command", plan(["cat", "../outside"]), workspace)
    assert not policy.allows(
        "run_command", plan(["wc", "--files0-from=/etc/passwd"]), workspace
    )
    assert policy.allows("run_command", plan(["git", "status"]), workspace)
    assert policy.allows("run_command", plan(["git", "rev-parse", "HEAD"]), workspace)
    assert not policy.allows("run_command", plan(["git", "diff"]), workspace)
    assert not policy.allows(
        "run_command", plan(["git", "status", "--output=/tmp/x"]), workspace
    )
    assert not policy.allows(
        "run_command", plan(["git", "-C", "/tmp", "status"]), workspace
    )
    assert not policy.allows("run_command", plan(["rm", "-rf", "x"]), workspace)
    assert not policy.allows(
        "run_command", plan([str(workspace.root / "git"), "status"]), workspace
    )


def test_local_config_defaults() -> None:
    from winnie_local.config import LocalConfig

    config = LocalConfig()
    assert config.review.enabled
    assert "read_file" in config.whitelist.operations
    assert "pwd" in config.whitelist.commands
    assert "status" in config.whitelist.git_subcommands
    assert "write_file" not in config.whitelist.operations
