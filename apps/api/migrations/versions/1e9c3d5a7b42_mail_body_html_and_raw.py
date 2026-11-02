"""mail body html and raw source

The reading pane shows a message three ways: the flattened text the assistant
reads, the sender's HTML, and the RFC 822 source. The first already lived in
``body``; the other two arrive with the same download, so they are stored next
to it rather than refetched on every switch between tabs.

Both columns are nullable and NULL means "body not downloaded yet", exactly like
``body``: the sweep only writes headers.

Revision ID: 1e9c3d5a7b42
Revises: 6af3fee5df7e
Create Date: 2026-11-02 23:41:07.118452

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e9c3d5a7b42'
down_revision: Union[str, Sequence[str], None] = '6af3fee5df7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('body_html', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('body_raw', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.drop_column('body_raw')
        batch_op.drop_column('body_html')
