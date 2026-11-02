"""error incidents

One row per distinct failure shape, not per occurrence: the fingerprint is the
dedupe key, ``occurrences`` and ``last_seen`` move when the same failure repeats,
and the triage columns record what the owner decided. A diagnosis is stored on
the row it explains, with the model that produced it, because the evidence it
was built from stops existing as soon as the logs rotate.

``fingerprint`` is unique for the same reason: the loser of a concurrent insert
must be an update, not a second row. Timestamps are naive UTC like the rest of
the database.

Revision ID: b7c1e4d92a30
Revises: 1e9c3d5a7b42
Create Date: 2026-11-03 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c1e4d92a30'
down_revision: Union[str, Sequence[str], None] = '1e9c3d5a7b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('error_incidents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('service', sa.String(length=32), nullable=False),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('message', sa.String(length=1000), nullable=False),
    sa.Column('exception_type', sa.String(length=200), nullable=False),
    sa.Column('location', sa.String(length=400), nullable=False),
    sa.Column('traceback', sa.Text(), nullable=False),
    sa.Column('context', sa.JSON(), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('first_seen', sa.DateTime(), nullable=False),
    sa.Column('last_seen', sa.DateTime(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
    sa.Column('ignored_at', sa.DateTime(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('diagnosis', sa.JSON(), nullable=True),
    sa.Column('diagnosis_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_error_incidents_fingerprint'), 'error_incidents', ['fingerprint'], unique=True)
    op.create_index(op.f('ix_error_incidents_last_seen'), 'error_incidents', ['last_seen'], unique=False)
    op.create_index(op.f('ix_error_incidents_service'), 'error_incidents', ['service'], unique=False)
    op.create_index(op.f('ix_error_incidents_source'), 'error_incidents', ['source'], unique=False)
    op.create_index(op.f('ix_error_incidents_status'), 'error_incidents', ['status'], unique=False)
    op.create_index('ix_error_incidents_status_last_seen', 'error_incidents', ['status', 'last_seen'], unique=False)
    op.create_index('ix_error_incidents_service_last_seen', 'error_incidents', ['service', 'last_seen'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_error_incidents_service_last_seen', table_name='error_incidents')
    op.drop_index('ix_error_incidents_status_last_seen', table_name='error_incidents')
    op.drop_index(op.f('ix_error_incidents_status'), table_name='error_incidents')
    op.drop_index(op.f('ix_error_incidents_source'), table_name='error_incidents')
    op.drop_index(op.f('ix_error_incidents_service'), table_name='error_incidents')
    op.drop_index(op.f('ix_error_incidents_last_seen'), table_name='error_incidents')
    op.drop_index(op.f('ix_error_incidents_fingerprint'), table_name='error_incidents')
    op.drop_table('error_incidents')
