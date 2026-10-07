"""Pilot institution and current membership/request state only."""
from alembic import op
import sqlalchemy as sa
revision = '0003_membership_requests'
down_revision = '0002_identity_profile'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('institutions', sa.Column('id', sa.Uuid(), primary_key=True))
    op.create_table('memberships',
        sa.Column('institution_id', sa.Uuid(), sa.ForeignKey('institutions.id'), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('profiles.id'), primary_key=True),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(16), server_default='pending', nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'active', 'rejected', 'revoked')", name='ck_memberships_status'),
        sa.UniqueConstraint('request_id', name='uq_memberships_request_id'))
    for table in ('institutions', 'memberships'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE {table} FROM PUBLIC')


def downgrade():
    op.drop_table('memberships')
    op.drop_table('institutions')
