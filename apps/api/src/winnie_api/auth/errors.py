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
