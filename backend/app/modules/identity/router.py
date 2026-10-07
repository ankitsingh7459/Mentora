from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import ErrorResponse, PublicError
from app.modules.identity.auth import get_verified_subject
from app.modules.identity.models import Profile

router = APIRouter()


class OwnProfile(BaseModel):
    userId: UUID
    displayName: str | None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    # With only one approved field and empty requests forbidden, presence is required; null is valid.
    displayName: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)] | None


def commit_profile(session: Session):
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise PublicError(503, "DATABASE_UNAVAILABLE", "Database unavailable.") from None


def get_current_profile(subject: UUID = Depends(get_verified_subject),
                        session: Session = Depends(get_session)) -> Profile:
    try:
        profile = session.get(Profile, subject)
        if profile is None:
            # Single provider UUID is the uniqueness boundary, including simultaneous first reads.
            session.execute(insert(Profile).values(id=subject).on_conflict_do_nothing(index_elements=[Profile.id]))
            profile = session.get(Profile, subject)
            if profile.account_status == "blocked":
                raise PublicError(403, "ACCOUNT_BLOCKED", "Account access is blocked.")
            # The HTTP operation owns commit: validation/failure must roll back initialization too.
        if profile.account_status != "active":
            raise PublicError(403, "ACCOUNT_BLOCKED", "Account access is blocked.")
        return profile
    except SQLAlchemyError:
        session.rollback()
        raise PublicError(503, "DATABASE_UNAVAILABLE", "Database unavailable.") from None


@router.get("/me", response_model=OwnProfile,
            responses={status: {"model": ErrorResponse} for status in (401, 403, 503)},
            summary="Read the verified caller's own profile")
def me(profile: Profile = Depends(get_current_profile), session: Session = Depends(get_session)):
    response = OwnProfile(userId=profile.id, displayName=profile.display_name)
    commit_profile(session)
    return response


@router.patch("/me", response_model=OwnProfile,
              responses={status: {"model": ErrorResponse} for status in (401, 403, 422, 503)},
              summary="Update only the verified caller's display name")
def patch_me(payload: ProfileUpdate, profile: Profile = Depends(get_current_profile),
             session: Session = Depends(get_session)):
    try:
        row = session.execute(update(Profile).where(
            Profile.id == profile.id, Profile.account_status == "active"
        ).values(display_name=payload.displayName).returning(Profile.id, Profile.display_name),
            execution_options={"synchronize_session": False}).one_or_none()
        if row is None:
            session.rollback()
            raise PublicError(403, "ACCOUNT_BLOCKED", "Account access is blocked.")
        response = OwnProfile(userId=row.id, displayName=row.display_name)
        commit_profile(session)
        return response
    except SQLAlchemyError:
        session.rollback()
        raise PublicError(503, "DATABASE_UNAVAILABLE", "Database unavailable.") from None
