"""contact birthdays

The contacts sync uploaded a contact's name, numbers, emails and organisation,
but not the birthday or anniversary its provider stores in the ``Event`` rows.
That is the one place a birthday survives when the phone does not expose a
birthday calendar, so the assistant could not answer "when is X's birthday"
from contacts at all.

The client normalises each date to ``{month, day, year?, type}``: Android writes
``YYYY-MM-DD`` or ``--MM-DD``, and the year is optional because it is optional in
the provider. These dates are Gregorian; a lunar birthday is a calendar row,
whose lunar rule the calendar sync already carries.

Revision ID: 3f2a9c1d4b67
Revises: 7a3d5c1e9b20
Create Date: 2026-11-01 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f2a9c1d4b67'
down_revision: Union[str, Sequence[str], None] = '7a3d5c1e9b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "phone_contacts",
        sa.Column(
            "birthdays",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("phone_contacts") as batch:
        batch.drop_column("birthdays")
