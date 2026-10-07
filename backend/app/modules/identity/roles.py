"""Current database grants; no metadata roles or institution-content bypass."""
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db import get_session
from app.errors import PublicError
from app.modules.identity.models import Profile
from app.modules.identity.router import get_current_profile
from app.modules.identity.membership import database_failure, require_active_membership
from app.modules.identity.membership_models import Membership
from app.modules.identity.role_models import InstitutionModerator, PlatformAdministrator


def require_platform_administrator(profile: Profile = Depends(get_current_profile),
                                   session: Session = Depends(get_session)) -> PlatformAdministrator:
    try:
        grant = session.execute(select(PlatformAdministrator).join(Profile).where(
            PlatformAdministrator.user_id == profile.id, Profile.account_status == 'active'
        ).execution_options(populate_existing=True)).scalar_one_or_none()
        if grant is None:
            raise PublicError(403, 'PLATFORM_ADMINISTRATOR_REQUIRED', 'Platform administrator permission required.')
        return grant
    except SQLAlchemyError:
        database_failure(session)


def require_institution_moderator(membership: Membership = Depends(require_active_membership),
                                 session: Session = Depends(get_session)) -> InstitutionModerator:
    try:
        # Recheck the account/membership in this SELECT rather than trusting a cached ORM state.
        grant = session.execute(select(InstitutionModerator).join(Membership).join(
            Profile, Profile.id == Membership.user_id).where(
            InstitutionModerator.institution_id == membership.institution_id,
            InstitutionModerator.user_id == membership.user_id,
            Membership.status == 'active', Profile.account_status == 'active'
        ).execution_options(populate_existing=True)).scalar_one_or_none()
        if grant is None:
            raise PublicError(403, 'INSTITUTION_MODERATOR_REQUIRED', 'Institution moderator permission required.')
        return grant
    except SQLAlchemyError:
        database_failure(session)
