import logging
from pathlib import Path
import subprocess
from uuid import UUID

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.config import Settings, load_settings
from app.db import get_session
from app.main import API_PREFIX, create_app

URL = "postgresql+psycopg://synthetic:secret-marker@127.0.0.1:1/test"


def test_missing_config_is_sanitized(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL") as error:
        load_settings()
    assert error.value.__suppress_context__
    assert "input_value" not in str(error.value)


@pytest.mark.parametrize("url", ["", "sqlite:///test", "postgresql://u:p@localhost/db", "secret-marker", "postgresql+psycopg://u:p@localhost:99999/db", URL + "?plugin=secret-marker", URL + "?sslmode=invalid"])
def test_invalid_config_does_not_echo_input(url, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError) as error:
        load_settings()
    if url:
        assert url not in str(error.value)
    assert "secret-marker" not in str(error.value)


def test_dotenv_and_environment_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"DATABASE_URL={URL}\nDB_CONNECT_TIMEOUT_SECONDS=5\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "2")
    settings = load_settings()
    assert settings.database_url.get_secret_value() == URL
    assert settings.db_connect_timeout_seconds == 2
    assert "secret-marker" not in repr(settings)


def test_timeouts_validated():
    with pytest.raises(ValidationError):
        Settings(database_url=URL, db_connect_timeout_seconds=0)


def test_health_failure_and_liveness(caplog):
    app = create_app(Settings(database_url=URL, _env_file=None))

    class Unavailable:
        def execute(self, statement):
            raise OperationalError("SELECT secret-marker", {}, Exception(URL))

    app.dependency_overrides[get_session] = lambda: Unavailable()
    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        response = client.get("/health/ready")
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "DATABASE_UNAVAILABLE"
        assert str(UUID(error["requestId"])) == response.headers["X-Request-ID"]
        assert error["details"] == []
        assert "secret-marker" not in response.text + caplog.text


def test_error_conventions_and_prefix(caplog):
    app = create_app(Settings(database_url=URL, _env_file=None))
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/test-validation")
    def validation(count: int):
        return {"count": count}

    @router.get("/test-failure")
    def failure():
        raise RuntimeError("secret-marker")

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/test-validation?count=secret-marker")
        assert response.status_code == 422
        assert response.json()["error"]["details"] == [{"field": "query.count", "code": "int_parsing"}]
        assert "secret-marker" not in response.text
        for path, status, code in [("/missing", 404, "NOT_FOUND"), ("/api/v1/test-failure", 500, "INTERNAL_ERROR")]:
            response = client.get(path)
            assert response.status_code == status
            assert response.json()["error"]["code"] == code
            assert response.json()["error"]["requestId"] == response.headers["X-Request-ID"]
            assert "secret-marker" not in response.text + caplog.text
        schema = client.get("/api/v1/openapi.json").json()
        assert "503" in schema["paths"]["/health/ready"]["get"]["responses"]


def test_startup_rejects_missing_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="Invalid configuration: check DATABASE_URL"):
        with TestClient(create_app()):
            pass


def test_secret_files_are_ignored(tmp_path):
    # Verify Git rules in an isolated repository; do not initialize the team checkout.
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    rules = Path(__file__).resolve().parents[2] / ".gitignore"
    (tmp_path / ".gitignore").write_text(rules.read_text())
    files = ["backend/.env", "backend/.env.local", "backend/private.key", ".venv/example"]
    result = subprocess.run(["git", "check-ignore", "--no-index", *files], cwd=tmp_path,
                            check=True, capture_output=True, text=True)
    assert set(result.stdout.splitlines()) == set(files)
    example = subprocess.run(["git", "check-ignore", "--no-index", "backend/.env.example"],
                             cwd=tmp_path, capture_output=True)
    assert example.returncode == 1


def test_pilot_uuid_configuration_is_optional_and_sanitized(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL', URL)
    monkeypatch.delenv('PILOT_INSTITUTION_ID', raising=False)
    assert load_settings().pilot_institution_id is None
    pilot='00000000-0000-4000-8000-000000000002'
    monkeypatch.setenv('PILOT_INSTITUTION_ID', pilot)
    assert str(load_settings().pilot_institution_id)==pilot
    monkeypatch.setenv('PILOT_INSTITUTION_ID', 'secret-invalid-uuid-marker')
    with pytest.raises(RuntimeError,match='PILOT_INSTITUTION_ID') as error:
        load_settings()
    assert 'secret-invalid-uuid-marker' not in str(error.value)
