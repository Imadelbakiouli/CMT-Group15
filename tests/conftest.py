"""Shared test setup: every test gets a fresh cmt_service with an empty in-memory database."""
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "cmt_service"


@pytest.fixture
def cmt(monkeypatch):
    """Gives (test client, app module). The database only lives in memory."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    sys.path.insert(0, str(SERVICE_DIR))
    try:
        spec = importlib.util.spec_from_file_location("cmt_service_app_test", SERVICE_DIR / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SERVICE_DIR))
    return module.app.test_client(), module


USER = {"name": "Test User", "email": "Test.User@Example.com", "affiliation": "VU", "password": "correct-horse-battery"}


def register(client, **overrides):
    return client.post("/auth/register", json={**USER, **overrides})


def login(client, email=USER["email"], password=USER["password"]):
    return client.post("/auth/login", json={"email": email, "password": password})


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def signup(client, email, name="Someone"):
    """Make an account and log in. Gives the headers to use in requests."""
    register(client, name=name, email=email)
    return auth_header(login(client, email=email).get_json()["token"])


CONFERENCE = {
    "name": "EAMT 2028", "description": "Annual conference",
    "submission_deadline": "2028-03-01T12:00:00Z",
    "review_deadline": "2028-04-01T12:00:00Z",
    "notification_deadline": "2028-05-01T12:00:00Z",
}


def make_conference(client, headers, **overrides):
    return client.post("/conferences", json={**CONFERENCE, **overrides}, headers=headers)
