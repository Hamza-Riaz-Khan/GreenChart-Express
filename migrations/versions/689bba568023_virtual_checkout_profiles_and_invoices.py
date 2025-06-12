"""virtual checkout profiles and invoices

Revision ID: 689bba568023
Revises: 0e50b0615e80
Create Date: 2026-09-28 18:11:43.992103

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '689bba568023'
down_revision = '0e50b0615e80'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('email', sa.String(length=255)))
        batch.add_column(sa.Column('phone', sa.String(length=30)))
        batch.add_column(sa.Column('wallet_balance_cents', sa.Integer(), nullable=False, server_default='100000'))
        batch.create_unique_constraint('uq_user_email', ['email'])

    with op.batch_alter_table('order') as batch:
        batch.add_column(sa.Column('payment_method', sa.String(length=50)))
        batch.add_column(sa.Column('payment_reference', sa.String(length=100)))
        batch.add_column(sa.Column('delivery_name', sa.String(length=150)))
        batch.add_column(sa.Column('delivery_address', sa.String(length=300)))
        batch.add_column(sa.Column('delivery_city', sa.String(length=100)))
        batch.add_column(sa.Column('delivery_phone', sa.String(length=30)))
        batch.add_column(sa.Column('delivery_notes', sa.String(length=300)))
        batch.add_column(sa.Column('invoice_number', sa.String(length=40)))
        batch.create_unique_constraint('uq_order_invoice_number', ['invoice_number'])

    op.create_table('payment_transaction',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('order.id')),
        sa.Column('transaction_type', sa.String(length=20), nullable=False),
        sa.Column('method', sa.String(length=50), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('balance_after_cents', sa.Integer(), nullable=False),
        sa.Column('reference', sa.String(length=100), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_payment_transaction_user_id', 'payment_transaction', ['user_id'])
    op.create_index('ix_payment_transaction_order_id', 'payment_transaction', ['order_id'])

    op.execute(sa.text('''
        UPDATE "order"
        SET delivery_name = (SELECT COALESCE(name, username) FROM user WHERE user.id = "order".customer_id),
            delivery_address = (SELECT address FROM user WHERE user.id = "order".customer_id),
            delivery_city = 'Karachi',
            payment_method = CASE WHEN payment_status IN ('demo', 'paid') THEN 'demo_wallet' ELSE NULL END,
            invoice_number = 'GC-' || strftime('%Y', COALESCE(created_at, CURRENT_TIMESTAMP)) || '-' || printf('%06d', id)
    '''))


def downgrade():
    op.drop_index('ix_payment_transaction_order_id', table_name='payment_transaction')
    op.drop_index('ix_payment_transaction_user_id', table_name='payment_transaction')
    op.drop_table('payment_transaction')
    with op.batch_alter_table('order') as batch:
        batch.drop_constraint('uq_order_invoice_number', type_='unique')
        for column in ('invoice_number', 'delivery_notes', 'delivery_phone',
                       'delivery_city', 'delivery_address', 'delivery_name',
                       'payment_reference', 'payment_method'):
            batch.drop_column(column)
    with op.batch_alter_table('user') as batch:
        batch.drop_constraint('uq_user_email', type_='unique')
        batch.drop_column('wallet_balance_cents')
        batch.drop_column('phone')
        batch.drop_column('email')
