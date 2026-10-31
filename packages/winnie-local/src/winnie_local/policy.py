"""Whitelist policy for local actions.

This is a guardrail, not a security boundary. It covers read-only file tools and
a small set of read-only commands whose path arguments stay inside the workspace.
Everything else goes to the LLM reviewer. Adding a write command to the configured
allowlist removes the review for that command; the human confirmation settings
still apply.
"""

from __future__ import annotations

from pathlib import Path

from winnie_local.local import CommandPlan, LocalToolError, Workspace, WritePlan

# These are read-only commands; their non-option arguments are checked as paths.
PATH_COMMANDS = frozenset({"ls", "cat", "head", "tail", "wc"})
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
    ) -> None:
        self.operations = frozenset(operations)
        self.commands = frozenset(commands)
        self.git_subcommands = frozenset(git_subcommands)

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
