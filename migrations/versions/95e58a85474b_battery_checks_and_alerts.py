"""battery checks and alerts

Revision ID: 95e58a85474b
Revises: 29e9c9819d7a
Create Date: 2026-10-04 15:23:52.645392

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '95e58a85474b'
down_revision = '29e9c9819d7a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('alerts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('alert_type', sa.Enum('low_battery', 'battery_overheat', 'battery_degraded', name='alert_type'), nullable=False),
    sa.Column('severity', sa.Enum('warning', 'critical', name='alert_severity'), nullable=False),
    sa.Column('message', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
    sa.Column('acknowledged_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['acknowledged_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alerts_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_alerts_vehicle_id'), ['vehicle_id'], unique=False)
        batch_op.create_index('uq_alerts_open_vehicle_type', ['vehicle_id', 'alert_type'], unique=True, sqlite_where=sa.text('resolved_at IS NULL'), postgresql_where=sa.text('resolved_at IS NULL'))

    op.create_table('battery_checks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('capacity_mah', sa.Double(), nullable=False),
    sa.Column('cycle_count', sa.Double(), nullable=False),
    sa.Column('voltage_v', sa.Double(), nullable=False),
    sa.Column('temperature_c', sa.Double(), nullable=False),
    sa.Column('internal_resistance_mohm', sa.Double(), nullable=False),
    sa.Column('predicted_soh_pct', sa.Double(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('model_name', sa.String(length=40), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('battery_checks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_battery_checks_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_battery_checks_vehicle_id'), ['vehicle_id'], unique=False)



def downgrade():
    with op.batch_alter_table('battery_checks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_battery_checks_vehicle_id'))
        batch_op.drop_index(batch_op.f('ix_battery_checks_created_at'))

    op.drop_table('battery_checks')
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.drop_index('uq_alerts_open_vehicle_type', sqlite_where=sa.text('resolved_at IS NULL'), postgresql_where=sa.text('resolved_at IS NULL'))
        batch_op.drop_index(batch_op.f('ix_alerts_vehicle_id'))
        batch_op.drop_index(batch_op.f('ix_alerts_created_at'))

    op.drop_table('alerts')
