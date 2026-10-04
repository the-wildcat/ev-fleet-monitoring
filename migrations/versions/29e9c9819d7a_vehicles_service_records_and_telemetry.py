"""vehicles, service records and telemetry

Revision ID: 29e9c9819d7a
Revises: 9377cb48970d
Create Date: 2026-10-04 14:57:26.674513

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '29e9c9819d7a'
down_revision = '9377cb48970d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('make', sa.String(length=60), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('plate_number', sa.String(length=15), nullable=False),
    sa.Column('vin', sa.String(length=17), nullable=True),
    sa.Column('battery_capacity_kwh', sa.Double(), nullable=False),
    sa.Column('efficiency_km_per_kwh', sa.Double(), nullable=False),
    sa.Column('status', sa.Enum('active', 'in_service', 'inactive', name='vehicle_status'), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('simulated', sa.Boolean(), nullable=False),
    sa.Column('api_key_hash', sa.String(length=64), nullable=True),
    sa.Column('api_key_prefix', sa.String(length=8), nullable=True),
    sa.Column('last_lat', sa.Double(), nullable=True),
    sa.Column('last_lon', sa.Double(), nullable=True),
    sa.Column('last_speed_kmh', sa.Double(), nullable=True),
    sa.Column('last_soc_pct', sa.Double(), nullable=True),
    sa.Column('last_is_charging', sa.Boolean(), nullable=True),
    sa.Column('last_battery_temp_c', sa.Double(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(), nullable=True),
    sa.Column('odometer_km', sa.Double(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['driver_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('vin')
    )
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vehicles_api_key_hash'), ['api_key_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_vehicles_plate_number'), ['plate_number'], unique=True)

    op.create_table('service_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('service_date', sa.Date(), nullable=False),
    sa.Column('service_type', sa.Enum('routine', 'battery', 'tyres', 'brakes', 'repair', 'other', name='service_type'), nullable=False),
    sa.Column('odometer_km', sa.Double(), nullable=True),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('cost_inr', sa.Double(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('service_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_service_records_vehicle_id'), ['vehicle_id'], unique=False)

    op.create_table('telemetry',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('lat', sa.Double(), nullable=False),
    sa.Column('lon', sa.Double(), nullable=False),
    sa.Column('speed_kmh', sa.Double(), nullable=False),
    sa.Column('soc_pct', sa.Double(), nullable=False),
    sa.Column('is_charging', sa.Boolean(), nullable=False),
    sa.Column('battery_temp_c', sa.Double(), nullable=True),
    sa.Column('odometer_km', sa.Double(), nullable=True),
    sa.Column('acceleration_mps2', sa.Double(), nullable=True),
    sa.Column('power_kw', sa.Double(), nullable=True),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('telemetry', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_telemetry_recorded_at'), ['recorded_at'], unique=False)
        batch_op.create_index('ix_telemetry_vehicle_time', ['vehicle_id', 'recorded_at'], unique=False)



def downgrade():
    with op.batch_alter_table('telemetry', schema=None) as batch_op:
        batch_op.drop_index('ix_telemetry_vehicle_time')
        batch_op.drop_index(batch_op.f('ix_telemetry_recorded_at'))

    op.drop_table('telemetry')
    with op.batch_alter_table('service_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_service_records_vehicle_id'))

    op.drop_table('service_records')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vehicles_plate_number'))
        batch_op.drop_index(batch_op.f('ix_vehicles_api_key_hash'))

    op.drop_table('vehicles')
