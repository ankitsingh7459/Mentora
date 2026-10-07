"""Real disposable PostgreSQL and signed JWTs; provider HTTP transport is synthetic."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
import psycopg
import pytest
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from tests.test_identity_auth import USER_ID, PROVIDER, token, user, verifier

pytestmark = pytest.mark.postgres


@pytest.fixture
def identity_client(disposable_postgres):
    _, url = disposable_postgres
    env = {**os.environ, "DATABASE_URL": url.replace("postgresql://", "postgresql+psycopg://", 1)}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True)
    assert result.returncode == 0, "Migration failed (output suppressed)."
    settings = Settings(database_url=env["DATABASE_URL"], supabase_url=PROVIDER,
                        supabase_publishable_key="sb_publishable_synthetic", _env_file=None)
    key = ec.generate_private_key(ec.SECP256R1())
    with TestClient(create_app(settings)) as client:
        old = client.app.state.identity_verifier
        client.app.state.identity_verifier = verifier(key)
        old.close()
        yield client, key, url


def test_own_profile_no_privilege_claims_or_foreign_identity(identity_client):
    client, key, url = identity_client
    encoded = token(key, role="admin", user_metadata={"role": "moderator", "display_name": "untrusted"})
    response = client.get(f"/api/v1/me?userId={uuid4()}", headers={"Authorization": f"Bearer {encoded}"})
    assert response.status_code == 200
    assert response.json() == {"userId": str(USER_ID), "displayName": None}
    assert response.headers["X-Request-ID"]
    with psycopg.connect(url) as db:
        assert db.execute("SELECT id, account_status FROM profiles WHERE id=%s", (USER_ID,)).fetchone() == (USER_ID, "active")
        assert db.execute("SELECT relrowsecurity FROM pg_class WHERE oid='profiles'::regclass").fetchone()[0]
    schema = client.get("/api/v1/openapi.json").json()
    route = schema["paths"]["/api/v1/me"]["get"]
    assert route["security"] == [{"HTTPBearer": []}]
    assert set(route["responses"]) == {"200", "401", "403", "503"}
    assert set(schema["paths"]) == {"/api/v1/institutions/{institution_id}/memberships/{user_id}", "/api/v1/institutions/{institution_id}/memberships/{user_id}/revoke", "/api/v1/institutions/{institution_id}/memberships/{user_id}/reinstate", "/health/live", "/health/ready", "/api/v1/me", "/api/v1/institutions/{institution_id}/membership-requests", "/api/v1/membership-requests/{request_id}", "/api/v1/institutions/{institution_id}/membership-requests/{request_id}", "/api/v1/institutions/{institution_id}/membership-requests/{request_id}/decision"}


@pytest.mark.parametrize("header", [None, "Basic secret-marker", "Bearer", "Bearer invalid-secret-marker"])
def test_denied_auth_never_creates_profile(identity_client, header):
    client, _, url = identity_client
    with psycopg.connect(url) as db:
        before = db.execute("SELECT count(*) FROM profiles").fetchone()[0]
    response = client.get("/api/v1/me", headers={"Authorization": header} if header else {})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "secret-marker" not in response.text
    assert response.json()["error"]["requestId"] == response.headers["X-Request-ID"]
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM profiles").fetchone()[0] == before


def test_current_blocked_state_denies_same_unexpired_token(identity_client):
    client, key, url = identity_client
    encoded = token(key)
    headers = {"Authorization": f"Bearer {encoded}"}
    assert client.get("/api/v1/me", headers=headers).status_code == 200
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='blocked' WHERE id=%s", (USER_ID,))
    response = client.get("/api/v1/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_BLOCKED"
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='active' WHERE id=%s", (USER_ID,))


def test_unverified_no_initialization(identity_client):
    client, key, url = identity_client
    subject = uuid4()
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(id=str(subject), email_confirmed_at=None))
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token(key, sub=str(subject))}"})
    assert response.status_code == 403
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM profiles WHERE id=%s", (subject,)).fetchone()[0] == 0


def test_concurrent_first_reads_have_one_profile(identity_client):
    client, key, url = identity_client
    subject = uuid4()
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(id=str(subject)))
    headers = {"Authorization": f"Bearer {token(key, sub=str(subject))}"}
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: client.get("/api/v1/me", headers=headers), range(6)))
    assert all(r.status_code == 200 and r.json()["userId"] == str(subject) for r in responses)
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM profiles WHERE id=%s", (subject,)).fetchone()[0] == 1


def test_initialization_failure_rolls_back(identity_client, monkeypatch):
    client, key, url = identity_client
    subject = uuid4()
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(id=str(subject)))
    factory = client.app.state.session_factory
    class FailingSession(factory.class_):
        def commit(self):
            # Execute a real invalid SQL statement after INSERT to abort the same transaction.
            self.execute(text("SELECT * FROM checkpoint_missing_table"))
    client.app.state.session_factory = lambda: FailingSession(bind=factory.kw["bind"])
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token(key, sub=str(subject))}"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "checkpoint_missing_table" not in response.text
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM profiles WHERE id=%s", (subject,)).fetchone()[0] == 0


def test_auth_unconfigured_preserves_health(disposable_postgres):
    _, url = disposable_postgres
    config = Settings(database_url=url.replace("postgresql://", "postgresql+psycopg://"), _env_file=None)
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer a.b.c"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200


def test_profile_rls_denies_browser_role_even_with_table_grants(identity_client):
    _, _, url = identity_client
    with psycopg.connect(url) as db:
        db.execute("CREATE ROLE checkpoint_browser NOLOGIN")
        db.execute("GRANT USAGE ON SCHEMA public TO checkpoint_browser")
        db.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON profiles TO checkpoint_browser")
        db.execute("SET LOCAL ROLE checkpoint_browser")
        assert db.execute("SELECT count(*) FROM profiles").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("INSERT INTO profiles(id) VALUES (%s)", (uuid4(),))
        db.rollback()  # Includes role/grants; no persistent fixture side effects.
