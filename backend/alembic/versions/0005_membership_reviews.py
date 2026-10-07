"""Immutable audit of pending membership request decisions only."""
from alembic import op
import sqlalchemy as sa
revision='0005_membership_reviews'
down_revision='0004_administration_prerequisite'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('membership_reviews',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('request_id',sa.Uuid(),nullable=False),
        sa.Column('institution_id',sa.Uuid(),nullable=False),
        sa.Column('actor_id',sa.Uuid(),nullable=False),
        sa.Column('target_user_id',sa.Uuid(),nullable=False),
        sa.Column('decision',sa.String(16),nullable=False),
        sa.Column('reason',sa.String(500),nullable=True),
        sa.Column('decided_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('request_id',name='uq_membership_reviews_request_id'),
        sa.CheckConstraint("decision IN ('approve','reject')",name='ck_membership_reviews_decision'),
        sa.CheckConstraint('reason IS NULL OR char_length(reason) BETWEEN 1 AND 500',name='ck_membership_reviews_reason'))
    op.execute('ALTER TABLE membership_reviews ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON TABLE membership_reviews FROM PUBLIC')
    op.execute("""CREATE FUNCTION preserve_membership_review() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Membership review event is immutable'; END; $$""")
    op.execute('CREATE TRIGGER preserve_membership_review BEFORE UPDATE OR DELETE ON membership_reviews '
               'FOR EACH ROW EXECUTE FUNCTION preserve_membership_review()')


def downgrade():
    op.drop_table('membership_reviews')
    op.execute('DROP FUNCTION preserve_membership_review()')
