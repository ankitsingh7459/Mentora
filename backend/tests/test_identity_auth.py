"""Synthetic Supabase HTTP responses; JWT signatures are real local cryptography."""
from datetime import datetime, timezone
import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import ec, rsa
import httpx
import jwt
import pytest
from pydantic import ValidationError

from app.config import Settings, load_settings
from app.errors import PublicError
from app.modules.identity.auth import SupabaseVerifier

USER_ID = uuid4()
PROVIDER = "https://pilot.supabase.co"
DB_URL = "postgresql+psycopg://test:synthetic@127.0.0.1:1/test"


@pytest.fixture
def key():
    return ec.generate_private_key(ec.SECP256R1())


def settings(**kwargs):
    return Settings(database_url=DB_URL, supabase_url=PROVIDER,
                    supabase_publishable_key="sb_publishable_synthetic", _env_file=None, **kwargs)


def token(key, **changes):
    claims = {"sub": str(USER_ID), "iss": PROVIDER + "/auth/v1", "aud": "authenticated",
              "iat": int(time.time()) - 1, "exp": int(time.time()) + 300,
              "role": "authenticated"}
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "test-key"})


def jwk(key, kid="test-key"):
    value = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    return {**value, "kid": kid, "alg": "ES256", "use": "sig"}


def user(**changes):
    return {"id": str(USER_ID), "email": "synthetic@example.invalid",
            "email_confirmed_at": datetime.now(timezone.utc).isoformat(),
            "is_anonymous": False, **changes}


def verifier(key, user_data=None, jwks=None, status=200):
    def respond(request):
        assert str(request.url).startswith(PROVIDER + "/auth/v1/")
        assert "secret" not in str(request.url)
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json=jwks if jwks is not None else {"keys": [jwk(key)]})
        assert request.url.path == "/auth/v1/user"
        assert request.headers["apikey"] == "sb_publishable_synthetic"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(status, json=user_data if user_data is not None else user())
    return SupabaseVerifier(settings(), transport=httpx.MockTransport(respond))


def test_signed_verified_identity_ignores_privilege_claims(key):
    with verifier(key) as check:
        assert check.verify(token(key, role="service_role", user_metadata={"is_admin": True})) == USER_ID


@pytest.mark.parametrize("changes", [
    {"exp": 1}, {"iss": "https://attacker.invalid/auth/v1"}, {"aud": "other"},
    {"sub": "not-a-uuid"}, {"iat": int(time.time()) + 300}, {"exp": None},
])
def test_invalid_claims_are_denied(key, changes):
    with verifier(key) as check, pytest.raises(PublicError) as error:
        check.verify(token(key, **changes))
    assert error.value.status == 401


def test_missing_required_claim(key):
    encoded = jwt.encode({"sub": str(USER_ID)}, key, algorithm="ES256", headers={"kid": "test-key"})
    with verifier(key) as check, pytest.raises(PublicError) as error:
        check.verify(encoded)
    assert error.value.status == 401


@pytest.mark.parametrize("encoded", ["garbage", "a.b.c", "x" * 8193,
    jwt.encode({"sub": str(USER_ID)}, "synthetic-secret-long-enough-for-hmac-test", algorithm="HS256")])
def test_malformed_or_unsupported_token(encoded, key):
    with verifier(key) as check, pytest.raises(PublicError) as error:
        check.verify(encoded)
    assert error.value.status == 401


def test_bad_signature_and_unknown_key(key):
    other = ec.generate_private_key(ec.SECP256R1())
    with verifier(key) as check, pytest.raises(PublicError) as error:
        check.verify(token(other))
    assert error.value.status == 401
    with verifier(key, jwks={"keys": [jwk(other, "other-key")]}) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == 401


@pytest.mark.parametrize("changes", [{"email_confirmed_at": None}, {"email": None}, {"is_anonymous": True}])
def test_unverified_or_anonymous_denied(key, changes):
    with verifier(key, user_data=user(**changes)) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == 403


def test_provider_subject_mismatch_is_denied(key):
    with verifier(key, user_data=user(id=str(uuid4()))) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == 401


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 401), (429, 503), (500, 503)])
def test_provider_status_mapping(key, status, expected):
    with verifier(key, status=status) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == expected


def test_outage_and_malformed_provider_fail_closed(key):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic-secret", request=request)
    with SupabaseVerifier(settings(), transport=httpx.MockTransport(timeout)) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == 503
    assert "synthetic-secret" not in error.value.message
    with verifier(key, jwks={"keys": []}) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == 503
    with verifier(key, user_data={"unexpected": "synthetic-secret"}) as check, pytest.raises(PublicError) as error:
        check.verify(token(key))
    assert error.value.status == 503


def test_rotation_does_not_cache_old_keys(key):
    other = ec.generate_private_key(ec.SECP256R1())
    current = {"key": key}
    def respond(request):
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": [jwk(current["key"])]})
        return httpx.Response(200, json=user())
    with SupabaseVerifier(settings(), transport=httpx.MockTransport(respond)) as check:
        assert check.verify(token(key)) == USER_ID
        current["key"] = other
        assert check.verify(token(other)) == USER_ID
        with pytest.raises(PublicError):
            check.verify(token(key))


def test_rsa_signature_supported():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),
              "kid": "rsa-test", "alg": "RS256", "use": "sig"}
    claims = {"sub": str(USER_ID), "iss": PROVIDER + "/auth/v1", "aud": "authenticated",
              "iat": int(time.time()) - 1, "exp": int(time.time()) + 300}
    encoded = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "rsa-test"})
    def respond(request):
        return httpx.Response(200, json={"keys": [public]} if request.url.path.endswith("jwks.json") else user())
    with SupabaseVerifier(settings(), transport=httpx.MockTransport(respond)) as check:
        assert check.verify(encoded) == USER_ID


def test_unsigned_and_token_key_url_cannot_bypass_fixed_key_source(key):
    with verifier(key) as check, pytest.raises(PublicError) as error:
        check.verify(jwt.encode({"sub": str(USER_ID)}, None, algorithm="none"))
    assert error.value.status == 401
    claims = {"sub": str(USER_ID), "iss": PROVIDER + "/auth/v1", "aud": "authenticated",
              "iat": int(time.time()) - 1, "exp": int(time.time()) + 300}
    encoded = jwt.encode(claims, key, algorithm="ES256", headers={"kid": "test-key", "jku": "https://attacker.invalid/keys"})
    with verifier(key) as check:
        assert check.verify(encoded) == USER_ID  # Test transport asserts only fixed provider URLs.


def test_oversized_or_corrupt_provider_response_is_unavailable(key):
    for payload in [b"{not json", b"x" * 65537]:
        with SupabaseVerifier(settings(), transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=payload))) as check, pytest.raises(PublicError) as error:
            check.verify(token(key))
        assert error.value.status == 503


def test_auth_configuration_is_optional_but_paired():
    Settings(database_url=DB_URL, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL, supabase_url=PROVIDER, _env_file=None)


def test_incomplete_config_has_sanitized_startup_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("SUPABASE_URL", PROVIDER)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY") as error:
        load_settings()
    assert "synthetic" not in str(error.value)


def test_privileged_provider_key_is_not_accepted():
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL, supabase_url=PROVIDER,
                 supabase_publishable_key="sb_secret_synthetic", _env_file=None)


@pytest.mark.parametrize("url", ["http://pilot.supabase.co", "https://user:password@pilot.supabase.co",
    "https://pilot.supabase.co/path", "https://pilot.supabase.co?secret=value", "https://pilot.supabase.co/#fragment",
    " https://pilot.supabase.co"])
def test_provider_url_is_fixed_https_origin(url):
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL, supabase_url=url, supabase_publishable_key="sb_publishable_synthetic", _env_file=None)
