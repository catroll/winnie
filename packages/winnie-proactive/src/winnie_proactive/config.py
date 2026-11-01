"""Configuration for proactive interventions.

The engine is deliberately configurable in policy, not in detection: the
horizon, quiet hours, interrupt budget and channel ladder are deployment
settings, while "what counts as a birthday" is code. A policy mistake should be
a YAML edit; a detection mistake should be a test.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NtfyConfig(BaseModel):
    """The fast bare notification rung.

    A self-hosted server keeps the text on the owner's infrastructure; the
    public ``ntfy.sh`` works the same way but is a third party.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    server: str = "https://ntfy.sh"
    # On a public ntfy server the topic name is the only secret: anyone who
    # knows it can subscribe. Prefer topic_env so the name lives in a gitignored
    # env file; a self-hosted server can use a plain topic instead.
    topic: str = ""
    topic_env: str = "WINNIE_NTFY_TOPIC"
    token_env: str = "WINNIE_NTFY_TOKEN"
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    # ntfy priorities: min, low, default, high, urgent.
    priority: str = "default"


class EmailConfig(BaseModel):
    """The durable rung: survives a killed app, a dead battery and a reboot."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    to: str = ""
    from_address: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65535)
    use_ssl: bool = True
    username_env: str = "WINNIE_SMTP_USER"
    password_env: str = "WINNIE_SMTP_PASSWORD"
    timeout_seconds: float = Field(default=15.0, ge=5.0, le=60.0)


class ChannelsConfig(BaseModel):
    """Which channels exist, and the order the ladder walks them in."""

    model_config = ConfigDict(extra="forbid")

    # The host adds "push" when the push capability is enabled; "console" is
    # always the inbox and never sends anything.
    order: list[str] = Field(default_factory=lambda: ["push", "ntfy", "email"])
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)


class EscalationConfig(BaseModel):
    """When to reach for the next channel, in minutes after the previous send."""

    model_config = ConfigDict(extra="forbid")

    # One entry per escalation after the first send. The ladder stops after
    # these steps; what remains unacked shows up in the digest, not in a loop.
    after_minutes: list[int] = Field(default_factory=lambda: [20, 60])


class BirthdayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Stages: T-14 heads-up (digest), T-7 prepare, T-2 confirm, T-0 day, and a
    # follow-up the next day. Each value must be >= 0 days before the date.
    lead_days: list[int] = Field(default_factory=lambda: [14, 7, 2, 0])
    follow_up_days: int = Field(default=1, ge=0, le=7)
    # A pending birthday stage is held until this local hour.
    notify_hour: int = Field(default=9, ge=0, le=23)


class WorkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # How long before a timed schedule item the owner should leave.
    leave_buffer_minutes: int = Field(default=30, ge=5, le=180)
    # Unchecked items from the last N days make one morning digest entry.
    overdue_days: int = Field(default=7, ge=1, le=30)
    overdue_hour: int = Field(default=8, ge=0, le=23)


class ComposerConfig(BaseModel):
    """Optional model rewrite of the reminder body.

    Off by default: the template is the contract, and a deployment without a
    model must still deliver every reminder.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_chars: int = Field(default=600, ge=80, le=2000)
    timeout_seconds: float = Field(default=20.0, ge=3.0, le=60.0)


class DigestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    morning: str = "07:30"
    evening: str = "20:30"
    # How far ahead the digest looks for P2 items.
    lookahead_days: int = Field(default=14, ge=1, le=120)


class ProactiveConfig(BaseModel):
    """Everything the capability needs from the host."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Whose items these are. Empty means the host resolves the single account.
    owner_user: str = ""
    timezone: str = "Asia/Shanghai"
    # Used to build the acknowledgement link in an email or ntfy message.
    # Empty means no link (the console can still ack).
    public_url: str = ""
    # Overrides the owner edits (see docs/proactive.md §5).
    root: str = "../../data/proactive"
    horizon_days: int = Field(default=120, ge=7, le=730)
    scan_interval_seconds: float = Field(default=900.0, ge=60.0, le=86400.0)
    tick_seconds: float = Field(default=60.0, ge=5.0, le=3600.0)
    # A stage planned while the server was down is still sent inside this
    # window, collapsed into one message; older than this it is dropped.
    catch_up_hours: int = Field(default=12, ge=0, le=168)
    quiet_hours: tuple[str, str] = ("22:30", "07:30")
    # A same-day P0 may cross quiet hours; nothing else does.
    allow_urgent: bool = True
    max_interrupts_per_day: int = Field(default=4, ge=0, le=50)
    daily_budget_window: int = Field(default=24, ge=1, le=48)
    # Unacked items expire this long after their due date.
    expiry_days: int = Field(default=3, ge=1, le=90)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    birthday: BirthdayConfig = Field(default_factory=BirthdayConfig)
    work: WorkConfig = Field(default_factory=WorkConfig)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    composer: ComposerConfig = Field(default_factory=ComposerConfig)
