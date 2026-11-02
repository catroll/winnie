"""Configuration for error collection, health detection and read-only diagnosis.

Disabled by default so a programmatic ``Settings()`` — a test, an embedder — does
not start a detector, install log handlers or write an incidents table. The
shipped ``config.yaml`` turns it on for the service.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DevloopDetectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # How often the in-process poller looks at the services it does not own.
    interval_seconds: float = Field(default=30.0, gt=0)
    probe_timeout_seconds: float = Field(default=3.0, gt=0)


class DevloopDiagnoseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # A diagnosis is one model call over a bounded evidence bundle. The cap is
    # bytes, not tokens, because the evidence is assembled locally.
    timeout_seconds: float = Field(default=45.0, gt=0)
    max_evidence_bytes: int = Field(default=24000, gt=0)


class DevloopConfig(BaseModel):
    """Where errors come from and what may be done with them."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # 4xx answers are usually the caller's mistake, not the program's. Recording
    # them by default would drown a real 500 in validation noise.
    capture_client_errors: bool = False
    # Resolved and ignored incidents are pruned after this many days.
    retention_days: int = Field(default=30, ge=1)
    # Relative paths resolve against apps/api/, like every other root.
    log_dir: str = "../../.cache/logs"
    spool_dir: str = "../../data/devloop/spool"
    # Services the API does not run in-process. The API itself is the
    # watchdog's job: a process cannot observe its own death.
    service_urls: dict[str, str] = Field(
        default_factory=lambda: {
            "index": "http://127.0.0.1:8790/healthz",
            "admin": "http://127.0.0.1:5173/",
        }
    )
    detector: DevloopDetectorConfig = Field(default_factory=DevloopDetectorConfig)
    diagnose: DevloopDiagnoseConfig = Field(default_factory=DevloopDiagnoseConfig)
