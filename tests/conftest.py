"""Shared test setup: a fresh cmt_service with an empty in-memory database for every test."""
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "cmt_service"


@pytest.fixture
def cmt(monkeypatch):
    """Returns (flask test client, loaded app module). Database lives only in memory."""
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
