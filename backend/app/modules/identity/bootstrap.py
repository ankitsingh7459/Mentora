"""Explicit operator-only first-administrator command. Never called at API startup."""
import argparse
from getpass import getpass, GetPassWarning
import json
import sys
from uuid import UUID, uuid4
import warnings
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.config import load_settings
from app.db import create_db_engine
from app.errors import Error, ErrorResponse, PublicError
from app.modules.identity.auth import SupabaseVerifier
from app.modules.identity.models import Profile
from app.modules.identity.role_models import AdministratorBootstrap, PlatformAdministrator


def bootstrap_first_admin(session: Session, verifier: SupabaseVerifier, target: UUID, access_token: str) -> None:
    """Owns commit/rollback. Caller must supply a fresh isolated unit-of-work session."""
    try:
        if verifier.verify(access_token) != target:
            raise PublicError(403, 'BOOTSTRAP_TARGET_MISMATCH', 'Verified identity must match the bootstrap target.')
        # Serialize all invocations, including when no singleton row exists yet.
        # This is a lock namespace, not a hardcoded administrator identity.
        session.execute(text('SELECT pg_advisory_xact_lock(724019, 1)'))
        if (session.get(AdministratorBootstrap, 1) is not None or
                session.scalar(select(PlatformAdministrator.user_id).limit(1)) is not None):
            raise PublicError(409, 'ADMINISTRATOR_BOOTSTRAP_CLOSED', 'Administrator bootstrap has already been closed.')
        session.execute(insert(Profile).values(id=target).on_conflict_do_nothing(index_elements=[Profile.id]))
        status = session.execute(select(Profile.account_status).where(Profile.id == target).with_for_update()).scalar_one()
        if status != 'active':
            raise PublicError(403, 'ACCOUNT_BLOCKED', 'Account access is blocked.')
        session.add(AdministratorBootstrap(singleton=1, target_user_id=target, actor='operator_command',
                                          reason='Initial platform administrator bootstrap.'))
        session.add(PlatformAdministrator(user_id=target))
        session.commit()
    except PublicError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise PublicError(503, 'DATABASE_UNAVAILABLE', 'Database unavailable.') from None


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise PublicError(422, 'INVALID_OPERATOR_INPUT', 'Provide exactly one valid --user-id UUID.')


def main(argv=None) -> int:
    engine = None
    try:
        parser = SafeParser(description='Explicitly bootstrap the first platform administrator.')
        parser.add_argument('--user-id', required=True, type=UUID)
        args = parser.parse_args(argv)
        settings = load_settings()
        # Never accept tokens via arguments/environment or fall back to echoed stdin.
        with warnings.catch_warnings():
            warnings.simplefilter('error', GetPassWarning)
            access_token = getpass('Target user Supabase access token (hidden): ')
        engine = create_db_engine(settings)
        with SupabaseVerifier(settings) as verifier, Session(engine) as session:
            bootstrap_first_admin(session, verifier, args.user_id, access_token)
        print(json.dumps({'status': 'bootstrapped'}))
        return 0
    except PublicError as error:
        failure = error
    except (EOFError, KeyboardInterrupt, GetPassWarning):
        failure = PublicError(422, 'SECURE_INPUT_REQUIRED', 'An interactive hidden-input terminal is required.')
    except RuntimeError:
        failure = PublicError(503, 'OPERATOR_CONFIGURATION_UNAVAILABLE', 'Check operator configuration and .env.example.')
    except Exception:
        failure = PublicError(500, 'OPERATOR_COMMAND_FAILED', 'Operator command failed.')
    finally:
        if engine is not None:
            engine.dispose()
    print(ErrorResponse(error=Error(code=failure.code, message=failure.message,
          requestId=str(uuid4()), details=[])).model_dump_json(), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
