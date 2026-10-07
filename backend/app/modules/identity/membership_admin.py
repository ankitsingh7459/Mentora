"""Scoped current request administration. No role management or content moderation."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db import get_session
from app.errors import ErrorResponse, PublicError
from app.modules.identity.models import Profile
from app.modules.identity.router import get_current_profile
from app.modules.identity.membership import EmptyRequest, database_failure, lock_active_profile, pilot_institution, require_active_membership
from app.modules.identity.membership_models import Membership
from app.modules.identity.roles import require_platform_administrator, require_institution_moderator
from app.modules.identity.role_models import PlatformAdministrator, InstitutionModerator
from app.modules.identity.membership_review_models import MembershipReview

router=APIRouter()


class RequestListQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: Literal['pending','active','rejected','revoked','all']='pending'
    limit: int=Field(default=20,ge=1,le=100)
    offset: int=Field(default=0,ge=0,le=10000)


class RequestDecision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    decision: Literal['approve','reject']
    reason: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=500)] | None=None


class AdminMembershipRequest(BaseModel):
    requestId: UUID
    institutionId: UUID
    userId: UUID
    status: Literal['pending','active','rejected','revoked']
    requestedAt: datetime


class ReviewRecord(BaseModel):
    eventId: UUID
    actorId: UUID
    decision: Literal['approve','reject']
    reason: str | None
    decidedAt: datetime


class AdminRequestDetail(AdminMembershipRequest):
    review: ReviewRecord | None


class RequestPage(BaseModel):
    items: list[AdminMembershipRequest]
    limit: int
    offset: int
    hasMore: bool


def require_membership_reviewer(institution_id: UUID=Depends(pilot_institution),
                                profile: Profile=Depends(get_current_profile),
                                session: Session=Depends(get_session)) -> Profile:
    # This OR is only the approved membership-administration policy, not content access.
    try:
        require_platform_administrator(profile,session)
    except PublicError as error:
        if error.code!='PLATFORM_ADMINISTRATOR_REQUIRED':
            raise
        membership=require_active_membership(institution_id,profile,session)
        require_institution_moderator(membership,session)
    return profile


def lock_reviewer_authority(institution_id,reviewer,session):
    lock_active_profile(reviewer,session)
    admin=session.execute(select(PlatformAdministrator).where(
        PlatformAdministrator.user_id==reviewer.id).with_for_update(read=True)).scalar_one_or_none()
    if admin is None:
        session.execute(select(Membership).where(Membership.institution_id==institution_id,
            Membership.user_id==reviewer.id).with_for_update(read=True)).scalar_one_or_none()
        session.execute(select(InstitutionModerator).where(InstitutionModerator.institution_id==institution_id,
            InstitutionModerator.user_id==reviewer.id).with_for_update(read=True)).scalar_one_or_none()
        membership=require_active_membership(institution_id,reviewer,session)
        require_institution_moderator(membership,session)
    else:
        require_platform_administrator(reviewer,session)


def request_view(row):
    return AdminMembershipRequest(requestId=row.request_id,institutionId=row.institution_id,
        userId=row.user_id,status=row.status,requestedAt=row.requested_at)


def detail_view(row,event):
    review=None if event is None else ReviewRecord(eventId=event.id,actorId=event.actor_id,
        decision=event.decision,reason=event.reason,decidedAt=event.decided_at)
    return AdminRequestDetail(**request_view(row).model_dump(),review=review)


@router.get('/institutions/{institution_id}/membership-requests',response_model=RequestPage,
            responses={s:{'model':ErrorResponse} for s in (401,403,404,422,503)})
def list_requests(query: Annotated[RequestListQuery,Query()],institution_id: UUID=Depends(pilot_institution),
                  reviewer: Profile=Depends(require_membership_reviewer),session: Session=Depends(get_session)):
    try:
        statement=select(Membership).where(Membership.institution_id==institution_id)
        if query.status!='all':statement=statement.where(Membership.status==query.status)
        rows=session.scalars(statement.order_by(Membership.requested_at,Membership.request_id)
                             .offset(query.offset).limit(query.limit+1)).all()
        response=RequestPage(items=[request_view(row) for row in rows[:query.limit]],limit=query.limit,
                             offset=query.offset,hasMore=len(rows)>query.limit)
        session.commit()
        return response
    except SQLAlchemyError:
        database_failure(session)


@router.get('/institutions/{institution_id}/membership-requests/{request_id}',response_model=AdminRequestDetail,
            responses={s:{'model':ErrorResponse} for s in (401,403,404,422,503)})
def request_detail(query: Annotated[EmptyRequest,Query()],request_id: UUID,institution_id: UUID=Depends(pilot_institution),
                   reviewer: Profile=Depends(require_membership_reviewer),session: Session=Depends(get_session)):
    try:
        row=session.scalar(select(Membership).where(Membership.institution_id==institution_id,
                                                    Membership.request_id==request_id))
        if row is None:
            raise PublicError(404,'MEMBERSHIP_REQUEST_NOT_FOUND','Membership request not found.')
        event=session.scalar(select(MembershipReview).where(MembershipReview.institution_id==institution_id,
                                                           MembershipReview.request_id==request_id))
        response=detail_view(row,event)
        session.commit()
        return response
    except SQLAlchemyError:
        database_failure(session)


@router.post('/institutions/{institution_id}/membership-requests/{request_id}/decision',response_model=AdminRequestDetail,
             responses={s:{'model':ErrorResponse} for s in (401,403,404,409,422,503)})
def decide_request(query: Annotated[EmptyRequest,Query()],request_id: UUID,payload: RequestDecision,institution_id: UUID=Depends(pilot_institution),
                   reviewer: Profile=Depends(require_membership_reviewer),session: Session=Depends(get_session)):
    try:
        lock_reviewer_authority(institution_id,reviewer,session)
        row=session.execute(select(Membership).where(Membership.institution_id==institution_id,
            Membership.request_id==request_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None or row.status!='pending':
            raise PublicError(409,'MEMBERSHIP_REQUEST_NOT_PENDING','Request is not the current pending membership request.')
        if payload.decision=='approve' and row.user_id==reviewer.id:
            raise PublicError(403,'SELF_APPROVAL_FORBIDDEN','You cannot approve your own membership request.')
        row.status='active' if payload.decision=='approve' else 'rejected'
        event=MembershipReview(request_id=row.request_id,institution_id=institution_id,actor_id=reviewer.id,
                               target_user_id=row.user_id,decision=payload.decision,reason=payload.reason)
        session.add(event)
        session.flush()
        response=detail_view(row,event)
        session.commit()
        return response
    except SQLAlchemyError:
        database_failure(session)
