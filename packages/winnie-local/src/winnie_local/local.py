"""Local file and command tools with explicit confirmation.

These tools are deliberately more powerful than the rest of the assistant. File
operations resolve every path under a configured root and refuse the assistant's
own storage and common credential paths. Commands are not an OS sandbox: they
run as the API user. Both are opt-in, audited, and commands (and overwrites)
only run after a human confirmation in a later conversation turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Credential directories are never listed, read or written by the file tools.
# The assistant's own storage is added by the factory.
DEFAULT_PROTECTED = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.config",
    "~/.local/share/keyrings",
    "~/.kube",
    "~/.docker",
    "~/.netrc",
    "~/.git-credentials",
    "~/.password-store",
)
SECRET_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".git-credentials",
        "id_rsa",
        "id_ecdsa",
        "id_ed25519",
        "id_dsa",
    }
)
SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"})

# A short, explicit answer. A long message that happens to contain "yes" must
# not count as confirmation.
CONFIRMATIONS = frozenset(
    {
        "confirm",
        "confirmed",
        "yes",
        "y",
        "ok",
        "okay",
        "确认",
        "确认执行",
        "可以",
        "可以执行",
        "执行",
        "执行吧",
        "同意",
        "继续",
        "好的",
        "好",
        "行",
        "确定",
        "没问题",
    }
)
_CONFIRM_STRIP = re.compile(r"[\s。.!！?？,，、:：;；\"'“”‘’]+")


class LocalToolError(Exception):
    """A local tool call that cannot be completed for a reportable reason."""


def is_confirmation(text: str) -> bool:
    normalized = _CONFIRM_STRIP.sub("", text.strip().lower())
    return normalized in CONFIRMATIONS


def _expand(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _resolve_under_roots(raw: str, roots: list[Path], base: Path) -> Path:
    path = Path(raw or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise LocalToolError(f"cannot resolve path: {raw}") from exc
    for root in roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    raise LocalToolError(f"path is outside the configured roots: {raw}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp_name, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _create_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class AuditLog:
    """Append-only JSONL record of local side effects, mode 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        payload = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **record,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            fd = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)


@dataclass
class PendingAction:
    token: str
    kind: str
    summary: str
    turn: int
    created_at: float
    execute: Callable[[], Awaitable[str]]


class PendingActions:
    """One confirmation wait per conversation.

    A pending action can only run in a *later* turn and only when the user's
    message is an explicit confirmation. Keeping the turn check here means a
    model that calls the tool twice in one turn cannot execute by itself.
    """

    def __init__(self, *, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PendingAction] = {}
        self._turns: dict[str, int] = {}
        self._messages: dict[str, str] = {}

    def begin_turn(self, conversation_id: str, user_message: str) -> int:
        turn = self._turns.get(conversation_id, 0) + 1
        self._turns[conversation_id] = turn
        self._messages[conversation_id] = user_message
        action = self._items.get(conversation_id)
        if action is not None and (
            self._expired(action) or not is_confirmation(user_message)
        ):
            self._items.pop(conversation_id, None)
        return turn

    def has_turn(self, conversation_id: str) -> bool:
        return conversation_id in self._turns

    def current_turn(self, conversation_id: str) -> int:
        return self._turns.get(conversation_id, 0)

    def user_message(self, conversation_id: str) -> str:
        return self._messages.get(conversation_id, "")

    def put(
        self,
        conversation_id: str,
        *,
        kind: str,
        summary: str,
        execute: Callable[[], Awaitable[str]],
    ) -> PendingAction:
        action = PendingAction(
            token=secrets.token_hex(4),
            kind=kind,
            summary=summary,
            turn=self.current_turn(conversation_id),
            created_at=time.monotonic(),
            execute=execute,
        )
        self._items[conversation_id] = action
        return action

    def take(self, conversation_id: str, token: str) -> PendingAction:
        action = self._items.get(conversation_id)
        if action is None:
            raise LocalToolError("there is no pending action for this conversation")
        if action.token != token:
            raise LocalToolError("that pending id does not match the current action")
        if self._expired(action):
            self._items.pop(conversation_id, None)
            raise LocalToolError(
                "the pending action expired; ask the user to confirm again"
            )
        if self.current_turn(conversation_id) <= action.turn:
            raise LocalToolError(
                "the pending action was created in this turn; ask the user to "
                "confirm and call confirm_action on the next turn"
            )
        if not is_confirmation(self.user_message(conversation_id)):
            raise LocalToolError(
                "the user has not confirmed; ask for an explicit confirmation"
            )
        self._items.pop(conversation_id, None)
        return action

    def summary(self, conversation_id: str) -> str:
        action = self._items.get(conversation_id)
        if action is None:
            return ""
        if self._expired(action):
            self._items.pop(conversation_id, None)
            return ""
        return f"{action.kind} {action.token}: {action.summary}"

    def _expired(self, action: PendingAction) -> bool:
        return time.monotonic() - action.created_at > self.ttl_seconds


@dataclass
class WritePlan:
    target: Path
    content: str
    encoded: bytes
    mode: str
    old_sha: str
    old_size: int
    requires_confirmation: bool
    summary: str


class Workspace:
    """Confined file operations under one or more roots."""

    def __init__(
        self,
        *,
        root: Path,
        extra_roots: list[Path] | None = None,
        protected: list[Path] | None = None,
        max_read_bytes: int = 200_000,
        max_write_bytes: int = 200_000,
        max_list_entries: int = 500,
        confirm_writes: bool = True,
        audit: AuditLog,
        backup_dir: Path,
    ) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self.roots = [
            self.root,
            *[path.expanduser().resolve(strict=False) for path in (extra_roots or [])],
        ]
        protected_paths = [
            *[path.expanduser().resolve(strict=False) for path in (protected or [])],
            *[_expand(path) for path in DEFAULT_PROTECTED],
        ]
        self.protected = list(dict.fromkeys(protected_paths))
        self.max_read_bytes = max_read_bytes
        self.max_write_bytes = max_write_bytes
        self.max_list_entries = max_list_entries
        self.confirm_writes = confirm_writes
        self.audit = audit
        self.backup_dir = backup_dir

    # -- paths -----------------------------------------------------------

    def resolve(
        self, raw: str, *, write: bool, base: Path | None = None
    ) -> Path:
        resolved = _resolve_under_roots(raw, self.roots, base or self.root)
        if self._protected(resolved):
            raise LocalToolError(f"that path is protected: {resolved}")
        if any(part in SECRET_NAMES for part in resolved.parts) or (
            resolved.suffix.lower() in SECRET_SUFFIXES
        ):
            raise LocalToolError(
                "that file looks like a credential; use run_command with explicit "
                "confirmation if you really need it"
            )
        if write and any(part == ".git" for part in resolved.parts):
            raise LocalToolError("writing inside a .git directory is not allowed")
        return resolved

    def _protected(self, path: Path) -> bool:
        return any(path == item or path.is_relative_to(item) for item in self.protected)

    def _marker(self, path: Path) -> str:
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError):
            return "?"
        if not any(
            resolved == root or resolved.is_relative_to(root) for root in self.roots
        ):
            return "outside"
        if self._protected(resolved):
            return "protected"
        if any(part in SECRET_NAMES for part in resolved.parts) or (
            resolved.suffix.lower() in SECRET_SUFFIXES
        ):
            return "credential"
        return ""

    # -- read ------------------------------------------------------------

    def list(self, raw_path: str = ".") -> str:
        target = self.resolve(raw_path or ".", write=False)
        if target.is_file():
            return self._describe(target)
        if not target.is_dir():
            raise LocalToolError(f"not a file or directory: {target}")
        try:
            entries = sorted(
                target.iterdir(), key=lambda entry: (entry.is_file(), entry.name.lower())
            )
        except OSError as exc:
            raise LocalToolError(f"cannot list {target}: {exc}") from exc
        lines = [
            f"{target} ({len(entries)} entries; showing up to {self.max_list_entries})"
        ]
        for entry in entries[: self.max_list_entries]:
            marker = self._marker(entry)
            if marker:
                lines.append(f"  [{marker}] {entry.name}")
                continue
            try:
                info = entry.stat()
            except OSError:
                lines.append(f"  ? {entry.name}")
                continue
            suffix = "/" if entry.is_dir() else ""
            lines.append(
                f"  {entry.name}{suffix}  {info.st_size} bytes  "
                f"{datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec='seconds')}"
            )
        hidden = len(entries) - self.max_list_entries
        if hidden > 0:
            lines.append(f"  … {hidden} more")
        return "\n".join(lines)

    def _describe(self, path: Path) -> str:
        try:
            info = path.stat()
        except OSError as exc:
            raise LocalToolError(f"cannot stat {path}: {exc}") from exc
        return (
            f"{path} ({info.st_size} bytes, modified "
            f"{datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec='seconds')})"
        )

    def read(
        self, raw_path: str, *, start_line: int = 1, max_lines: int = 400
    ) -> str:
        target = self.resolve(raw_path, write=False)
        if not target.is_file():
            raise LocalToolError(f"not a file: {target}")
        size = target.stat().st_size
        if size > self.max_read_bytes:
            raise LocalToolError(
                f"file is {size} bytes, over the {self.max_read_bytes} byte limit; "
                "use run_command with head/sed for a window"
            )
        data = target.read_bytes()
        if b"\x00" in data:
            raise LocalToolError("binary files are not readable as text")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LocalToolError("file is not valid UTF-8 text") from exc
        digest = hashlib.sha256(data).hexdigest()
        lines = text.splitlines()
        total = len(lines)
        start = max(1, start_line)
        if total == 0:
            start = 0
            end = 0
            snippet = ""
        else:
            if start > total:
                raise LocalToolError(f"file has only {total} lines")
            end = min(total, start - 1 + max(1, min(max_lines, 2000)))
            snippet = "\n".join(lines[start - 1 : end])
        header = (
            f"path: {target}\n"
            f"lines: {start}-{end} of {total}\n"
            f"sha256: {digest}\n"
            "---"
        )
        return f"{header}\n{snippet}"

    # -- write -----------------------------------------------------------

    def plan_write(
        self,
        raw_path: str,
        content: str,
        *,
        mode: str = "create",
        expected_sha256: str = "",
    ) -> WritePlan:
        if not isinstance(content, str):
            raise LocalToolError("write_file requires string content")
        if mode not in {"create", "overwrite", "append"}:
            raise LocalToolError("mode must be create, overwrite or append")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_write_bytes:
            raise LocalToolError(
                f"content is {len(encoded)} bytes, over the {self.max_write_bytes} byte limit"
            )
        target = self.resolve(raw_path, write=True)
        exists = target.exists()
        if exists and not target.is_file():
            raise LocalToolError(f"not a regular file: {target}")
        if mode == "create":
            if exists:
                raise LocalToolError(
                    f"{target} already exists; use overwrite with its sha256"
                )
            if not target.parent.is_dir():
                raise LocalToolError(f"parent directory does not exist: {target.parent}")
            return WritePlan(
                target=target,
                content=content,
                encoded=encoded,
                mode=mode,
                old_sha="",
                old_size=0,
                requires_confirmation=False,
                summary=f"create {target} ({len(encoded)} bytes)",
            )
        if not exists:
            raise LocalToolError(f"{target} does not exist; use create")
        old_size = target.stat().st_size
        old_sha = _sha256_file(target)
        if mode == "overwrite":
            expected = expected_sha256.strip().lower()
            if not expected:
                raise LocalToolError(
                    "overwrite needs expected_sha256; read the file first. "
                    f"current sha256: {old_sha}"
                )
            if expected != old_sha:
                raise LocalToolError(
                    f"file changed; current sha256 is {old_sha}; read it again"
                )
            summary = f"overwrite {target} ({old_size} -> {len(encoded)} bytes)"
        else:
            summary = f"append {target} (+{len(encoded)} bytes to {old_size})"
        return WritePlan(
            target=target,
            content=content,
            encoded=encoded,
            mode=mode,
            old_sha=old_sha,
            old_size=old_size,
            requires_confirmation=self.confirm_writes,
            summary=summary,
        )

    def write(self, plan: WritePlan, conversation_id: str) -> str:
        backup = ""
        if plan.mode == "create":
            try:
                _create_bytes(plan.target, plan.encoded)
            except FileExistsError as exc:
                raise LocalToolError(
                    f"{plan.target} appeared after the plan was made; read it first"
                ) from exc
        else:
            current = _sha256_file(plan.target)
            if current != plan.old_sha:
                raise LocalToolError(
                    "file changed since it was read; read it again before writing"
                )
            backup = self._backup(plan.target, conversation_id)
            if plan.mode == "overwrite":
                _atomic_write_bytes(plan.target, plan.encoded)
            else:
                with plan.target.open("ab") as stream:
                    stream.write(plan.encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
        new_sha = _sha256_file(plan.target)
        self.audit.write(
            {
                "kind": "file_write",
                "conversation_id": conversation_id,
                "mode": plan.mode,
                "path": str(plan.target),
                "bytes": len(plan.encoded),
                "sha256": new_sha,
                "backup": str(backup),
            }
        )
        message = f"Wrote {len(plan.encoded)} bytes to {plan.target} (sha256 {new_sha})."
        if plan.mode != "create":
            message += f" Backup: {backup}"
        return message

    def _backup(self, target: Path, conversation_id: str) -> Path:
        folder = self.backup_dir / (conversation_id or "local")
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        destination = folder / f"{stamp}-{secrets.token_hex(2)}-{target.name}"
        shutil.copy2(target, destination)
        os.chmod(destination, 0o600)
        return destination


@dataclass
class CommandPlan:
    argv: list[str]
    cwd: Path
    summary: str


class CommandRunner:
    """Run one argv under a confined working directory.

    The process runs as the API user and is not sandboxed. The allowlist is a
    guardrail; the confirmation gate and the audit log are the controls.
    """

    def __init__(
        self,
        *,
        roots: list[Path],
        workdir: Path,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 8_000,
        allow: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        require_confirmation: bool = True,
        audit: AuditLog,
    ) -> None:
        self.roots = [path.expanduser().resolve(strict=False) for path in roots]
        self.workdir = workdir.expanduser().resolve(strict=False)
        if self.workdir not in self.roots and not any(
            self.workdir.is_relative_to(root) for root in self.roots
        ):
            self.roots.append(self.workdir)
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.allow = frozenset(allow or [])
        self.extra_env = dict(extra_env or {})
        self.require_confirmation = require_confirmation
        self.audit = audit

    def plan(self, argv: Any, raw_cwd: str = "") -> CommandPlan:
        if not isinstance(argv, list) or not argv:
            raise LocalToolError("run_command requires a non-empty argv array")
        if len(argv) > 64:
            raise LocalToolError("argv has too many items")
        cleaned: list[str] = []
        for item in argv:
            if not isinstance(item, str) or not item:
                raise LocalToolError("argv items must be non-empty strings")
            if "\x00" in item:
                raise LocalToolError("argv contains a NUL byte")
            if len(item) > 4096:
                raise LocalToolError("an argv item is too long")
            cleaned.append(item)
        name = Path(cleaned[0]).name
        if self.allow and name not in self.allow:
            raise LocalToolError(
                f"{name!r} is not in exec.allow "
                f"({', '.join(sorted(self.allow))})"
            )
        cwd = _resolve_under_roots(raw_cwd or ".", self.roots, self.workdir)
        if not cwd.is_dir():
            raise LocalToolError(f"working directory does not exist: {cwd}")
        return CommandPlan(argv=cleaned, cwd=cwd, summary=f"run {shlex.join(cleaned)} in {cwd}")

    async def run(self, plan: CommandPlan, conversation_id: str) -> str:
        if self.allow and Path(plan.argv[0]).name not in self.allow:
            raise LocalToolError("the command is no longer allowed by exec.allow")
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *plan.argv,
                cwd=plan.cwd,
                env=self._environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise LocalToolError(f"cannot start {plan.argv[0]!r}: {exc}") from exc

        async def drain(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                return b""
            data = bytearray()
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                if len(data) < self.max_output_chars + 1:
                    data.extend(chunk[: self.max_output_chars + 1 - len(data)])
            return bytes(data)

        stdout_task = asyncio.create_task(drain(process.stdout))
        stderr_task = asyncio.create_task(drain(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        duration = time.monotonic() - started
        code = process.returncode if process.returncode is not None else -1
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if len(out) > self.max_output_chars:
            out = out[: self.max_output_chars] + "\n… (stdout truncated)"
        if len(err) > self.max_output_chars:
            err = err[: self.max_output_chars] + "\n… (stderr truncated)"
        status = "timed out" if timed_out else "finished"
        lines = [
            f"command: {shlex.join(plan.argv)}",
            f"cwd: {plan.cwd}",
            f"exit: {code} ({status} after {duration:.2f}s)",
        ]
        lines.append("--- stdout ---")
        lines.append(out.rstrip() or "(empty)")
        lines.append("--- stderr ---")
        lines.append(err.rstrip() or "(empty)")
        self.audit.write(
            {
                "kind": "command",
                "conversation_id": conversation_id,
                "argv": plan.argv,
                "cwd": str(plan.cwd),
                "exit_code": code,
                "duration_ms": round(duration * 1000),
                "timed_out": timed_out,
                "output_chars": len(out) + len(err),
            }
        )
        return "\n".join(lines)

    def _environment(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get(
                "PATH", "/usr/local/bin:/usr/bin:/bin"
            ),
            "HOME": str(Path.home()),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": "dumb",
            "TMPDIR": tempfile.gettempdir(),
            "PYTHONUNBUFFERED": "1",
            # Keep git from executing a pager, fsmonitor, external diff or a
            # terminal prompt through repository/system configuration.
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_EXTERNAL_DIFF": "",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.pager",
            "GIT_CONFIG_VALUE_1": "cat",
            "GIT_CONFIG_KEY_2": "diff.external",
            "GIT_CONFIG_VALUE_2": "",
        }
        for key in ("LC_ALL", "USER", "LOGNAME", "SHELL", "XDG_RUNTIME_DIR"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        env.update(self.extra_env)
        return env
