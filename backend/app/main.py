from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from app.config import Settings, load_settings
from app.db import create_db_engine, get_session
from app.errors import ErrorResponse, PublicError, error_response, http_error, public_error, unexpected_error, validation_error
from app.modules.identity.auth import SupabaseVerifier
from app.modules.identity.router import router as identity_router

from app.modules.identity.membership import router as membership_router

from app.modules.identity.membership_admin import router as membership_admin_router

from app.modules.identity.membership_transitions import router as membership_transitions_router

API_PREFIX = "/api/v1"


class Health(BaseModel):
    status: str


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings or load_settings()
        app.state.pilot_institution_id = config.pilot_institution_id
        engine = create_db_engine(config)
        app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        app.state.identity_verifier = SupabaseVerifier(config)
        try:
            yield
        finally:
            app.state.identity_verifier.close()
            engine.dispose()

    app = FastAPI(title="Mentora API", version="0.1.0", lifespan=lifespan,
                  docs_url=f"{API_PREFIX}/docs", redoc_url=None,
                  openapi_url=f"{API_PREFIX}/openapi.json")
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)
    app.add_exception_handler(PublicError, public_error)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except Exception:
            # Do not let Uvicorn log database exceptions, URLs or user input.
            response = await unexpected_error(request, Exception())
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/health/live", response_model=Health)
    def live():
        return Health(status="alive")

    @app.get("/health/ready", response_model=Health,
             responses={503: {"model": ErrorResponse}})
    def ready(request: Request, session: Session = Depends(get_session)):
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return error_response(request, 503, "DATABASE_UNAVAILABLE", "Database unavailable.")
        return Health(status="ready")

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(identity_router, tags=["identity"])
    api.include_router(membership_router, tags=["membership"])
    api.include_router(membership_admin_router, tags=["membership administration"])
    api.include_router(membership_transitions_router, tags=["membership administration"])
    app.include_router(api)
    return app


app = create_app()
