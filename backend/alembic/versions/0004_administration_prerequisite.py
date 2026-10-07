"""Current privilege grants and one-time immutable administrator bootstrap event."""
from alembic import op
import sqlalchemy as sa
revision = '0004_administration_prerequisite'
down_revision = '0003_membership_requests'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('platform_administrators',
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('profiles.id'),primary_key=True),
        sa.Column('granted_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_table('institution_moderators',
        sa.Column('institution_id',sa.Uuid(),primary_key=True),
        sa.Column('user_id',sa.Uuid(),primary_key=True),
        sa.Column('granted_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.ForeignKeyConstraint(['institution_id','user_id'],['memberships.institution_id','memberships.user_id']))
    op.create_table('administrator_bootstrap',
        sa.Column('singleton',sa.Integer(),primary_key=True),
        sa.Column('target_user_id',sa.Uuid(),nullable=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('actor',sa.String(32),nullable=False),
        sa.Column('reason',sa.String(128),nullable=False),
        sa.CheckConstraint('singleton = 1',name='ck_administrator_bootstrap_singleton'))
    for table in ('platform_administrators','institution_moderators','administrator_bootstrap'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE {table} FROM PUBLIC')
    op.execute("""CREATE FUNCTION preserve_administrator_bootstrap() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Administrator bootstrap event is immutable'; END; $$""")
    op.execute('CREATE TRIGGER preserve_administrator_bootstrap BEFORE UPDATE OR DELETE ON administrator_bootstrap '
               'FOR EACH ROW EXECUTE FUNCTION preserve_administrator_bootstrap()')


def downgrade():
    op.drop_table('administrator_bootstrap')
    op.execute('DROP FUNCTION preserve_administrator_bootstrap()')
    op.drop_table('institution_moderators')
    op.drop_table('platform_administrators')
