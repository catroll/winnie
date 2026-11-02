"""mail cache

The mailbox gains a local mirror: one row per folder with the cursor that makes
the next sync incremental (``uid_validity`` + ``last_uid`` for IMAP; POP3 uses
the cached uids themselves), and one row per message header. A message body is
stored only when someone opens it, so the cache cannot grow to the size of the
mailbox.

Revision ID: 6af3fee5df7e
Revises: fbedd668a9bd
Create Date: 2026-11-02 22:58:36.617718

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6af3fee5df7e'
down_revision: Union[str, Sequence[str], None] = 'fbedd668a9bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'mail_folders',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.String(length=128), nullable=False),
        sa.Column('protocol', sa.String(length=8), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('delimiter', sa.String(length=8), nullable=False),
        sa.Column('selectable', sa.Boolean(), nullable=False),
        sa.Column('uid_validity', sa.Integer(), nullable=False),
        sa.Column('last_uid', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('unread', sa.Integer(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'protocol', 'name', name='uq_mail_folders_account_folder'
        ),
    )
    with op.batch_alter_table('mail_folders', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_mail_folders_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            'ix_mail_folders_user_synced', ['user_id', 'last_synced_at'], unique=False
        )

    op.create_table(
        'mail_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.String(length=128), nullable=False),
        sa.Column('protocol', sa.String(length=8), nullable=False),
        sa.Column('folder', sa.String(length=255), nullable=False),
        sa.Column('uid', sa.String(length=128), nullable=False),
        sa.Column('message_id', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=False),
        sa.Column('sender', sa.String(length=500), nullable=False),
        sa.Column('recipients', sa.String(length=1000), nullable=False),
        sa.Column('date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('snippet', sa.String(length=500), nullable=False),
        sa.Column('unread', sa.Boolean(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('body_fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cached_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'protocol',
            'folder',
            'uid',
            name='uq_mail_messages_account_message',
        ),
    )
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_mail_messages_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            'ix_mail_messages_user_date', ['user_id', 'date'], unique=False
        )
        batch_op.create_index(
            'ix_mail_messages_user_folder',
            ['user_id', 'protocol', 'folder'],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.drop_index('ix_mail_messages_user_folder')
        batch_op.drop_index('ix_mail_messages_user_date')
        batch_op.drop_index(batch_op.f('ix_mail_messages_user_id'))
    op.drop_table('mail_messages')
    with op.batch_alter_table('mail_folders', schema=None) as batch_op:
        batch_op.drop_index('ix_mail_folders_user_synced')
        batch_op.drop_index(batch_op.f('ix_mail_folders_user_id'))
    op.drop_table('mail_folders')
