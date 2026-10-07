"""Real integration; creates and removes only a uniquely named disposable container."""
import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.main import create_app

pytestmark = pytest.mark.postgres


def test_migrations_sessions_and_outage(disposable_postgres):
    name, url = disposable_postgres
    sqlalchemy_url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    env = {**os.environ, "DATABASE_URL": sqlalchemy_url}
    backend = Path(__file__).resolve().parents[1]

    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend,
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, "Migration command failed (output suppressed for credentials)."

    migrate("upgrade", "head")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0006_membership_transitions"
        assert connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall() == [("administrator_bootstrap",), ("alembic_version",), ("institution_moderators",), ("institutions",), ("membership_reviews",), ("membership_transitions",), ("memberships",), ("platform_administrators",), ("profiles",)]
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO profiles(id,display_name) VALUES ('00000000-0000-4000-8000-000000000001','Preserved')")
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO institutions(id) VALUES ('00000000-0000-4000-8000-000000000002')")
        connection.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES ('00000000-0000-4000-8000-000000000002','00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000003','active')")
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO platform_administrators(user_id) VALUES ('00000000-0000-4000-8000-000000000001')")
        connection.execute("INSERT INTO administrator_bootstrap(singleton,target_user_id,actor,reason) VALUES (1,'00000000-0000-4000-8000-000000000001','operator_command','Synthetic isolated migration fixture')")
    migrate("downgrade", "0005_membership_reviews")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT to_regclass('public.membership_transitions')").fetchone()[0] is None
        assert connection.execute("SELECT status FROM memberships").fetchall()==[("active",)]
    migrate("upgrade", "head")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT transition_version FROM memberships").fetchall()==[(0,)]
    migrate("downgrade", "0004_administration_prerequisite")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT to_regclass('public.membership_reviews')").fetchone()[0] is None
        assert connection.execute("SELECT count(*) FROM platform_administrators").fetchone()[0]==1
        assert connection.execute("SELECT count(*) FROM administrator_bootstrap").fetchone()[0]==1
        assert connection.execute("SELECT status FROM memberships").fetchall()==[("active",)]
    migrate("upgrade", "head")
    migrate("downgrade", "0003_membership_requests")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT status FROM memberships").fetchall() == [("active",)]
        assert connection.execute("SELECT display_name FROM profiles").fetchall() == [("Preserved",)]
        assert connection.execute("SELECT to_regclass('public.platform_administrators'),to_regclass('public.institution_moderators'),to_regclass('public.administrator_bootstrap')").fetchone() == (None,None,None)
    migrate("upgrade", "head")
    migrate("downgrade", "0002_identity_profile")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT display_name FROM profiles").fetchall() == [("Preserved",)]
        assert connection.execute("SELECT to_regclass('public.memberships'),to_regclass('public.institutions')").fetchone() == (None,None)
    migrate("upgrade", "head")
    migrate("downgrade", "0001_foundation")
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT to_regclass('public.profiles')").fetchone()[0] is None
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_foundation"
    migrate("downgrade", "base")
    migrate("upgrade", "head")
    app = create_app(Settings(database_url=sqlalchemy_url, _env_file=None))

    @app.get("/test-session")
    def session_probe(session: Session = Depends(get_session)):
        # Test-only transaction probe; rolls back at dependency close.
        session.execute(text("CREATE TABLE rollback_probe (id integer)"))
        return {"result": session.execute(text("SELECT 1")).scalar_one()}

    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/test-session").json() == {"result": 1}
        with psycopg.connect(url) as connection:
            assert connection.execute("SELECT to_regclass('public.rollback_probe')").fetchone()[0] is None
        subprocess.check_output(["docker", "stop", name], text=True, stderr=subprocess.STDOUT)
        start = time.monotonic()
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert url not in response.text
        assert time.monotonic() - start < 10
        assert client.get("/health/live").status_code == 200
