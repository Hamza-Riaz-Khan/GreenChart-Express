"""legacy schema baseline

Revision ID: adbcdd1f8449
Revises: 
Create Date: 2026-09-28 17:41:02.378252

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'adbcdd1f8449'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(length=150), nullable=False),
        sa.Column('name', sa.String(length=150)),
        sa.Column('password', sa.String(length=150), nullable=False),
        sa.Column('role', sa.String(length=50)),
        sa.Column('address', sa.String(length=255)),
        sa.Column('latitude', sa.Float()),
        sa.Column('longitude', sa.Float()),
        sa.UniqueConstraint('username'),
    )
    op.create_table('inventory',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('item', sa.String(length=100)),
        sa.Column('quantity', sa.Integer(), server_default='0'),
        sa.Column('low_stock_threshold', sa.Integer(), server_default='10'),
        sa.UniqueConstraint('item'),
    )
    op.create_table('driver',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('current_lat', sa.Float()),
        sa.Column('current_lng', sa.Float()),
        sa.Column('status', sa.String(length=50), server_default='Available'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_table('order',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('inventory.id')),
        sa.Column('item_name', sa.String(length=100)),
        sa.Column('quantity', sa.Integer(), server_default='1'),
        sa.Column('status', sa.String(length=50), server_default='Pending'),
        sa.Column('driver_id', sa.Integer(), sa.ForeignKey('driver.id')),
        sa.Column('stripe_pi', sa.String(length=100)),
        sa.Column('amount', sa.Float(), server_default='0'),
        sa.Column('distance_text', sa.String(length=50)),
        sa.Column('duration_text', sa.String(length=50)),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table('delivery',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('order.id')),
        sa.Column('driver_id', sa.Integer(), sa.ForeignKey('driver.id')),
        sa.Column('delivery_date', sa.String(length=100)),
    )


def downgrade():
    op.drop_table('delivery')
    op.drop_table('order')
    op.drop_table('driver')
    op.drop_table('inventory')
    op.drop_table('user')
