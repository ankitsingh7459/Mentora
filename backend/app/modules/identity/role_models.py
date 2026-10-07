"""Only current administrator/moderator grants and a durable bootstrap event."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
# Register referenced tables even when only the standalone operator module is imported.
from app.modules.identity.models import Profile
from app.modules.identity.membership_models import Membership


class PlatformAdministrator(Base):
    __tablename__ = 'platform_administrators'
    user_id: Mapped[UUID] = mapped_column(ForeignKey('profiles.id'), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstitutionModerator(Base):
    __tablename__ = 'institution_moderators'
    __table_args__ = (ForeignKeyConstraint(['institution_id', 'user_id'],
                                          ['memberships.institution_id', 'memberships.user_id']),)
    institution_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdministratorBootstrap(Base):
    __tablename__ = 'administrator_bootstrap'
    __table_args__ = (CheckConstraint('singleton = 1', name='ck_administrator_bootstrap_singleton'),)
    singleton: Mapped[int] = mapped_column(primary_key=True)
    # Deliberately no FK: deleting a grant/profile must never reopen bootstrap.
    target_user_id: Mapped[UUID] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(128))
