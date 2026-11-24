"""Tests for T05 on the CMT service: role checks per conference (SEC-03), request checks
and security headers (SEC-09, SEC-12), and the no-static-files rule that T08 relies on."""
import sys

import pytest
from flask import jsonify

from conftest import SERVICE_DIR, make_conference, signup

PAYLOAD = "<script>alert(1)</script>"


@pytest.fixture
def guarded(cmt):
    """The app with three test routes, one per role rule. Routes must be added before the first request."""
    client, module = cmt
    sys.path.insert(0, str(SERVICE_DIR))
    try:
        import access                      # needs the modules the app already loaded (auth, models, audit)
    finally:
        sys.path.remove(str(SERVICE_DIR))

    @module.app.get("/test/<int:conference_id>/chair")
    @access.conference_role_required("chair")
    def chair_only(conference_id):
        return jsonify(ok=True)

    @module.app.get("/test/<int:conference_id>/reviewer")
    @access.conference_role_required("reviewer")
    def reviewer_only(conference_id):
        return jsonify(ok=True)

    @module.app.get("/test/<int:conference_id>/either")
    @access.conference_role_required("chair", "reviewer")
    def chair_or_reviewer(conference_id):
        return jsonify(ok=True)

    return client, module, access


def committee(client):
    """A chair, a reviewer who accepted an invitation, and a stranger with no role. Gives headers and the conference id."""
    chair = signup(client, "chair@example.com", "Chair")
    reviewer = signup(client, "reviewer@example.com", "Reviewer")
    stranger = signup(client, "stranger@example.com", "Stranger")
    conf_id = make_conference(client, chair).get_json()["id"]
    invitation = client.post(f"/conferences/{conf_id}/invitations", json={"email": "reviewer@example.com"},
                             headers=chair).get_json()["id"]
    assert client.post(f"/invitations/{invitation}/accept", headers=reviewer).status_code == 200
    return chair, reviewer, stranger, conf_id


# ---- role checks per conference (SEC-03) ----

def test_role_check_needs_login(guarded):
    client, _, _ = guarded
    assert client.get("/test/1/chair").status_code == 401


def test_role_check_on_unknown_conference_gives_404(guarded):
    client, _, _ = guarded
    chair, *_ = committee(client)
    assert client.get("/test/999/chair", headers=chair).status_code == 404


def test_right_role_gets_in(guarded):
    client, _, _ = guarded
    chair, reviewer, _, conf = committee(client)
    assert client.get(f"/test/{conf}/chair", headers=chair).status_code == 200
    assert client.get(f"/test/{conf}/reviewer", headers=reviewer).status_code == 200
    assert client.get(f"/test/{conf}/either", headers=chair).status_code == 200
    assert client.get(f"/test/{conf}/either", headers=reviewer).status_code == 200


def test_wrong_role_or_no_role_is_refused(guarded):
    client, _, _ = guarded
    chair, reviewer, stranger, conf = committee(client)
    assert client.get(f"/test/{conf}/chair", headers=reviewer).status_code == 403
    assert client.get(f"/test/{conf}/chair", headers=stranger).status_code == 403
    assert client.get(f"/test/{conf}/reviewer", headers=stranger).status_code == 403
    assert client.get(f"/test/{conf}/either", headers=stranger).status_code == 403


def test_a_role_does_not_include_other_roles(guarded):
    """Being chair does not make you a reviewer (a chair has to be added to the committee like anyone else)."""
    client, _, _ = guarded
    chair, _, _, conf = committee(client)
    assert client.get(f"/test/{conf}/reviewer", headers=chair).status_code == 403


def test_role_in_one_conference_gives_nothing_in_another(guarded):
    client, _, _ = guarded
    chair, _, _, conf = committee(client)
    other_chair = signup(client, "other@example.com", "Other chair")
    other_conf = make_conference(client, other_chair).get_json()["id"]
    assert client.get(f"/test/{other_conf}/chair", headers=chair).status_code == 403
    assert client.get(f"/test/{conf}/chair", headers=other_chair).status_code == 403


def test_refused_role_checks_are_audited(guarded):
    client, module, _ = guarded
    _, reviewer, _, conf = committee(client)
    client.get(f"/test/{conf}/chair", headers=reviewer)
    import models
    with module.app.app_context():
        actions = [a.action for a in models.AuditLog.query.all()]
    assert f"denied: chair action on conference {conf}" in actions


def test_role_decorator_without_roles_is_refused_when_defined(guarded):
    """Deny by default: a decorator that names no role would let nobody in, so it is an error."""
    _, _, access = guarded
    with pytest.raises(ValueError):
        access.conference_role_required()


# ---- request checks (SEC-09) ----

def test_body_that_is_not_json_or_an_upload_is_refused(cmt):
    client, _ = cmt
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "application/xml"):
        r = client.post("/auth/login", data="email=a&password=b", content_type=content_type)
        assert r.status_code == 415, content_type


def test_json_body_must_be_an_object_not_a_list_or_a_value(cmt):
    """Before T05, a body like [1] crashed the route with a 500."""
    client, _ = cmt
    headers = signup(client, "someone@example.com")
    for body in ("[1]", '"text"', "42", "true"):
        for path in ("/auth/register", "/auth/login", "/conferences"):
            r = client.post(path, data=body, content_type="application/json", headers=headers)
            assert r.status_code == 400, (path, body)
            assert r.get_json()["error"]


def test_broken_json_is_still_a_clean_400(cmt):
    client, _ = cmt
    r = client.post("/auth/register", data="{broken", content_type="application/json")
    assert r.status_code == 400


def test_request_over_the_size_limit_is_refused(cmt):
    client, _ = cmt
    r = client.post("/auth/login", data=b"x" * (21 * 1024 * 1024 + 1), content_type="application/json")
    assert r.status_code == 413 and r.get_json()["error"]


def test_errors_are_json_and_show_no_details(cmt):
    client, _ = cmt
    missing = client.get("/does-not-exist")
    wrong_method = client.delete("/auth/login")   # login has no DELETE
    assert missing.status_code == 404 and missing.get_json() == {"error": "Not found"}
    assert wrong_method.status_code == 405 and wrong_method.get_json() == {"error": "Method not allowed"}
    assert b"Traceback" not in missing.data + wrong_method.data


# ---- headers and output (SEC-09) ----

def test_security_headers_are_on_every_response(cmt):
    client, _ = cmt
    for r in (client.get("/health"), client.get("/does-not-exist"), client.post("/auth/login", json={})):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Content-Security-Policy"].startswith("default-src 'none'")
        assert r.headers["Cache-Control"] == "no-store"


def test_script_in_user_text_comes_back_as_json_never_as_a_page(cmt):
    """The API stores text as typed and sends it as JSON with nosniff, so a browser cannot run it as HTML."""
    client, _ = cmt
    headers = signup(client, "xss@example.com", PAYLOAD)
    r = client.get("/auth/me", headers=headers)
    assert r.mimetype == "application/json"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.get_json()["name"] == PAYLOAD


def test_service_serves_no_static_files(cmt):
    """T08: uploads must never be reachable as files, so the service has no /static route at all."""
    client, module = cmt
    assert module.app.static_folder is None
    assert client.get("/static/anything.pdf").status_code == 404


# ---- database address (found by starting the system in Docker) ----

def test_a_plain_postgresql_address_gets_the_driver_we_install(cmt, monkeypatch):
    _, module = cmt
    for given, expected in [("postgresql://cmt:pw@db:5432/cmt", "postgresql+psycopg2://cmt:pw@db:5432/cmt"),
                            ("postgres://cmt:pw@db/cmt", "postgresql+psycopg2://cmt:pw@db/cmt"),
                            ("postgresql+psycopg2://cmt:pw@db/cmt", "postgresql+psycopg2://cmt:pw@db/cmt"),
                            ("sqlite://", "sqlite://")]:
        monkeypatch.setenv("DATABASE_URL", given)
        assert module.database_url() == expected
