"""battery checks use real-data model inputs

Revision ID: 8b20d7fcdc08
Revises: 95e58a85474b
Create Date: 2026-10-04 15:54:19.969529

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b20d7fcdc08'
down_revision = '95e58a85474b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('battery_checks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ambient_temperature_c', sa.Double(), nullable=False))
        batch_op.add_column(sa.Column('discharge_current_a', sa.Double(), nullable=False))
        batch_op.add_column(sa.Column('avg_voltage_v', sa.Double(), nullable=False))
        batch_op.add_column(sa.Column('max_temperature_c', sa.Double(), nullable=False))
        batch_op.drop_column('temperature_c')
        batch_op.drop_column('capacity_mah')
        batch_op.drop_column('voltage_v')



def downgrade():
    with op.batch_alter_table('battery_checks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('voltage_v', sa.DOUBLE(), nullable=False))
        batch_op.add_column(sa.Column('capacity_mah', sa.DOUBLE(), nullable=False))
        batch_op.add_column(sa.Column('temperature_c', sa.DOUBLE(), nullable=False))
        batch_op.drop_column('max_temperature_c')
        batch_op.drop_column('avg_voltage_v')
        batch_op.drop_column('discharge_current_a')
        batch_op.drop_column('ambient_temperature_c')
