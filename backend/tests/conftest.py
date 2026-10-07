"""Shared disposable PostgreSQL fixture; never targets configured databases."""
import os
import subprocess
import time
from uuid import uuid4

import psycopg
import pytest

def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


@pytest.fixture(scope="module")
def disposable_postgres():
    name = f"mentora-s0-test-{uuid4().hex}"
    password = uuid4().hex
    image = os.environ.get("TEST_POSTGRES_IMAGE", "postgres:16")
    try:
        docker("run", "--detach", "--rm", "--name", name,
               "--publish", "127.0.0.1::5432", "--env", "POSTGRES_USER=mentora",
               "--env", f"POSTGRES_PASSWORD={password}", "--env", "POSTGRES_DB=mentora_test", image)
        port = docker("port", name, "5432/tcp").split(":")[-1]
        url = f"postgresql://mentora:{password}@127.0.0.1:{port}/mentora_test"
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(url, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable PostgreSQL did not become ready.")
                time.sleep(0.5)
        yield name, url
    finally:
        # The unique name is generated here, never supplied by a shared environment.
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False)
