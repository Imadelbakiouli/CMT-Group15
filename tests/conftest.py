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


# ---- the whole stack in one process (interface -> orchestrator -> cmt_service / ai_agent) ----

class FakeAnswer:
    """Looks like the answer of the requests library."""

    def __init__(self, response):
        self.status_code = response.status_code
        self.content = response.data
        self.headers = response.headers

    def json(self):
        import json
        return json.loads(self.content)


def load_service(name):
    """Load <name>/app.py as a module (own module name, so the four app.py files do not clash)."""
    folder = ROOT / name
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location(f"{name}_stack_test", folder / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(folder))
    return module


@pytest.fixture
def stack(monkeypatch):
    """All four servers as real Flask apps that call each other without a network.

    Returns an object with .interface, .orchestrator, .cmt, .ai (test clients), .calls (every call
    that went between servers) and .routes (set an entry to None to make a server 'unreachable').
    """
    import requests

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("CMT_SERVICE_URL", "http://cmt.test")
    monkeypatch.setenv("AI_AGENT_URL", "http://ai.test")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orch.test")

    class Stack:
        pass

    s = Stack()
    s.cmt_module = load_service("cmt_service")
    s.cmt = s.cmt_module.app.test_client()
    s.ai_module = load_service("ai_agent")
    s.ai = s.ai_module.app.test_client()
    s.orchestrator = load_service("orchestrator").app.test_client()
    s.interface = load_service("interface").app.test_client()
    s.routes = {"http://cmt.test": s.cmt, "http://ai.test": s.ai, "http://orch.test": s.orchestrator}
    s.calls = []

    def fake_request(method, url, params=None, data=None, headers=None, timeout=None, **kwargs):
        s.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "params": params})
        for base, client in s.routes.items():
            if url.startswith(base):
                if client is None:
                    raise requests.ConnectionError("server is down")
                return FakeAnswer(client.open(url[len(base):], method=method, query_string=params,
                                              data=data, json=kwargs.get("json"), headers=headers))
        raise requests.ConnectionError("unknown server")

    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(requests.api, "request", fake_request)
    monkeypatch.setattr(requests, "get", lambda url, **kw: fake_request("GET", url, **kw))
    return s
