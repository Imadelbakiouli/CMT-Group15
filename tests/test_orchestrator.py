"""Integration tests for T12: interface -> orchestrator -> CMT service / AI agent."""
import pytest
import requests

from conftest import CONFERENCE, USER, auth_header


def via_interface(stack, method, path, **kwargs):
    """Call the interface like a browser does. Sends a CSRF token too when the interface hands one out."""
    stack.interface.get("/")
    cookie = stack.interface.get_cookie("csrf_token")
    headers = kwargs.pop("headers", {})
    if cookie:
        headers["X-CSRF-Token"] = cookie.value
    return stack.interface.open(path, method=method, headers=headers, **kwargs)


def register_and_login(stack, email="chair@example.com"):
    r = via_interface(stack, "POST", "/api/cmt/auth/register", json={**USER, "email": email})
    assert r.status_code == 201
    r = via_interface(stack, "POST", "/api/cmt/auth/login", json={"email": email, "password": USER["password"]})
    return auth_header(r.get_json()["token"])


# ---- the whole chain works ----

def test_full_chain_register_login_and_create_conference(stack):
    headers = register_and_login(stack)
    me = via_interface(stack, "GET", "/api/cmt/auth/me", headers=headers)
    assert me.status_code == 200 and me.get_json()["email"] == "chair@example.com"
    r = via_interface(stack, "POST", "/api/cmt/conferences", json=CONFERENCE, headers=headers)
    assert r.status_code == 201 and r.get_json()["my_roles"] == ["chair"]
    listing = via_interface(stack, "GET", "/api/cmt/conferences", headers=headers).get_json()
    assert [c["name"] for c in listing] == ["EAMT 2028"]


def test_every_request_really_goes_through_the_orchestrator(stack):
    register_and_login(stack)
    urls = [c["url"] for c in stack.calls]
    assert "http://orch.test/api/cmt/auth/register" in urls
    assert "http://cmt.test/auth/register" in urls
    # the interface never talks to the CMT service directly
    assert not any(c["url"].startswith("http://cmt.test") and c["headers"].get("X-Via") for c in stack.calls)


def test_error_answers_of_the_cmt_service_come_through_unchanged(stack):
    no_login = via_interface(stack, "GET", "/api/cmt/conferences")
    assert no_login.status_code == 401 and "error" in no_login.get_json()
    bad = via_interface(stack, "POST", "/api/cmt/auth/register", json={"name": ""})
    assert bad.status_code == 400


def test_login_token_is_checked_by_the_cmt_service_not_the_orchestrator(stack):
    """Security (SEC-03): the orchestrator only passes the header on. A fake token still gets a 401."""
    r = via_interface(stack, "GET", "/api/cmt/auth/me", headers={"Authorization": "Bearer made-up-token"})
    assert r.status_code == 401


def test_query_string_is_passed_on(stack):
    stack.orchestrator.get("/api/cmt/conferences?page=2")
    assert stack.calls[-1]["params"].to_dict() == {"page": "2"}


# ---- what is NOT passed on / allowed (SEC-09) ----

@pytest.mark.parametrize("path", [
    "/api/cmt/../health", "/api/cmt/auth/../../x", "/api/cmt/auth/%2e%2e/x", "/api/cmt/auth//me",
    "/api/cmt/http://evil.example/x", "/api/cmt/auth/me%00", "/api/cmt/auth;x/me", "/api/cmt/a b",
])
def test_odd_paths_are_refused(stack, path):
    r = stack.orchestrator.get(path)
    assert r.status_code in (400, 404)
    assert not any(c["url"].startswith("http://cmt.test") for c in stack.calls)   # nothing reached the service


def test_only_allowed_parts_of_the_api_are_reachable(stack):
    """Deny by default: /health of the CMT service or an unknown name is not reachable through the API."""
    assert stack.orchestrator.get("/api/cmt/health").status_code == 404
    assert stack.orchestrator.get("/api/cmt/secret").status_code == 404
    assert stack.orchestrator.get("/api/ai/health").status_code == 404
    assert not stack.calls


def test_only_safe_headers_are_passed_on(stack):
    stack.orchestrator.get("/api/cmt/auth/me", headers={
        "Authorization": "Bearer x", "Cookie": "session=secret", "X-Forwarded-For": "1.2.3.4", "X-Admin": "yes"})
    sent = stack.calls[-1]["headers"]
    assert sent.get("Authorization") == "Bearer x"
    assert not {"Cookie", "X-Forwarded-For", "X-Admin"} & set(sent)


def test_cookies_from_the_service_are_not_given_to_the_browser(stack, monkeypatch):
    class Answer:
        status_code, content = 200, b"{}"
        headers = {"Content-Type": "application/json", "Set-Cookie": "evil=1", "Server": "internal-thing/1.0"}
    monkeypatch.setattr(requests, "request", lambda *a, **k: Answer())
    r = stack.orchestrator.get("/api/cmt/conferences")
    assert "Set-Cookie" not in r.headers and "Server" not in r.headers
    assert r.headers["Content-Type"] == "application/json"


def test_unsupported_method_is_refused(stack):
    assert stack.orchestrator.open("/api/cmt/conferences", method="TRACE").status_code == 405


def test_too_big_request_is_refused_before_it_travels_on(stack):
    r = stack.orchestrator.post("/api/cmt/conferences", data=b"x" * (22 * 1024 * 1024),
                                headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert not stack.calls


# ---- when something is down ----

def test_cmt_service_down_gives_502_not_a_crash(stack):
    stack.routes["http://cmt.test"] = None
    r = via_interface(stack, "GET", "/api/cmt/conferences")
    assert r.status_code == 502 and r.get_json() == {"error": "The service cannot be reached"}


def test_timeout_gives_504(stack, monkeypatch):
    def slow(*args, **kwargs):
        raise requests.Timeout()
    monkeypatch.setattr(requests, "request", slow)
    assert stack.orchestrator.get("/api/cmt/conferences").status_code == 504


def test_orchestrator_down_gives_502_at_the_interface(stack):
    stack.routes["http://orch.test"] = None
    r = via_interface(stack, "GET", "/api/cmt/conferences")
    assert r.status_code == 502


def test_health_all_shows_which_component_is_down(stack):
    assert stack.orchestrator.get("/health/all").get_json() == {
        "orchestrator": "ok", "cmt_service": "ok", "ai_agent": "ok"}
    stack.routes["http://ai.test"] = None
    assert stack.orchestrator.get("/health/all").get_json()["ai_agent"] == "unreachable"
    chain = stack.interface.get("/health/all").get_json()
    assert chain["interface"] == "ok" and chain["backend"]["ai_agent"] == "unreachable"


# ---- AI agent route ----

def test_ai_requests_go_to_the_ai_agent_only(stack):
    stack.orchestrator.post("/api/ai/chat", json={"message": "hello"}, headers={"Authorization": "Bearer x"})
    assert stack.calls[-1]["url"] == "http://ai.test/chat"
    assert stack.calls[-1]["headers"]["Authorization"] == "Bearer x"   # so the agent can act with the user's rights
