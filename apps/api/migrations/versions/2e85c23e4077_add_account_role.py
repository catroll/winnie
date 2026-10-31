"""add the account role

Revision ID: 2e85c23e4077
Revises: c0d5c0d23f3b
Create Date: 2026-11-01 08:00:00.000000

The column is added with a server default so existing rows are backfilled as
``user``. If the table already has users but no administrator, the oldest active
account is promoted so an upgraded deployment is not left without an operator.
Take a database backup before running this on a populated database.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2e85c23e4077"
down_revision: Union[str, Sequence[str], None] = "c0d5c0d23f3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``users.role`` and promote the oldest active user when needed."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(length=16),
                nullable=False,
                server_default="user",
            )
        )

    # ``NOT EXISTS`` makes the statement safe if this revision is ever applied to
    # a table where an administrator was added out of band. Only active accounts
    # are candidates: an inactive first user cannot operate the console.
    op.execute(
        sa.text(
            """
            UPDATE users
            SET role = 'admin'
            WHERE id = (
                SELECT id FROM users
                WHERE is_active
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin')
            """
        )
    )


def downgrade() -> None:
    """Drop ``users.role``; accounts themselves are left untouched."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("role")
