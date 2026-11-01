"""calendar exdate

The calendar sync carried an event's RRULE but not its EXDATE, so a series could
be expanded but not its exceptions: a cancelled occurrence of a weekly class
would still be reported. The column holds the provider's value list verbatim;
`winnie_phone.recurrence` parses it at read time.

Revision ID: 7a3d5c1e9b20
Revises: 2e85c23e4077
Create Date: 2026-11-01 20:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a3d5c1e9b20'
down_revision: Union[str, Sequence[str], None] = '2e85c23e4077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "phone_calendar_events",
        sa.Column("exdate", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("phone_calendar_events") as batch:
        batch.drop_column("exdate")
