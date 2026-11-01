"""proactive composed_at

The optional model composer rewrites a reminder body once, just before the first
send. The column records that attempt — successful or not — so a retry and an
escalation re-send the same text instead of buying the same sentence twice.

Revision ID: fbedd668a9bd
Revises: f47576bb2aea
Create Date: 2026-11-02 01:10:23.570080

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbedd668a9bd'
down_revision: Union[str, Sequence[str], None] = 'f47576bb2aea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('proactive_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('composed_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('proactive_items', schema=None) as batch_op:
        batch_op.drop_column('composed_at')

