from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Institution(Base):
    __tablename__ = 'institutions'
    id: Mapped[UUID] = mapped_column(primary_key=True)


class Membership(Base):
    __tablename__ = 'memberships'
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'rejected', 'revoked')", name='ck_memberships_status'),
        UniqueConstraint('request_id', name='uq_memberships_request_id'),
        CheckConstraint('transition_version >= 0', name='ck_memberships_transition_version'),
    )
    institution_id: Mapped[UUID] = mapped_column(ForeignKey('institutions.id'), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('profiles.id'), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(default=uuid4)
    status: Mapped[str] = mapped_column(String(16), server_default='pending')
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transition_version: Mapped[int] = mapped_column(Integer, server_default="0")
