"""The single entry point for local actions.

The assistant only builds an action and calls the executor. Planning, the
whitelist check, the LLM review, the pending confirmation and the audit all
happen here, so no other package can quietly bypass them.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from winnie_local.local import (
    AuditLog,
    CommandRunner,
    LocalToolError,
    PendingAction,
    PendingActions,
    Workspace,
    WritePlan,
)
from winnie_local.policy import Whitelist
from winnie_local.review import LLMReviewer, ReviewDecision


@dataclass(frozen=True)
class ListFiles:
    path: str = "."


@dataclass(frozen=True)
class ReadFile:
    path: str
    start_line: int = 1
    max_lines: int = 400


@dataclass(frozen=True)
class WriteFile:
    path: str
    content: str
    mode: str = "create"
    expected_sha256: str = ""


@dataclass(frozen=True)
class RunCommand:
    argv: list[str]
    cwd: str = ""


LocalAction = ListFiles | ReadFile | WriteFile | RunCommand


@dataclass(frozen=True)
class LocalOutcome:
    status: str
    text: str
    token: str = ""


class LocalExecutor:
    def __init__(
        self,
        *,
        workspace: Workspace | None,
        commands: CommandRunner | None,
        reviewer: LLMReviewer | None,
        review_enabled: bool,
        review_audit: AuditLog,
        whitelist: Whitelist,
        pending: PendingActions,
        model_name: str = "",
        max_review_detail_chars: int = 4_000,
    ) -> None:
        self.workspace = workspace
        self.commands = commands
        self.reviewer = reviewer
        self.review_enabled = review_enabled
        self.review_audit = review_audit
        self.whitelist = whitelist
        self.pending = pending
        self.model_name = model_name
        self.max_review_detail_chars = max_review_detail_chars

    @property
    def has_files(self) -> bool:
        return self.workspace is not None

    @property
    def has_commands(self) -> bool:
        return self.commands is not None

    @property
    def has_tools(self) -> bool:
        return self.has_files or self.has_commands

    def begin_turn(self, conversation_id: str, user_message: str) -> None:
        self.pending.begin_turn(conversation_id, user_message)

    def pending_summary(self, conversation_id: str) -> str:
        return self.pending.summary(conversation_id)

    async def execute(
        self, action: LocalAction, *, conversation_id: str
    ) -> LocalOutcome:
        if not self.pending.has_turn(conversation_id):
            self.pending.begin_turn(conversation_id, "")
        if isinstance(action, ListFiles):
            return await self._read_only("list_files", action, conversation_id)
        if isinstance(action, ReadFile):
            return await self._read_only("read_file", action, conversation_id)
        if isinstance(action, WriteFile):
            return await self._write(action, conversation_id)
        if isinstance(action, RunCommand):
            return await self._command(action, conversation_id)
        raise LocalToolError(f"unknown local action: {type(action).__name__}")

    async def confirm(self, conversation_id: str, token: str) -> str:
        action = self.pending.take(conversation_id, token)
        return await action.execute()

    # -- read-only -------------------------------------------------------

    async def _read_only(
        self,
        name: str,
        action: ListFiles | ReadFile,
        conversation_id: str,
    ) -> LocalOutcome:
        workspace = self._workspace()
        if isinstance(action, ListFiles):
            summary = f"list files in {action.path or '.'}"
            detail = f"path: {action.path or '.'}"
            call = lambda: workspace.list(action.path or ".")
        else:
            summary = f"read {action.path}"
            detail = (
                f"path: {action.path}\n"
                f"start_line: {action.start_line}\n"
                f"max_lines: {action.max_lines}"
            )
            call = lambda: workspace.read(
                action.path,
                start_line=action.start_line,
                max_lines=action.max_lines,
            )
        whitelisted = self.whitelist.allows(name, None, workspace)
        decision = None
        if not whitelisted:
            decision = await self._review(
                name, summary, detail, conversation_id, workspace
            )
            if not decision.allow:
                return self._denied(decision)
        text = await asyncio.to_thread(call)
        return LocalOutcome(status="executed", text=text)

    # -- writes ----------------------------------------------------------

    async def _write(
        self, action: WriteFile, conversation_id: str
    ) -> LocalOutcome:
        workspace = self._workspace()
        plan = await asyncio.to_thread(
            workspace.plan_write,
            action.path,
            action.content,
            mode=action.mode,
            expected_sha256=action.expected_sha256,
        )
        whitelisted = self.whitelist.allows("write_file", plan, workspace)
        decision = None
        if not whitelisted:
            decision = await self._review(
                "write_file",
                plan.summary,
                self._write_detail(plan),
                conversation_id,
                workspace,
            )
            if not decision.allow:
                return self._denied(decision)
        note = self._note(whitelisted, decision)
        if plan.requires_confirmation:
            pending = self.pending.put(
                conversation_id,
                kind="write_file",
                summary=f"{plan.summary} [{note}]",
                execute=lambda: asyncio.to_thread(
                    workspace.write, plan, conversation_id
                ),
            )
            return self._pending(pending)
        text = await asyncio.to_thread(workspace.write, plan, conversation_id)
        return LocalOutcome(status="executed", text=f"{text}\n{note}")

    # -- commands --------------------------------------------------------

    async def _command(
        self, action: RunCommand, conversation_id: str
    ) -> LocalOutcome:
        commands = self._commands()
        plan = commands.plan(action.argv, action.cwd)
        summary = plan.summary
        detail = (
            "argv: "
            + json.dumps(plan.argv, ensure_ascii=False)
            + f"\ncwd: {plan.cwd}"
        )
        whitelisted = (
            self.workspace is not None
            and self.whitelist.allows("run_command", plan, self.workspace)
        )
        decision = None
        if not whitelisted:
            decision = await self._review(
                "run_command",
                summary,
                detail,
                conversation_id,
                self.workspace,
            )
            if not decision.allow:
                return self._denied(decision)
        note = self._note(whitelisted, decision)
        # A whitelisted action is read-only by construction (path-confined
        # reads, read-only git, or a public HTTP GET), so it does not ask for a
        # confirmation that can only ever say yes. Writes and reviewed actions
        # keep the gate.
        needs_confirmation = commands.require_confirmation and not (
            whitelisted and self.whitelist.unattended
        )
        if needs_confirmation:
            pending = self.pending.put(
                conversation_id,
                kind="run_command",
                summary=f"{summary} [{note}]",
                execute=lambda: commands.run(plan, conversation_id),
            )
            return self._pending(pending)
        text = await commands.run(plan, conversation_id)
        return LocalOutcome(status="executed", text=f"{text}\n{note}")

    # -- review ----------------------------------------------------------

    async def _review(
        self,
        action: str,
        summary: str,
        detail: str,
        conversation_id: str,
        workspace: Workspace | None,
    ) -> ReviewDecision:
        started = time.monotonic()
        if not self.review_enabled:
            decision = ReviewDecision(
                allow=False,
                risk="high",
                reason="LLM review is disabled and the action is not whitelisted",
                source="disabled",
                model=self.model_name,
            )
        elif self.reviewer is None:
            decision = ReviewDecision(
                allow=False,
                risk="high",
                reason="no reviewer is configured",
                source="error",
                model=self.model_name,
            )
        else:
            roots = (
                [str(root) for root in workspace.roots]
                if workspace is not None
                else [str(self.commands.workdir)] if self.commands else []
            )
            try:
                decision = await self.reviewer.review(
                    action=action,
                    summary=summary,
                    detail=detail,
                    user_request=self.pending.user_message(conversation_id),
                    workspace_roots=roots,
                )
            except Exception as exc:  # noqa: BLE001 - fail closed
                decision = ReviewDecision(
                    allow=False,
                    risk="high",
                    reason=f"the review failed: {type(exc).__name__}",
                    source="error",
                    model=self.model_name,
                )
        self.review_audit.write(
            {
                "kind": "review",
                "conversation_id": conversation_id,
                "action": action,
                "summary": summary,
                "allow": decision.allow,
                "risk": decision.risk,
                "reason": decision.reason,
                "source": decision.source,
                "model": decision.model or self.model_name,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )
        return decision

    # -- helpers ---------------------------------------------------------

    def _workspace(self) -> Workspace:
        if self.workspace is None:
            raise LocalToolError("local file tools are disabled")
        return self.workspace

    def _commands(self) -> CommandRunner:
        if self.commands is None:
            raise LocalToolError("local command execution is disabled")
        return self.commands

    def _write_detail(self, plan: WritePlan) -> str:
        lines = [
            f"path: {plan.target}",
            f"mode: {plan.mode}",
            f"old_size: {plan.old_size}",
            f"new_size: {len(plan.encoded)}",
        ]
        if plan.mode == "create":
            lines.append("new content:")
            lines.append(plan.content)
        elif plan.mode == "append":
            lines.append("appended content:")
            lines.append(plan.content)
        else:
            old = ""
            limit = self.workspace.max_read_bytes if self.workspace else 200_000
            if plan.old_size <= limit:
                try:
                    old = plan.target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    old = ""
            if old:
                diff = difflib.unified_diff(
                    old.splitlines(),
                    plan.content.splitlines(),
                    fromfile="old",
                    tofile="new",
                    lineterm="",
                )
                lines.append("diff:")
                lines.append("\n".join(diff))
            else:
                lines.append("new content:")
                lines.append(plan.content)
        return "\n".join(lines)[: self.max_review_detail_chars]

    def _note(
        self, whitelisted: bool, decision: ReviewDecision | None
    ) -> str:
        if whitelisted:
            return "whitelisted"
        assert decision is not None
        return f"review allowed (risk {decision.risk}): {decision.reason}"

    def _pending(self, action: PendingAction) -> LocalOutcome:
        text = (
            f"PENDING {action.token}: nothing has been changed or run.\n"
            f"{action.summary}\n"
            "Explain exactly what will happen, ask the user to reply with an "
            "explicit confirmation, and call confirm_action with "
            f'pending_id="{action.token}" on the next turn.'
        )
        return LocalOutcome(status="pending", text=text, token=action.token)

    def _denied(self, decision: ReviewDecision) -> LocalOutcome:
        if decision.source == "disabled":
            label = "REVIEW DISABLED"
        elif decision.source == "error":
            label = "REVIEW UNAVAILABLE"
        else:
            label = "REVIEW DENIED"
        text = (
            f"{label} (risk {decision.risk}): {decision.reason}. "
            "Nothing was run or changed."
        )
        return LocalOutcome(status="denied", text=text)
