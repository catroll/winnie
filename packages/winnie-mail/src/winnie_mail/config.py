"""Where the mailbox settings live and how long a protocol call may take."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # One 0600 YAML file per account. A mailbox password must be readable to be
    # used, so the file's permissions are the protection and it never enters the
    # database or an API response.
    root: str = "../../data/mail"
    # A mailbox that cannot answer a login in this long is not usable from a
    # request handler; the test button reports the timeout instead of hanging.
    default_timeout_seconds: float = Field(default=20.0, ge=3.0, le=120.0)
    # How many of the newest messages per folder a sync keeps in the cache. The
    # sync fetches headers only; a larger number costs bandwidth, not bodies.
    per_folder_limit: int = Field(default=300, ge=1, le=1000)
