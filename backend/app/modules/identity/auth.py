"""Supabase identity verification. Membership/privilege claims are never trusted."""
from datetime import datetime
import json
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx
import jwt
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.errors import PublicError


def invalid_token():
    return PublicError(401, "INVALID_CREDENTIALS", "Valid authentication is required.")


def auth_unavailable():
    return PublicError(503, "AUTH_UNAVAILABLE", "Authentication service unavailable.")


class ProviderUser(BaseModel):
    id: UUID
    email: str | None = Field(default=None, max_length=320)
    email_confirmed_at: datetime | None = None
    is_anonymous: bool = Field(strict=True)


class SupabaseVerifier:
    def __init__(self, settings: Settings, *, transport=None):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.auth_http_timeout_seconds,
                                   follow_redirects=False, trust_env=False, transport=transport)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.client.close()

    def _json(self, path: str, *, headers=None, user_request=False):
        try:
            with self.client.stream("GET", self.settings.supabase_url + path, headers=headers) as response:
                if user_request and response.status_code in (401, 403):
                    raise invalid_token()
                if response.status_code != 200:
                    raise auth_unavailable()
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 65536:
                        raise auth_unavailable()
                return json.loads(payload)
        except (httpx.HTTPError, ValueError, TypeError):
            raise auth_unavailable() from None

    def verify(self, token: str) -> UUID:
        if not token or len(token) > 8192:
            raise invalid_token()
        if not self.settings.supabase_url or not self.settings.supabase_publishable_key:
            raise auth_unavailable()
        try:
            header = jwt.get_unverified_header(token)
            algorithm, kid = header.get("alg"), header.get("kid")
            if algorithm not in ("RS256", "ES256") or not isinstance(kid, str) or not kid or len(kid) > 128:
                raise invalid_token()
        except jwt.InvalidTokenError:
            raise invalid_token() from None

        # Fetch current keys each request: no stale application cache or token-controlled URL.
        jwks = self._json("/auth/v1/.well-known/jwks.json")
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list) or not jwks["keys"]:
            raise auth_unavailable()
        candidates = [key for key in jwks["keys"] if isinstance(key, dict) and
                      key.get("kid") == kid and key.get("alg") == algorithm and key.get("use", "sig") == "sig"]
        if not candidates:
            raise invalid_token()
        if len(candidates) != 1:
            raise auth_unavailable()
        try:
            key = jwt.PyJWK.from_dict(candidates[0], algorithm=algorithm)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise auth_unavailable() from None
        try:
            claims = jwt.decode(token, key.key, algorithms=[algorithm],
                                issuer=self.settings.supabase_url + "/auth/v1",
                                audience=self.settings.supabase_jwt_audience,
                                options={"require": ["sub", "iss", "aud", "exp", "iat"]})
            subject = UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise invalid_token() from None

        data = self._json("/auth/v1/user", user_request=True, headers={
            "Authorization": f"Bearer {token}",
            "apikey": self.settings.supabase_publishable_key.get_secret_value(),
        })
        try:
            user = ProviderUser.model_validate(data)
        except ValidationError:
            raise auth_unavailable() from None
        if user.id != subject:
            raise invalid_token()
        if user.is_anonymous or not user.email or not user.email_confirmed_at:
            raise PublicError(403, "EMAIL_VERIFICATION_REQUIRED", "A verified email account is required.")
        return subject


bearer = HTTPBearer(auto_error=False)


def get_verified_subject(request: Request,
                         credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> UUID:
    authorization = request.headers.get("Authorization", "")
    parts = authorization.split()
    if credentials is None or len(parts) != 2 or parts[0].lower() != "bearer":
        raise invalid_token()
    return request.app.state.identity_verifier.verify(parts[1])
