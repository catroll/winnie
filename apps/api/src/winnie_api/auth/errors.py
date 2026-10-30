"""Auth domain errors mapped to HTTP responses by the router."""

from __future__ import annotations


class AuthError(Exception):
    pass


class RegistrationClosed(AuthError):
    pass


class UsernameTaken(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


class LoginLocked(AuthError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many failed login attempts")
        self.retry_after_seconds = retry_after_seconds
