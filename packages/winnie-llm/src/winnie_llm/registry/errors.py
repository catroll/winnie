"""Errors raised while resolving a supplier model for a feature."""

from __future__ import annotations


class AIProviderError(RuntimeError):
    """A provider cannot serve a feature as configured."""


class AIConflict(AIProviderError):
    """The requested change collides with an existing record."""


class AINotFound(AIProviderError):
    """The requested integration or feature does not exist."""
