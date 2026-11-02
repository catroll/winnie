"""Configuration models shared by the host and the local executor."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Read-only operations that do not need an LLM review. The file operations are
# already confined to the workspace and refuse credential paths.
DEFAULT_WHITELIST_OPERATIONS = ("list_files", "read_file")
# Read-only commands whose path arguments are checked against the workspace.
DEFAULT_WHITELIST_COMMANDS = ("pwd", "ls", "cat", "head", "tail", "wc")
# Read-only git subcommands. Write options such as --output are rejected.
DEFAULT_WHITELIST_GIT = ("status", "diff", "log", "show", "rev-parse", "ls-files")


class ReviewConfig(BaseModel):
    """One LLM review before a non-whitelisted local action."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    # How much of the action (and its diff/preview) the reviewer may see.
    max_action_chars: int = Field(default=4_000, ge=500, le=20_000)


class WhitelistConfig(BaseModel):
    """Operations that skip the LLM review.

    This is a guardrail, not a security boundary: adding a write command here
    removes the review for it. The human confirmation settings still apply.
    """

    model_config = ConfigDict(extra="forbid")

    operations: list[str] = Field(
        default_factory=lambda: list(DEFAULT_WHITELIST_OPERATIONS)
    )
    commands: list[str] = Field(
        default_factory=lambda: list(DEFAULT_WHITELIST_COMMANDS)
    )
    git_subcommands: list[str] = Field(
        default_factory=lambda: list(DEFAULT_WHITELIST_GIT)
    )
    # Whitelisted actions are read-only, so they run without a confirmation.
    # Set false to keep asking before even a whitelisted command runs.
    unattended: bool = True


class LocalConfig(BaseModel):
    """Review and whitelist settings for the local execution package."""

    model_config = ConfigDict(extra="forbid")

    review: ReviewConfig = Field(default_factory=ReviewConfig)
    whitelist: WhitelistConfig = Field(default_factory=WhitelistConfig)
