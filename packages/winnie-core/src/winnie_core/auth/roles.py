"""Authorization roles for accounts.

The role is stored as a short string on ``users`` so a future role can be added
without a schema change. ``admin`` owns operator-only routes; ``user`` owns
their own data. The API is the authority: the console may hide navigation for a
role, but every operator route checks the role server-side on each request.
"""

from __future__ import annotations

from typing import Literal

ROLE_ADMIN = "admin"
ROLE_USER = "user"

Role = Literal["admin", "user"]

ROLES: frozenset[str] = frozenset({ROLE_ADMIN, ROLE_USER})
