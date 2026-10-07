from uuid import UUID
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=3000, ge=1, le=30000)
    pilot_institution_id: UUID | None = None
    supabase_url: str | None = None
    supabase_publishable_key: SecretStr | None = None
    supabase_jwt_audience: str = Field(default="authenticated", min_length=1, max_length=128)
    auth_http_timeout_seconds: int = Field(default=3, ge=1, le=10)

    @field_validator("supabase_url")
    @classmethod
    def provider_origin(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if (value != value.strip() or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            raise ValueError("must be an HTTPS project origin without credentials, path or query")
        return value.rstrip("/")

    @field_validator("supabase_publishable_key")
    @classmethod
    def publishable_key(cls, value):
        if value is not None:
            raw = value.get_secret_value()
            if not raw.startswith("sb_publishable_") or not 16 <= len(raw) <= 512:
                raise ValueError("must be a Supabase publishable key, not a privileged secret key")
        return value

    @model_validator(mode="after")
    def paired_auth_settings(self):
        if bool(self.supabase_url) != bool(self.supabase_publishable_key):
            raise ValueError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be configured together")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+psycopg" or not all(
                (url.host, url.username, url.password, url.database)
            ):
                raise ValueError
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError
            if set(url.query) - {"sslmode"}:
                raise ValueError
            if "sslmode" in url.query and url.query["sslmode"] not in {
                "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
            }:
                raise ValueError
        except (ArgumentError, ValueError, TypeError):
            raise ValueError("must be a PostgreSQL psycopg URL with host, database and credentials") from None
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        fields = ", ".join(sorted({str(e["loc"][0]).upper() if e["loc"] else
                                   "SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY" for e in exc.errors()}))
        raise RuntimeError(f"Invalid configuration: check {fields}. See .env.example.") from None
