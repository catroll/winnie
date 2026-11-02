"""Whitelist policy for local actions.

This is a guardrail, not a security boundary. It covers read-only file tools, a
small set of read-only commands whose path arguments stay inside the workspace,
and read-only HTTP GETs for public data. Everything else goes to the LLM
reviewer and, unless confirmed later, does not run.

Whitelisted actions ran the review only in the first version, so `ls` and every
`curl` still waited for a "确认" before doing a read. That is what the owner kept
hitting: the confirmation asked the human to approve a request that changes
nothing on the machine. A whitelisted action now also runs unattended; the
confirmation gate is for writes and for actions the reviewer had to allow.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from winnie_local.local import CommandPlan, LocalToolError, Workspace, WritePlan

# These are read-only commands; their non-option arguments are checked as paths.
PATH_COMMANDS = frozenset({"ls", "cat", "head", "tail", "wc"})
# Read-only network fetches. Only GET requests to public hosts qualify: no
# upload, no output file, no request body, no header, no cookie, no proxy and no
# private or loopback address.
NETWORK_READ_COMMANDS = frozenset({"curl"})
CURL_BARE_FLAGS = frozenset(
    {"-s", "--silent", "-S", "--show-error", "-f", "--fail",
     "-L", "--location", "--compressed"}
)
CURL_VALUE_FLAGS = frozenset({"-m", "--max-time"})
CURL_MAX_TIME = 120
_PRIVATE_HOSTS = frozenset({"localhost", "localhost.localdomain"})
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
GIT_FORBIDDEN = (
    "--output",
    "--ext-diff",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--open-files-in-pager",
)
GIT_BRANCH_SAFE = frozenset(
    {"-a", "-r", "-v", "--list", "--all", "--remotes", "--verbose"}
)


class Whitelist:
    def __init__(
        self,
        *,
        operations: list[str] | tuple[str, ...],
        commands: list[str] | tuple[str, ...],
        git_subcommands: list[str] | tuple[str, ...],
        unattended: bool = True,
    ) -> None:
        self.operations = frozenset(operations)
        self.commands = frozenset(commands)
        self.git_subcommands = frozenset(git_subcommands)
        # A whitelisted action is read-only, so it does not need a human yes.
        # Set false to keep the old "whitelisted but still confirm" behaviour.
        self.unattended = unattended

    def allows(
        self,
        action: str,
        plan: CommandPlan | WritePlan | None,
        workspace: Workspace,
    ) -> bool:
        if action in {"list_files", "read_file"}:
            return action in self.operations
        if action == "write_file":
            return action in self.operations
        if action == "run_command" and isinstance(plan, CommandPlan):
            return self._command(plan, workspace)
        return False

    def _command(self, plan: CommandPlan, workspace: Workspace) -> bool:
        # A path-qualified executable could be a project-local binary named
        # "git" or "cat"; resolve it through PATH only for the whitelist.
        if "/" in plan.argv[0] or "\\" in plan.argv[0]:
            return False
        name = Path(plan.argv[0]).name
        if name in NETWORK_READ_COMMANDS:
            return self._network_read(plan)
        if name == "git":
            return self._git(plan, workspace)
        if name not in self.commands:
            return False
        if name == "pwd":
            return all(
                arg in {"-L", "-P", "--logical", "--physical"}
                for arg in plan.argv[1:]
            )
        return self._paths_allowed(plan.argv[1:], plan.cwd, workspace)

    def _paths_allowed(
        self, args: list[str], base: Path, workspace: Workspace
    ) -> bool:
        for arg in args:
            if arg in {"--", "-"}:
                continue
            if arg.startswith("-"):
                # These options read a file list or a command from a path.
                if arg.startswith("--files0-from") or arg.startswith("--pre"):
                    return False
                continue
            try:
                workspace.resolve(arg, write=False, base=base)
            except LocalToolError:
                return False
        return True

    def _git(self, plan: CommandPlan, workspace: Workspace) -> bool:
        args = plan.argv[1:]
        index = 0
        subcommand = ""
        while index < len(args):
            arg = args[index]
            if arg == "-C":
                if index + 1 >= len(args):
                    return False
                try:
                    workspace.resolve(args[index + 1], write=False, base=plan.cwd)
                except LocalToolError:
                    return False
                index += 2
                continue
            if arg in {"--no-pager", "--paginate"}:
                index += 1
                continue
            if arg.startswith("-"):
                return False
            subcommand = arg
            index += 1
            break
        if not subcommand or subcommand not in self.git_subcommands:
            return False
        rest = args[index:]
        for arg in rest:
            if any(token in arg for token in GIT_FORBIDDEN):
                return False
        if subcommand == "branch":
            return all(arg in GIT_BRANCH_SAFE for arg in rest)
        return True

    def _network_read(self, plan: CommandPlan) -> bool:
        """True for a curl GET that only reads public HTTP data."""
        args = plan.argv[1:]
        if plan.argv[0] != "curl" or not args:
            return False
        urls = 0
        index = 0
        while index < len(args):
            arg = args[index]
            index += 1
            if arg == "--":
                rest = args[index:]
                if not rest or not all(_public_url(item) for item in rest):
                    return False
                return True
            if arg.startswith("-") and arg != "-":
                if not _curl_flag_allowed(arg):
                    return False
                if arg in CURL_VALUE_FLAGS:
                    if index >= len(args) or not _seconds(args[index]):
                        return False
                    index += 1
                continue
            if not _public_url(arg):
                return False
            urls += 1
        return urls > 0


def _curl_flag_allowed(arg: str) -> bool:
    if arg in CURL_BARE_FLAGS:
        return True
    if arg in CURL_VALUE_FLAGS:
        return True
    if arg.startswith("--max-time="):
        return _seconds(arg.split("=", 1)[1])
    if arg.startswith("-m") and len(arg) > 2:
        return _seconds(arg[2:])
    # Combined short flags such as -sS, and nothing else: -o, -d, -F, -T, -H,
    # -b, -c, -u, -x, -K and --proxy all fail here.
    if arg.startswith("-") and not arg.startswith("--"):
        return all(char in "sSfL" for char in arg[1:])
    return False


def _seconds(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= CURL_MAX_TIME


def _public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in _PRIVATE_HOSTS or host.endswith(_PRIVATE_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name with no dot is an intranet host, not a public endpoint.
        if "." not in host:
            return False
        # Numeric and hex spellings ("2130706433", "0x7f.0.0.1") still resolve
        # to an address; pass only a real name or a real IP literal.
        if not any(char.isalpha() for char in host):
            return False
        if host.startswith("0x") or host.rsplit(".", 1)[-1].isdigit():
            return False
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )
