"""normalize order timestamps

Revision ID: 0e50b0615e80
Revises: c018849be9c4
Create Date: 2026-09-28 17:45:39.882904

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0e50b0615e80'
down_revision = 'c018849be9c4'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text('UPDATE "order" SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL'))
    with op.batch_alter_table('order') as batch:
        batch.alter_column('created_at', existing_type=sa.DateTime(), nullable=False)


def downgrade():
    with op.batch_alter_table('order') as batch:
        batch.alter_column('created_at', existing_type=sa.DateTime(), nullable=True)
