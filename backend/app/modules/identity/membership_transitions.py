"""Revoke/reinstate student membership under approved target restrictions."""
from datetime import datetime
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,ConfigDict,Field,StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db import get_session
from app.errors import ErrorResponse,PublicError
from app.modules.identity.models import Profile
from app.modules.identity.membership import EmptyRequest,database_failure,pilot_institution
from app.modules.identity.membership_admin import require_membership_reviewer,lock_reviewer_authority
from app.modules.identity.membership_models import Membership
from app.modules.identity.role_models import PlatformAdministrator,InstitutionModerator
from app.modules.identity.roles import require_platform_administrator
from app.modules.identity.membership_transition_models import MembershipTransition

router=APIRouter()


class TransitionInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    reason: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=500)]
    expectedVersion: Annotated[int,Field(ge=0)]


class MembershipState(BaseModel):
    institutionId: UUID
    userId: UUID
    requestId: UUID
    status: Literal['pending','active','rejected','revoked']
    version: int


class TransitionRecord(BaseModel):
    eventId: UUID
    actorId: UUID
    operation: Literal['revoke','reinstate']
    reason: str
    occurredAt: datetime
    moderatorGrantInvalidated: bool


class TransitionResult(MembershipState):
    transition: TransitionRecord


def state_view(row):
    return MembershipState(institutionId=row.institution_id,userId=row.user_id,requestId=row.request_id,
                           status=row.status,version=row.transition_version)


@router.get('/institutions/{institution_id}/memberships/{user_id}',response_model=MembershipState,
            responses={s:{'model':ErrorResponse} for s in (401,403,404,422,503)})
def membership_state(query: Annotated[EmptyRequest,Query()],user_id: UUID,
                     institution_id: UUID=Depends(pilot_institution),
                     actor: Profile=Depends(require_membership_reviewer),session: Session=Depends(get_session)):
    try:
        row=session.scalar(select(Membership).where(Membership.institution_id==institution_id,Membership.user_id==user_id))
        if row is None:raise PublicError(404,'MEMBERSHIP_NOT_FOUND','Membership not found.')
        result=state_view(row)
        session.commit()
        return result
    except SQLAlchemyError:
        database_failure(session)


def transition_membership(operation,user_id,institution_id,payload,actor,session):
    try:
        if user_id==actor.id:
            raise PublicError(403,'SELF_MEMBERSHIP_ACTION_FORBIDDEN','You cannot revoke or reinstate your own membership.')
        # Global order prevents actor/target profile lock inversion between transitions.
        locked_profiles=session.scalars(select(Profile.id).where(Profile.id.in_([actor.id,user_id]))
                                        .order_by(Profile.id).with_for_update()).all()
        if user_id not in locked_profiles:
            raise PublicError(404,'MEMBERSHIP_NOT_FOUND','Membership not found.')
        lock_reviewer_authority(institution_id,actor,session)
        target_admin=session.scalar(select(PlatformAdministrator).where(PlatformAdministrator.user_id==user_id)
                                    .with_for_update(read=True))
        if target_admin is not None:
            raise PublicError(403,'PLATFORM_ADMINISTRATOR_TARGET_FORBIDDEN','Platform administrator targets are outside this operation.')
        row=session.execute(select(Membership).where(Membership.institution_id==institution_id,Membership.user_id==user_id)
                            .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:raise PublicError(404,'MEMBERSHIP_NOT_FOUND','Membership not found.')
        moderator=session.scalar(select(InstitutionModerator).where(InstitutionModerator.institution_id==institution_id,
                                                                    InstitutionModerator.user_id==user_id).with_for_update())
        if moderator is not None:require_platform_administrator(actor,session)
        if row.transition_version!=payload.expectedVersion:
            raise PublicError(409,'MEMBERSHIP_VERSION_CONFLICT','Membership version changed; refresh current state.')
        before,after=('active','revoked') if operation=='revoke' else ('revoked','active')
        if row.status!=before:
            raise PublicError(409,'MEMBERSHIP_TRANSITION_CONFLICT','Membership is not in the required state for this transition.')
        row.status=after
        row.transition_version+=1
        # Contributor grants do not exist. Future work must invalidate them in this transaction.
        # Clear stale moderator grants on reinstate too: reinstatement must be student-only.
        event=MembershipTransition(institution_id=institution_id,target_user_id=user_id,request_id=row.request_id,
            actor_id=actor.id,operation=operation,from_status=before,to_status=after,version=row.transition_version,
            reason=payload.reason,moderator_grant_invalidated=moderator is not None,
            moderator_granted_at=None if moderator is None else moderator.granted_at)
        if moderator is not None:session.delete(moderator)
        session.add(event)
        session.flush()
        result=TransitionResult(**state_view(row).model_dump(),transition=TransitionRecord(
            eventId=event.id,actorId=event.actor_id,operation=event.operation,reason=event.reason,
            occurredAt=event.occurred_at,moderatorGrantInvalidated=event.moderator_grant_invalidated))
        session.commit()
        return result
    except SQLAlchemyError:
        database_failure(session)


@router.post('/institutions/{institution_id}/memberships/{user_id}/revoke',response_model=TransitionResult,
             responses={s:{'model':ErrorResponse} for s in (401,403,404,409,422,503)})
def revoke(query: Annotated[EmptyRequest,Query()],user_id: UUID,payload: TransitionInput,
           institution_id: UUID=Depends(pilot_institution),actor: Profile=Depends(require_membership_reviewer),
           session: Session=Depends(get_session)):
    return transition_membership('revoke',user_id,institution_id,payload,actor,session)


@router.post('/institutions/{institution_id}/memberships/{user_id}/reinstate',response_model=TransitionResult,
             responses={s:{'model':ErrorResponse} for s in (401,403,404,409,422,503)})
def reinstate(query: Annotated[EmptyRequest,Query()],user_id: UUID,payload: TransitionInput,
              institution_id: UUID=Depends(pilot_institution),actor: Profile=Depends(require_membership_reviewer),
              session: Session=Depends(get_session)):
    return transition_membership('reinstate',user_id,institution_id,payload,actor,session)
