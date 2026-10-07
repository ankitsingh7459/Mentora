from alembic import context

from app.config import load_settings
from app.db import Base, create_db_engine
from app.modules.identity.models import Profile  # Register only the implemented model.

from app.modules.identity.membership_models import Institution, Membership

from app.modules.identity.role_models import AdministratorBootstrap, InstitutionModerator, PlatformAdministrator

from app.modules.identity.membership_review_models import MembershipReview

from app.modules.identity.membership_transition_models import MembershipTransition

target_metadata = Base.metadata
settings = load_settings()

if context.is_offline_mode():
    context.configure(url=settings.database_url.get_secret_value(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    except Exception:
        raise RuntimeError("Migration failed; check database availability and migration compatibility.") from None
    finally:
        engine.dispose()
