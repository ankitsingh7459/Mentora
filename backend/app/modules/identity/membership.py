"""Own membership requests and current-state access checks; no privilege administration."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db import get_session
from app.errors import ErrorResponse, PublicError
from app.modules.identity.models import Profile
from app.modules.identity.router import get_current_profile
from app.modules.identity.membership_models import Institution, Membership

router = APIRouter()

class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class OwnMembershipRequest(BaseModel):
    requestId: UUID
    institutionId: UUID
    status: Literal['pending', 'active', 'rejected', 'revoked']
    requestedAt: datetime

def database_failure(session):
    session.rollback()
    raise PublicError(503, 'DATABASE_UNAVAILABLE', 'Database unavailable.') from None

def pilot_institution(institution_id: UUID, request: Request, session: Session = Depends(get_session)) -> UUID:
    pilot = request.app.state.pilot_institution_id
    if pilot is None:
        raise PublicError(503, 'MEMBERSHIP_UNAVAILABLE', 'Membership is not configured.')
    try:
        if institution_id != pilot or session.get(Institution, institution_id) is None:
            raise PublicError(404, 'INSTITUTION_NOT_FOUND', 'Institution not found.')
        return institution_id
    except SQLAlchemyError:
        database_failure(session)

def lock_active_profile(profile, session):
    # Serialize request decisions, even when no membership row exists yet. Recheck blocking.
    status = session.execute(select(Profile.account_status).where(Profile.id == profile.id).with_for_update()).scalar_one()
    if status != 'active':
        raise PublicError(403, 'ACCOUNT_BLOCKED', 'Account access is blocked.')

def response_for(row):
    return OwnMembershipRequest(requestId=row.request_id, institutionId=row.institution_id,
                                status=row.status, requestedAt=row.requested_at)

@router.post('/institutions/{institution_id}/membership-requests', response_model=OwnMembershipRequest,
             status_code=201, responses={s: {'model': ErrorResponse} for s in (401,403,404,409,422,503)})
def request_membership(payload: EmptyRequest, institution_id: UUID = Depends(pilot_institution),
                       profile: Profile = Depends(get_current_profile), session: Session = Depends(get_session)):
    try:
        lock_active_profile(profile, session)
        row = session.execute(select(Membership).where(Membership.institution_id == institution_id,
                              Membership.user_id == profile.id).with_for_update()).scalar_one_or_none()
        if row is not None and row.status != 'rejected':
            code, message = {'pending': ('MEMBERSHIP_REQUEST_PENDING', 'A membership request is already pending.'),
                             'active': ('MEMBERSHIP_ALREADY_ACTIVE', 'Membership is already active.'),
                             'revoked': ('MEMBERSHIP_REINSTATEMENT_REQUIRED', 'Membership requires authorized reinstatement.')}[row.status]
            raise PublicError(409, code, message)
        if row is None:
            row = Membership(institution_id=institution_id, user_id=profile.id)
            session.add(row)
        else:
            row.status = 'pending'
            row.request_id = uuid4()
            row.requested_at = session.scalar(select(func.now()))
        session.flush()
        result = response_for(row)
        session.commit()
        return result
    except SQLAlchemyError:
        database_failure(session)

@router.get('/membership-requests/{request_id}', response_model=OwnMembershipRequest,
            responses={s: {'model': ErrorResponse} for s in (401,403,404,422,503)})
def own_request(request_id: UUID, request: Request, profile: Profile = Depends(get_current_profile),
                session: Session = Depends(get_session)):
    try:
        row = session.execute(select(Membership).where(Membership.request_id == request_id,
                              Membership.user_id == profile.id)).scalar_one_or_none()
        if row is None:
            raise PublicError(404, 'MEMBERSHIP_REQUEST_NOT_FOUND', 'Membership request not found.')
        pilot_institution(row.institution_id, request, session)
        result = response_for(row)
        session.commit()
        return result
    except SQLAlchemyError:
        database_failure(session)

def require_active_membership(institution_id: UUID = Depends(pilot_institution),
                              profile: Profile = Depends(get_current_profile),
                              session: Session = Depends(get_session)) -> Membership:
    """Depends-compatible check for {institution_id}; current membership, not moderator authority.

    No commit. Callers own transactions and must recheck/lock for sensitive writes.
    """
    try:
        row = session.execute(select(Membership).where(Membership.institution_id == institution_id,
                              Membership.user_id == profile.id, Membership.status == 'active')
                              .execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:
            raise PublicError(403, 'ACTIVE_MEMBERSHIP_REQUIRED', 'Active institution membership required.')
        return row
    except SQLAlchemyError:
        database_failure(session)
