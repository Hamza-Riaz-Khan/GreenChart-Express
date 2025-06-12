"""catalog media driver approvals and route simulation

Revision ID: 6c2660d216e8
Revises: 689bba568023
Create Date: 2026-09-28 19:10:18.721958

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6c2660d216e8'
down_revision = '689bba568023'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('approval_status', sa.String(length=20), nullable=False, server_default='approved'))
    with op.batch_alter_table('inventory') as batch:
        batch.add_column(sa.Column('image_filename', sa.String(length=255)))
        batch.add_column(sa.Column('image_url', sa.String(length=500)))
    with op.batch_alter_table('order') as batch:
        batch.add_column(sa.Column('order_number', sa.String(length=30)))
        batch.add_column(sa.Column('delivery_lat', sa.Float()))
        batch.add_column(sa.Column('delivery_lng', sa.Float()))
        batch.add_column(sa.Column('transit_started_at', sa.DateTime()))
        batch.add_column(sa.Column('route_start_lat', sa.Float()))
        batch.add_column(sa.Column('route_start_lng', sa.Float()))
        batch.add_column(sa.Column('simulation_duration_minutes', sa.Integer(), nullable=False, server_default='30'))
        batch.create_unique_constraint('uq_order_order_number', ['order_number'])
    op.execute(sa.text('''
        UPDATE "order"
        SET order_number = 'GCX-' || upper(hex(randomblob(4))),
            delivery_lat = COALESCE((SELECT latitude FROM user WHERE user.id = "order".customer_id), 24.8607),
            delivery_lng = COALESCE((SELECT longitude FROM user WHERE user.id = "order".customer_id), 67.0011)
    '''))


def downgrade():
    with op.batch_alter_table('order') as batch:
        batch.drop_constraint('uq_order_order_number', type_='unique')
        for column in ('simulation_duration_minutes', 'route_start_lng',
                       'route_start_lat', 'transit_started_at', 'delivery_lng',
                       'delivery_lat', 'order_number'):
            batch.drop_column(column)
    with op.batch_alter_table('inventory') as batch:
        batch.drop_column('image_url')
        batch.drop_column('image_filename')
    with op.batch_alter_table('user') as batch:
        batch.drop_column('approval_status')
