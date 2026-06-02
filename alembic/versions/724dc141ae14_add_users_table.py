"""add users table

Revision ID: 724dc141ae14
Revises: f6d406a9c7da
Create Date: 2026-06-01 22:30:44.419273

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '724dc141ae14'
down_revision: Union[str, None] = 'f6d406a9c7da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('clerk_user_id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('clerk_user_id'),
    )


def downgrade() -> None:
    op.drop_table('users')
