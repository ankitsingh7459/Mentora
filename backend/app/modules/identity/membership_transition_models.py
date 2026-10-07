"""Immutable transition audit; no contributor workflow or role appointments."""
from datetime import datetime
from uuid import UUID,uuid4
from sqlalchemy import Boolean,CheckConstraint,DateTime,Integer,String,UniqueConstraint,func
from sqlalchemy.orm import Mapped,mapped_column
from app.db import Base


class MembershipTransition(Base):
    __tablename__='membership_transitions'
    __table_args__=(
        UniqueConstraint('institution_id','target_user_id','version',name='uq_membership_transitions_version'),
        CheckConstraint('version >= 1',name='ck_membership_transitions_version'),
        CheckConstraint("(operation='revoke' AND from_status='active' AND to_status='revoked') OR "
                        "(operation='reinstate' AND from_status='revoked' AND to_status='active')",name='ck_membership_transitions_states'),
        CheckConstraint('char_length(reason) BETWEEN 1 AND 500',name='ck_membership_transitions_reason'),
        CheckConstraint('(moderator_grant_invalidated AND moderator_granted_at IS NOT NULL) OR '
                        '(NOT moderator_grant_invalidated AND moderator_granted_at IS NULL)',name='ck_membership_transitions_moderator'),
    )
    id: Mapped[UUID]=mapped_column(primary_key=True,default=uuid4)
    institution_id: Mapped[UUID]=mapped_column()
    target_user_id: Mapped[UUID]=mapped_column()
    request_id: Mapped[UUID]=mapped_column()
    actor_id: Mapped[UUID]=mapped_column()
    operation: Mapped[str]=mapped_column(String(16))
    from_status: Mapped[str]=mapped_column(String(16))
    to_status: Mapped[str]=mapped_column(String(16))
    version: Mapped[int]=mapped_column(Integer)
    reason: Mapped[str]=mapped_column(String(500))
    moderator_grant_invalidated: Mapped[bool]=mapped_column(Boolean)
    moderator_granted_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    occurred_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
