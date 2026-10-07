"""Immutable pending-request decisions; independent of replaceable current request rows."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class MembershipReview(Base):
    __tablename__ = 'membership_reviews'
    __table_args__ = (
        UniqueConstraint('request_id', name='uq_membership_reviews_request_id'),
        CheckConstraint("decision IN ('approve','reject')", name='ck_membership_reviews_decision'),
        CheckConstraint('reason IS NULL OR char_length(reason) BETWEEN 1 AND 500', name='ck_membership_reviews_reason'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column()
    institution_id: Mapped[UUID] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column()
    target_user_id: Mapped[UUID] = mapped_column()
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
