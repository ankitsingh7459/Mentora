"""Checkpoint 2: disposable PostgreSQL, actual signed JWTs, synthetic Supabase HTTP."""
from uuid import uuid4

import psycopg
import pytest
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.identity.auth import get_verified_subject
from app.modules.identity.router import get_current_profile

from tests.test_identity_postgres import identity_client as base_identity_client
from tests.test_identity_auth import USER_ID, token, user, verifier

pytestmark = pytest.mark.postgres


@pytest.fixture
def identity_client(base_identity_client):
    client, key, url = base_identity_client
    with psycopg.connect(url) as db:
        db.execute("DELETE FROM profiles WHERE id=%s", (USER_ID,))
    yield client, key, url


def headers(key, **claims):
    return {"Authorization": f"Bearer {token(key, **claims)}"}


def stored(url, subject=USER_ID):
    with psycopg.connect(url) as db:
        return db.execute("SELECT id, display_name, account_status, created_at FROM profiles WHERE id=%s",
                          (subject,)).fetchone()


def test_trimmed_update_persists_and_null_clears(identity_client):
    client, key, url = identity_client
    response = client.patch("/api/v1/me", json={"displayName": "  Ankit Singh  "}, headers=headers(key))
    assert response.status_code == 200
    assert response.json() == {"userId": str(USER_ID), "displayName": "Ankit Singh"}
    assert stored(url)[1] == "Ankit Singh"
    assert client.get("/api/v1/me", headers=headers(key)).json() == response.json()
    response = client.patch("/api/v1/me", json={"displayName": None}, headers=headers(key))
    assert response.status_code == 200
    assert response.json() == {"userId": str(USER_ID), "displayName": None}
    assert stored(url)[1] is None
    assert client.get("/api/v1/me", headers=headers(key)).json() == response.json()


def test_invalid_and_protected_payloads_are_atomic(identity_client):
    client, key, url = identity_client
    assert client.patch("/api/v1/me", json={"displayName": "Original"}, headers=headers(key)).status_code == 200
    before = stored(url)
    protected = ["id", "userId", "email", "emailVerified", "email_confirmed_at", "account_status",
                 "blocked", "roles", "role", "membership", "contributorStatus", "isAdmin", "created_at",
                 "display_name", "unknownField"]
    payloads = [{}, {"displayName": ""}, {"displayName": " \t\n"}, {"displayName": "A"},
                {"displayName": "x" * 81}, {"displayName": 123}, {"displayName": True},
                {"displayName": []}, {"displayName": {"value": "Name"}}, [], "Name"]
    payloads += [{"displayName": "Attempted Change", field: "secret-marker"} for field in protected]
    payloads += [{"roles": ["admin"]}]
    for payload in payloads:
        response = client.patch("/api/v1/me", json=payload, headers=headers(key))
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "secret-marker" not in response.text
        assert response.json()["error"]["requestId"] == response.headers["X-Request-ID"]
        assert stored(url) == before, payload


def test_invalid_first_patch_does_not_initialize_profile(identity_client):
    client, key, url = identity_client
    subject = uuid4()
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(id=str(subject)))
    for payload in [{}, {"displayName": "New Name", "isAdmin": True}, {"displayName": " "}]:
        response = client.patch("/api/v1/me", json=payload, headers=headers(key, sub=str(subject)))
        assert response.status_code == 422
        assert stored(url, subject) is None


def test_missing_and_malformed_body_do_not_initialize(identity_client):
    client, key, url = identity_client
    subject = uuid4()
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(id=str(subject)))
    for body in [None, "{invalid json", "null"]:
        response = client.patch("/api/v1/me", content=body,
            headers={**headers(key, sub=str(subject)), "Content-Type": "application/json"})
        assert response.status_code == 422
        assert stored(url, subject) is None


def test_length_boundaries_after_trimming_and_safe_owner_selection(identity_client):
    client, key, url = identity_client
    other = uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO profiles(id, display_name) VALUES (%s, 'Other User')", (other,))
    for name in ["AB", "x" * 80, "अंकित", "Moderator"]:
        response = client.patch(f"/api/v1/me?userId={other}", json={"displayName": f"  {name}  "},
                                headers=headers(key, role="admin"))
        assert response.status_code == 200
        assert response.json() == {"userId": str(USER_ID), "displayName": name}
        assert stored(url, other)[1] == "Other User"
        assert stored(url)[2] == "active"


def test_invalid_expired_unverified_and_blocked_denied(identity_client):
    client, key, url = identity_client
    assert client.patch("/api/v1/me", json={"displayName": "Kept Name"}, headers=headers(key)).status_code == 200
    before = stored(url)
    for auth in [{}, {"Authorization": "Bearer invalid"}, headers(key, exp=1)]:
        response = client.patch("/api/v1/me", json={"displayName": "Denied Change"}, headers=auth)
        assert response.status_code == 401
        assert stored(url) == before
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(email_confirmed_at=None))
    assert client.patch("/api/v1/me", json={"displayName": "Denied Change"}, headers=headers(key)).status_code == 403
    assert stored(url) == before
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key)
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='blocked' WHERE id=%s", (USER_ID,))
    blocked = stored(url)
    response = client.patch("/api/v1/me", json={"displayName": "Denied Change"}, headers=headers(key))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_BLOCKED"
    assert stored(url) == blocked


def test_database_failure_rolls_back_update_and_first_initialization(identity_client):
    client, key, url = identity_client
    subject = uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO profiles(id, display_name) VALUES (%s, 'Before Failure')", (USER_ID,))
    before = stored(url)
    factory = client.app.state.session_factory
    class FailingSession(factory.class_):
        def commit(self):
            self.flush()  # Updates hit real PostgreSQL before failure in the same transaction.
            self.execute(text("SELECT * FROM checkpoint_update_missing_table"))
    client.app.state.session_factory = lambda: FailingSession(bind=factory.kw["bind"])
    for current in [USER_ID, subject]:
        client.app.state.identity_verifier.close()
        client.app.state.identity_verifier = verifier(key, user_data=user(id=str(current)))
        response = client.patch("/api/v1/me", json={"displayName": "Failed Change"}, headers=headers(key, sub=str(current)))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert "checkpoint_update_missing_table" not in response.text
        assert stored(url) == before
        assert stored(url, subject) is None


def test_block_committed_after_lookup_prevents_update(identity_client):
    client, key, url = identity_client
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO profiles(id, display_name) VALUES (%s, 'Unchanged')", (USER_ID,))
    def block_after_lookup(subject=Depends(get_verified_subject), session: Session = Depends(get_session)):
        profile = get_current_profile(subject, session)
        with psycopg.connect(url) as db:
            db.execute("UPDATE profiles SET account_status='blocked' WHERE id=%s", (subject,))
        return profile
    client.app.dependency_overrides[get_current_profile] = block_after_lookup
    response = client.patch("/api/v1/me", json={"displayName": "Denied Change"}, headers=headers(key))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_BLOCKED"
    assert stored(url)[1:3] == ("Unchanged", "blocked")


def test_patch_openapi_contract(identity_client):
    client, _, _ = identity_client
    schema = client.get("/api/v1/openapi.json").json()
    operation = schema["paths"]["/api/v1/me"]["patch"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert set(operation["responses"]) == {"200", "401", "403", "422", "503"}
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
    update = schema["components"]["schemas"]["ProfileUpdate"]
    assert update["additionalProperties"] is False
    assert set(update["properties"]) == {"displayName"}
    assert update["required"] == ["displayName"]
    assert {"type": "null"} in update["properties"]["displayName"]["anyOf"]
