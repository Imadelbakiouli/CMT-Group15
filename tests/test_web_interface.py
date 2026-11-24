"""Tests for the web interface (NFR-04): the page itself and the login cookie of the interface server.

The page talks to /api/... like any other client, so everything behind it is already tested elsewhere.
Here we test what is new: the page is served safely, the login token is kept in an HttpOnly cookie,
and a whole flow works with only that cookie (no Authorization header), like in a browser.
"""
import io
import pathlib
import re

import pytest
from pypdf import PdfWriter

from conftest import CONFERENCE, USER

STATIC = pathlib.Path(__file__).resolve().parents[1] / "interface" / "static"


def csrf_headers(stack):
    """Do what the page does: open the site once, then send the CSRF cookie value as a header."""
    stack.interface.get("/")
    return {"X-CSRF-Token": stack.interface.get_cookie("csrf_token").value}


def web_login(stack, email="web@example.com"):
    """Register and log in through the interface like the web page (no Authorization header)."""
    headers = csrf_headers(stack)
    stack.interface.post("/api/cmt/auth/register", json={**USER, "email": email}, headers=headers)
    return stack.interface.post("/api/cmt/auth/login", json={"email": email, "password": USER["password"]},
                                headers=headers)


def small_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---- the page ----

def test_home_page_is_the_web_interface(stack):
    r = stack.interface.get("/")
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert b"/static/app.js" in r.data and b"login-form" in r.data


def test_page_files_are_served(stack):
    assert stack.interface.get("/static/app.js").status_code == 200
    assert stack.interface.get("/static/style.css").status_code == 200


def test_page_is_protected_by_security_headers(stack):
    """Security (SEC-09): scripts only from this site, the page cannot be put in a frame."""
    headers = stack.interface.get("/").headers
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"


def test_page_has_no_inline_scripts():
    """Security (SEC-09): the CSP blocks inline scripts, so the page must not need any."""
    html = (STATIC / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)                  # every <script> has a src
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)          # no onclick=, onsubmit=, ...
    assert "<style" not in html and "style=" not in html                  # styles only from style.css


def test_page_never_inserts_server_text_as_html():
    """Security (SEC-09, XSS): text from the server is always inserted as text (textContent), never as HTML."""
    js = re.sub(r"//.*", "", (STATIC / "app.js").read_text())        # ignore comments
    for dangerous in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert dangerous not in js


# ---- the login cookie ----

def test_login_puts_the_token_in_an_httponly_cookie(stack):
    """Security (SEC-01): JavaScript cannot read the login token, so an injected script cannot steal it."""
    r = web_login(stack)
    assert r.status_code == 200
    cookie = next(c for c in r.headers.getlist("Set-Cookie") if c.startswith("cmt_session="))
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_the_cookie_alone_is_enough_to_use_the_api(stack):
    web_login(stack)
    me = stack.interface.get("/api/cmt/auth/me")             # no Authorization header, only the cookie
    assert me.status_code == 200 and me.get_json()["email"] == "web@example.com"


def test_wrong_password_sets_no_login_cookie(stack):
    headers = csrf_headers(stack)
    stack.interface.post("/api/cmt/auth/register", json={**USER, "email": "x@example.com"}, headers=headers)
    r = stack.interface.post("/api/cmt/auth/login", json={"email": "x@example.com", "password": "wrong-password!"},
                             headers=headers)
    assert r.status_code == 401
    assert stack.interface.get_cookie("cmt_session") is None


def test_logout_removes_the_cookie_and_ends_the_login(stack):
    web_login(stack)
    r = stack.interface.post("/api/cmt/auth/logout", headers=csrf_headers(stack))
    assert r.status_code == 200
    assert stack.interface.get_cookie("cmt_session") is None
    assert stack.interface.get("/api/cmt/auth/me").status_code == 401


def test_a_made_up_cookie_gives_no_access_and_is_removed(stack):
    """Security (SEC-03): the cookie is only a carrier; the CMT service still checks the token."""
    stack.interface.set_cookie("cmt_session", "made-up-token")
    r = stack.interface.get("/api/cmt/auth/me")
    assert r.status_code == 401
    assert stack.interface.get_cookie("cmt_session") is None


def test_the_cookie_does_not_help_without_a_csrf_token(stack):
    """Security (SEC-12): because the login is now a cookie, CSRF protection matters. Without the token: refused."""
    web_login(stack)
    r = stack.interface.post("/api/cmt/conferences", json=CONFERENCE)
    assert r.status_code == 403


# ---- a whole flow like in the browser ----

def test_web_flow_create_conference_and_submit_a_paper(stack, tmp_path, monkeypatch):
    """Integration: create a conference and upload a PDF with only the cookie and the CSRF token."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    web_login(stack)
    headers = csrf_headers(stack)
    r = stack.interface.post("/api/cmt/conferences", json=CONFERENCE, headers=headers)
    assert r.status_code == 201
    conference_id = r.get_json()["id"]

    data = {"title": "Web paper", "abstract": "Sent from the web page", "pdf": (io.BytesIO(small_pdf()), "paper.pdf")}
    r = stack.interface.post(f"/api/cmt/conferences/{conference_id}/submissions", data=data, headers=headers,
                             content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    papers = stack.interface.get("/api/cmt/submissions").get_json()
    assert [p["title"] for p in papers] == ["Web paper"]


def test_web_flow_ai_assistant_with_the_cookie(stack):
    """Integration: the chat on the page reaches the AI agent, which acts with the same login."""
    web_login(stack)
    r = stack.interface.post("/api/ai/chat", json={"message": "show my papers"}, headers=csrf_headers(stack))
    assert r.status_code == 200 and r.get_json()["type"] == "result"
