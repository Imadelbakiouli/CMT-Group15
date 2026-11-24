"""Tests for T05 on the interface server: CSRF protection and security headers (SEC-12).

No database is needed. Routes are added to the real interface app, because there are no
write routes there yet; the protection must work for every route that is added later.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
METHODS = ("post", "put", "patch", "delete")


@pytest.fixture
def web(monkeypatch):
    """The interface app with a test route for every unsafe method. Gives (client, app)."""
    monkeypatch.setenv("CSRF_SECRET", "secret-for-tests")
    monkeypatch.setenv("COOKIE_SECURE", "false")          # tests run over plain http
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    folder = ROOT / "interface"
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location("interface_app_csrf_test", folder / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(folder))
    for method in METHODS:
        module.app.add_url_rule("/write", f"write_{method}", lambda: ("changed", 200), methods=[method.upper()])
    return module.app.test_client(), module.app


def token_from(client):
    """Do what the browser does: open the site once and read the CSRF cookie."""
    client.get("/")
    return client.get_cookie("csrf_token").value


def send(client, method, token=None, **headers):
    if token:
        headers["X-CSRF-Token"] = token
    return getattr(client, method)("/write", headers=headers)


def test_visiting_the_site_sets_a_strict_csrf_cookie(web):
    client, _ = web
    r = client.get("/")
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("csrf_token=") and "SameSite=Strict" in cookie


def test_cookie_is_marked_secure_unless_switched_off(web, monkeypatch):
    client, _ = web
    monkeypatch.setenv("COOKIE_SECURE", "true")
    assert "Secure" in client.get("/").headers["Set-Cookie"]


def test_health_check_does_not_set_a_cookie(web):
    client, _ = web
    assert "Set-Cookie" not in client.get("/health").headers


def test_reading_never_needs_a_token(web):
    client, _ = web
    assert client.get("/").status_code == 200 and client.get("/health").status_code == 200


def test_every_unsafe_method_is_refused_without_a_token(web):
    client, _ = web
    token_from(client)                                    # has the cookie, but sends no header
    for method in METHODS:
        r = send(client, method)
        assert r.status_code == 403 and "CSRF" in r.get_json()["error"], method


def test_every_unsafe_method_works_with_the_token(web):
    client, _ = web
    token = token_from(client)
    for method in METHODS:
        assert send(client, method, token).status_code == 200, method


def test_request_without_cookie_is_refused_even_with_a_header(web):
    client, _ = web
    token = token_from(web[1].test_client())              # a token from somewhere else
    assert send(client, "post", token).status_code == 403


def test_header_that_differs_from_the_cookie_is_refused(web):
    client, app = web
    token_from(client)
    other = token_from(app.test_client())                 # a valid token, but not the one in this cookie
    assert send(client, "post", other).status_code == 403


def test_forged_token_planted_by_an_attacker_is_refused(web):
    """Cookie and header match, but the value was not made by this server (no valid signature)."""
    client, _ = web
    client.set_cookie("csrf_token", "attacker-value.0000")
    assert send(client, "post", "attacker-value.0000").status_code == 403


def test_token_signed_with_another_secret_is_refused(web, monkeypatch):
    client, _ = web
    token = token_from(client)
    monkeypatch.setenv("CSRF_SECRET", "a-different-secret")
    assert send(client, "post", token).status_code == 403


def test_request_from_another_site_is_refused_even_with_a_valid_token(web):
    client, _ = web
    token = token_from(client)
    assert send(client, "post", token, Origin="https://evil.example").status_code == 403
    assert send(client, "post", token, Referer="https://evil.example/page").status_code == 403


def test_request_from_this_site_is_accepted(web):
    client, _ = web
    token = token_from(client)
    assert send(client, "post", token, Origin="http://localhost").status_code == 200


def test_extra_allowed_origin_can_be_configured(web, monkeypatch):
    client, _ = web
    token = token_from(client)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://cmt.example.org")
    assert send(client, "post", token, Origin="https://cmt.example.org").status_code == 200
    assert send(client, "post", token, Origin="https://evil.example").status_code == 403


def test_security_headers_are_on_every_response(web):
    client, _ = web
    for r in (client.get("/"), client.get("/health"), client.get("/does-not-exist"), client.post("/write")):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in r.headers["Content-Security-Policy"]     # no inline scripts: stops injected scripts
