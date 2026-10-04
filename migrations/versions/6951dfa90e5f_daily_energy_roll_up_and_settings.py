"""daily energy roll-up and settings

Revision ID: 6951dfa90e5f
Revises: 207795249c72
Create Date: 2026-10-04 17:18:54.233031

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6951dfa90e5f'
down_revision = '207795249c72'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.String(length=255), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('daily_energy',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('distance_km', sa.Double(), nullable=False),
    sa.Column('energy_used_kwh', sa.Double(), nullable=False),
    sa.Column('battery_charged_kwh', sa.Double(), nullable=False),
    sa.Column('grid_energy_kwh', sa.Double(), nullable=False),
    sa.Column('energy_cost_inr', sa.Double(), nullable=False),
    sa.Column('charging_cost_inr', sa.Double(), nullable=False),
    sa.Column('tariff_inr_per_kwh', sa.Double(), nullable=False),
    sa.Column('readings', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('vehicle_id', 'day', name='uq_daily_energy_vehicle_day')
    )
    with op.batch_alter_table('daily_energy', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_daily_energy_day'), ['day'], unique=False)
        batch_op.create_index(batch_op.f('ix_daily_energy_vehicle_id'), ['vehicle_id'], unique=False)



def downgrade():
    with op.batch_alter_table('daily_energy', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_daily_energy_vehicle_id'))
        batch_op.drop_index(batch_op.f('ix_daily_energy_day'))

    op.drop_table('daily_energy')
    op.drop_table('settings')
