"""commerce and tracking upgrade

Revision ID: c018849be9c4
Revises: adbcdd1f8449
Create Date: 2026-09-28 17:41:03.393280

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c018849be9c4'
down_revision = 'adbcdd1f8449'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    with op.batch_alter_table('driver') as batch:
        batch.add_column(sa.Column('last_location_at', sa.DateTime()))
    with op.batch_alter_table('inventory') as batch:
        batch.add_column(sa.Column('description', sa.String(length=300)))
        batch.add_column(sa.Column('category', sa.String(length=80), nullable=False, server_default='Groceries'))
        batch.add_column(sa.Column('price_cents', sa.Integer(), nullable=False, server_default='1000'))
        batch.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    with op.batch_alter_table('order') as batch:
        batch.add_column(sa.Column('stripe_session_id', sa.String(length=255)))
        batch.add_column(sa.Column('amount_cents', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('currency', sa.String(length=3), nullable=False, server_default='usd'))
        batch.add_column(sa.Column('payment_status', sa.String(length=30), nullable=False, server_default='unpaid'))
        batch.add_column(sa.Column('paid_at', sa.DateTime()))
        batch.add_column(sa.Column('assigned_at', sa.DateTime()))
        batch.add_column(sa.Column('delivered_at', sa.DateTime()))
        batch.add_column(sa.Column('cancelled_at', sa.DateTime()))
        batch.create_unique_constraint('uq_order_stripe_session_id', ['stripe_session_id'])
    op.create_table('order_item',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('order.id'), nullable=False),
        sa.Column('inventory_id', sa.Integer(), sa.ForeignKey('inventory.id')),
        sa.Column('item_name', sa.String(length=100), nullable=False),
        sa.Column('unit_price_cents', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
    )
    op.create_index('ix_order_item_order_id', 'order_item', ['order_id'])

    # Preserve legacy orders and interpret the former mocked checkout as a
    # completed demo payment, so they remain dispatchable after this upgrade.
    op.execute(sa.text('''
        INSERT INTO order_item (order_id, inventory_id, item_name, unit_price_cents, quantity)
        SELECT id, item_id, COALESCE(item_name, 'Legacy item'), 1000, COALESCE(quantity, 1)
        FROM "order" WHERE item_name IS NOT NULL
    '''))
    op.execute(sa.text('''
        UPDATE "order"
        SET amount_cents = CASE WHEN amount > 0 THEN CAST(amount * 100 AS INTEGER)
                                ELSE COALESCE(quantity, 1) * 1000 END,
            payment_status = CASE WHEN status = 'Cancelled' THEN 'refunded' ELSE 'demo' END,
            paid_at = CASE WHEN status = 'Cancelled' THEN NULL ELSE created_at END,
            status = CASE WHEN status = 'Pending' THEN 'Paid' ELSE status END
    '''))


def downgrade():
    op.drop_index('ix_order_item_order_id', table_name='order_item')
    op.drop_table('order_item')
    with op.batch_alter_table('order') as batch:
        batch.drop_constraint('uq_order_stripe_session_id', type_='unique')
        for column in ('cancelled_at', 'delivered_at', 'assigned_at', 'paid_at',
                       'payment_status', 'currency', 'amount_cents', 'stripe_session_id'):
            batch.drop_column(column)
    with op.batch_alter_table('inventory') as batch:
        for column in ('created_at', 'is_active', 'price_cents', 'category', 'description'):
            batch.drop_column(column)
    with op.batch_alter_table('driver') as batch:
        batch.drop_column('last_location_at')
    with op.batch_alter_table('user') as batch:
        batch.drop_column('created_at')
