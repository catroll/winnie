"""proactive items

One row per (subject, occurrence, stage): the record that something was noticed,
what was decided, what was said, on which channels, and whether the owner
acknowledged it. Push is an acceleration; this table is the truth, so a restart
never forgets an intervention that was due.

Timestamps are naive UTC like the rest of the database. The unique key makes the
planner idempotent: a re-scan updates an unsent item instead of duplicating it.

Revision ID: f47576bb2aea
Revises: 3f2a9c1d4b67
Create Date: 2026-11-02 00:17:35.119687

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f47576bb2aea'
down_revision: Union[str, Sequence[str], None] = '3f2a9c1d4b67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('proactive_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('subject_key', sa.String(length=256), nullable=False),
    sa.Column('occurrence_key', sa.String(length=32), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('actions', sa.JSON(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('planned_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('channels', sa.JSON(), nullable=False),
    sa.Column('conversation_id', sa.String(length=64), nullable=False),
    sa.Column('ack_token', sa.String(length=64), nullable=False),
    sa.Column('acked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('acked_via', sa.String(length=32), nullable=False),
    sa.Column('feedback', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'subject_key', 'occurrence_key', 'stage', name='uq_proactive_subject_occurrence_stage')
    )
    with op.batch_alter_table('proactive_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_proactive_items_ack_token'), ['ack_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_proactive_items_planned_at'), ['planned_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_proactive_items_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_proactive_items_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('proactive_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_proactive_items_user_id'))
        batch_op.drop_index(batch_op.f('ix_proactive_items_status'))
        batch_op.drop_index(batch_op.f('ix_proactive_items_planned_at'))
        batch_op.drop_index(batch_op.f('ix_proactive_items_ack_token'))

    op.drop_table('proactive_items')
