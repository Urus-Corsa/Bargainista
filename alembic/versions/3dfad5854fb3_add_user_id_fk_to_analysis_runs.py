"""add user_id fk to analysis_runs

Revision ID: 3dfad5854fb3
Revises: 724dc141ae14
Create Date: 2026-06-01 22:30:49.384584

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3dfad5854fb3'
down_revision: Union[str, None] = '724dc141ae14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analysis_runs',
        sa.Column('user_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_analysis_runs_user_id',
        'analysis_runs',
        'users',
        ['user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_analysis_runs_user_id', 'analysis_runs', type_='foreignkey')
    op.drop_column('analysis_runs', 'user_id')
