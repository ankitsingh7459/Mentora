"""Versioned transition audit; preserve prior review/privilege history."""
from alembic import op
import sqlalchemy as sa
revision='0006_membership_transitions'
down_revision='0005_membership_reviews'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('memberships',sa.Column('transition_version',sa.Integer(),server_default='0',nullable=False))
    op.create_check_constraint('ck_memberships_transition_version','memberships','transition_version >= 0')
    op.create_table('membership_transitions',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('institution_id',sa.Uuid(),nullable=False),
        sa.Column('target_user_id',sa.Uuid(),nullable=False),
        sa.Column('request_id',sa.Uuid(),nullable=False),
        sa.Column('actor_id',sa.Uuid(),nullable=False),
        sa.Column('operation',sa.String(16),nullable=False),
        sa.Column('from_status',sa.String(16),nullable=False),
        sa.Column('to_status',sa.String(16),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('reason',sa.String(500),nullable=False),
        sa.Column('moderator_grant_invalidated',sa.Boolean(),nullable=False),
        sa.Column('moderator_granted_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('occurred_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('institution_id','target_user_id','version',name='uq_membership_transitions_version'),
        sa.CheckConstraint('version >= 1',name='ck_membership_transitions_version'),
        sa.CheckConstraint("(operation='revoke' AND from_status='active' AND to_status='revoked') OR "
                           "(operation='reinstate' AND from_status='revoked' AND to_status='active')",name='ck_membership_transitions_states'),
        sa.CheckConstraint('char_length(reason) BETWEEN 1 AND 500',name='ck_membership_transitions_reason'),
        sa.CheckConstraint('(moderator_grant_invalidated AND moderator_granted_at IS NOT NULL) OR '
                           '(NOT moderator_grant_invalidated AND moderator_granted_at IS NULL)',name='ck_membership_transitions_moderator'))
    op.execute('ALTER TABLE membership_transitions ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON TABLE membership_transitions FROM PUBLIC')
    op.execute("""CREATE FUNCTION preserve_membership_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Membership transition event is immutable'; END; $$""")
    op.execute('CREATE TRIGGER preserve_membership_transition BEFORE UPDATE OR DELETE ON membership_transitions '
               'FOR EACH ROW EXECUTE FUNCTION preserve_membership_transition()')


def downgrade():
    op.drop_table('membership_transitions')
    op.execute('DROP FUNCTION preserve_membership_transition()')
    op.drop_constraint('ck_memberships_transition_version','memberships',type_='check')
    op.drop_column('memberships','transition_version')
