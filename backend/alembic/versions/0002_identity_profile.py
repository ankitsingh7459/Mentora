"""Minimal own-profile/account state; no membership or privilege tables."""
from alembic import op
import sqlalchemy as sa

revision = "0002_identity_profile"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=True),
        sa.Column("account_status", sa.String(16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("account_status IN ('active', 'blocked')", name="ck_profiles_account_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Defense in depth for a Supabase database: API connections must use a trusted server role.
    op.execute("ALTER TABLE profiles ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE profiles FROM PUBLIC")


def downgrade():
    op.drop_table("profiles")
