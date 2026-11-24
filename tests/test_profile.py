"""Tests for T04: edit profile, change password, export personal data, delete account."""
from datetime import timedelta

import bcrypt

from conftest import USER, auth_header, login, make_conference, register, signup

PASSWORD = USER["password"]
EMAIL = "test.user@example.com"


def make_user(client, email=EMAIL, name="Test User"):
    register(client, email=email, name=name)
    return auth_header(login(client, email=email).get_json()["token"])


def models_of(module):
    import models
    return models


# ---- edit profile (FR-02) ----

def test_edit_name_and_affiliation(cmt):
    client, _ = cmt
    h = make_user(client)
    r = client.patch("/auth/me", json={"name": "New Name", "affiliation": "UvA"}, headers=h)
    assert r.status_code == 200
    me = client.get("/auth/me", headers=h).get_json()
    assert me["name"] == "New Name" and me["affiliation"] == "UvA"
    client.patch("/auth/me", json={"affiliation": ""}, headers=h)
    assert client.get("/auth/me", headers=h).get_json()["affiliation"] is None


def test_profile_routes_need_login(cmt):
    client, _ = cmt
    assert client.patch("/auth/me", json={"name": "x"}).status_code == 401
    assert client.get("/auth/me/export").status_code == 401
    assert client.delete("/auth/me", json={}).status_code == 401
    assert client.post("/auth/me/password", json={}).status_code == 401


def test_edit_rejects_bad_input(cmt):
    client, _ = cmt
    h = make_user(client)
    assert client.patch("/auth/me", json={"name": ""}, headers=h).status_code == 400
    assert client.patch("/auth/me", json={"name": "x" * 121}, headers=h).status_code == 400
    assert client.patch("/auth/me", json={"name": ["a"]}, headers=h).status_code == 400
    assert client.patch("/auth/me", json={"affiliation": "x" * 256}, headers=h).status_code == 400
    assert client.patch("/auth/me", json={"email": "nope"}, headers=h).status_code == 400


def test_edit_cannot_change_id_password_or_other_fields(cmt):
    """Security: extra fields in the request are ignored (no mass assignment)."""
    client, module = cmt
    h = make_user(client)
    me = client.get("/auth/me", headers=h).get_json()
    client.patch("/auth/me", json={"name": "Same", "id": 999, "password_hash": "x", "failed_logins": 0,
                                   "is_admin": True}, headers=h)
    assert client.get("/auth/me", headers=h).get_json()["id"] == me["id"]
    assert login(client).status_code == 200           # password still works


def test_change_email_needs_correct_password(cmt):
    client, _ = cmt
    h = make_user(client)
    assert client.patch("/auth/me", json={"email": "new@example.com"}, headers=h).status_code == 403
    assert client.patch("/auth/me", json={"email": "new@example.com", "current_password": "wrong-wrong-1"},
                        headers=h).status_code == 403
    r = client.patch("/auth/me", json={"email": "NEW@example.com", "current_password": PASSWORD}, headers=h)
    assert r.status_code == 200 and r.get_json()["email"] == "new@example.com"
    assert login(client, email="new@example.com").status_code == 200
    assert login(client, email=EMAIL).status_code == 401            # the old email no longer works


def test_change_email_to_existing_one_is_refused(cmt):
    client, _ = cmt
    h = make_user(client)
    make_user(client, email="other@example.com")
    r = client.patch("/auth/me", json={"email": "Other@example.com", "current_password": PASSWORD}, headers=h)
    assert r.status_code == 409
    assert client.get("/auth/me", headers=h).get_json()["email"] == EMAIL


def test_same_email_in_other_case_needs_no_password(cmt):
    client, _ = cmt
    h = make_user(client)
    assert client.patch("/auth/me", json={"email": EMAIL.upper(), "name": "Other"}, headers=h).status_code == 200


def test_guessing_the_password_through_the_profile_locks_the_account(cmt):
    """Security (SEC-12): the profile route is not a way around the login limit."""
    client, _ = cmt
    h = make_user(client)
    for _ in range(5):
        assert client.patch("/auth/me", json={"email": "x@example.com", "current_password": "wrong-wrong-1"},
                            headers=h).status_code == 403
    assert client.patch("/auth/me", json={"email": "x@example.com", "current_password": PASSWORD},
                        headers=h).status_code == 429
    assert login(client).status_code == 429


# ---- change password ----

def test_change_password(cmt):
    client, module = cmt
    h = make_user(client)
    other_login = auth_header(login(client).get_json()["token"])      # a second login of the same user
    r = client.post("/auth/me/password", json={"current_password": PASSWORD, "new_password": "a-brand-new-pass"},
                    headers=h)
    assert r.status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 200       # current session stays
    assert client.get("/auth/me", headers=other_login).status_code == 401   # other sessions are ended
    assert login(client).status_code == 401
    assert login(client, password="a-brand-new-pass").status_code == 200
    m = models_of(module)
    with module.app.app_context():
        stored = m.User.query.one().password_hash
    assert stored.startswith("$2") and bcrypt.checkpw(b"a-brand-new-pass", stored.encode())


def test_change_password_checks(cmt):
    client, _ = cmt
    h = make_user(client)
    assert client.post("/auth/me/password", json={"current_password": "wrong-wrong-1",
                                                   "new_password": "a-brand-new-pass"}, headers=h).status_code == 403
    assert client.post("/auth/me/password", json={"current_password": PASSWORD, "new_password": "short"},
                       headers=h).status_code == 400
    assert client.post("/auth/me/password", json={"current_password": PASSWORD, "new_password": "a" * 73},
                       headers=h).status_code == 400
    assert login(client).status_code == 200                           # nothing changed


# ---- export (UR-02, FR-18, NFR-06) ----

def seed_data(client, module, h, other_h):
    """The user is author of a paper, wrote a review, and was invited. Another user has data as well."""
    chair = make_user(client, email="chair@example.com", name="Chair")
    conf_id = make_conference(client, chair).get_json()["id"]
    m = models_of(module)
    me = client.get("/auth/me", headers=h).get_json()["id"]
    other = client.get("/auth/me", headers=other_h).get_json()["id"]
    with module.app.app_context():
        db = m.db
        sub = m.Submission(conference_id=conf_id, title="My paper", abstract="Mine", pdf_path="secret-name.pdf")
        other_sub = m.Submission(conference_id=conf_id, title="Their paper", abstract="Theirs")
        db.session.add_all([sub, other_sub])
        db.session.flush()
        db.session.add_all([m.SubmissionAuthor(submission_id=sub.id, user_id=me, is_corresponding=True),
                            m.SubmissionAuthor(submission_id=other_sub.id, user_id=other)])
        a1 = m.Assignment(submission_id=other_sub.id, reviewer_id=me)
        a2 = m.Assignment(submission_id=sub.id, reviewer_id=other)
        db.session.add_all([a1, a2, m.Invitation(conference_id=conf_id, user_id=me, status="accepted"),
                            m.ConferenceRole(user_id=me, conference_id=conf_id, role="reviewer"),
                            m.Decision(submission_id=sub.id, outcome="accept", released=True)])
        db.session.flush()
        db.session.add_all([m.Review(assignment_id=a1.id, overall_score=4, confidence=3, comments="my review text"),
                            m.Review(assignment_id=a2.id, overall_score=1, confidence=5, comments="SECRET review of my paper")])
        db.session.commit()


def test_export_contains_own_data(cmt):
    client, module = cmt
    h, other_h = make_user(client), make_user(client, email="other@example.com", name="Other Person")
    seed_data(client, module, h, other_h)
    r = client.get("/auth/me/export", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    data = r.get_json()
    assert data["profile"]["email"] == EMAIL
    assert [s["title"] for s in data["submissions_as_author"]] == ["My paper"]
    assert data["submissions_as_author"][0]["released_decision"] == "accept"
    assert [x["comments"] for x in data["reviews_written"]] == ["my review text"]
    assert data["roles"] == [{"conference_id": 1, "role": "reviewer"}]
    assert data["invitations"] == [{"conference_id": 1, "status": "accepted"}]
    assert "logged in" in [a["action"] for a in data["activity_log"]]


def test_export_contains_nothing_of_other_people_or_secrets(cmt):
    """Security/privacy: no password hash, no other users, no reviews that others wrote about my paper."""
    client, module = cmt
    h, other_h = make_user(client), make_user(client, email="other@example.com", name="Other Person")
    seed_data(client, module, h, other_h)
    text = client.get("/auth/me/export", headers=h).get_data(as_text=True)
    for forbidden in ["password_hash", "$2b$", "other@example.com", "Other Person", "chair@example.com",
                      "Their paper", "SECRET review", "secret-name.pdf", "token"]:
        assert forbidden not in text, forbidden


def test_export_is_logged(cmt):
    client, module = cmt
    h = make_user(client)
    client.get("/auth/me/export", headers=h)
    m = models_of(module)
    with module.app.app_context():
        assert "exported personal data" in [a.action for a in m.AuditLog.query]


# ---- delete account (FR-18, NFR-06) ----

def test_delete_account_needs_the_password(cmt):
    client, _ = cmt
    h = make_user(client)
    assert client.delete("/auth/me", headers=h, json={}).status_code == 403
    assert client.delete("/auth/me", headers=h, json={"current_password": "wrong-wrong-1"}).status_code == 403
    assert client.get("/auth/me", headers=h).status_code == 200       # still there


def test_delete_account_removes_personal_data_and_access(cmt):
    client, module = cmt
    h, other_h = make_user(client), make_user(client, email="other@example.com", name="Other Person")
    seed_data(client, module, h, other_h)
    login_again = auth_header(login(client).get_json()["token"])
    r = client.delete("/auth/me", headers=h, json={"current_password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 401          # all sessions are gone
    assert client.get("/auth/me", headers=login_again).status_code == 401
    assert login(client).status_code == 401
    m = models_of(module)
    with module.app.app_context():
        user = m.User.query.filter(m.User.email.like("deleted-%")).one()
        assert user.name == "Deleted user" and user.affiliation is None
        assert EMAIL not in user.email and not bcrypt.checkpw(PASSWORD.encode(), user.password_hash.encode())
        assert m.UserSession.query.filter_by(user_id=user.id).count() == 0
        assert "account deleted (anonymised)" in [a.action for a in m.AuditLog.query.filter_by(user_id=user.id)]
        # the papers and reviews stay in the conference, nothing breaks
        assert m.Submission.query.count() == 2 and m.Review.query.count() == 2
    # the other person is not touched and the email can be used again
    assert client.get("/auth/me", headers=other_h).get_json()["name"] == "Other Person"
    assert register(client).status_code == 201


def test_delete_removes_pending_invitations_only(cmt):
    client, module = cmt
    chair = make_user(client, email="chair@example.com")
    h = make_user(client)
    conf_id = make_conference(client, chair).get_json()["id"]
    client.post(f"/conferences/{conf_id}/invitations", json={"email": EMAIL}, headers=chair)
    client.delete("/auth/me", headers=h, json={"current_password": PASSWORD})
    m = models_of(module)
    with module.app.app_context():
        assert m.Invitation.query.count() == 0


def test_cannot_delete_somebody_elses_account(cmt):
    """Security: the route only ever touches the logged-in user. There is no id in the URL to change."""
    client, module = cmt
    h, other_h = make_user(client), make_user(client, email="other@example.com")
    other_id = client.get("/auth/me", headers=other_h).get_json()["id"]
    assert client.delete(f"/auth/me/{other_id}", headers=h, json={"current_password": PASSWORD}).status_code == 404
    assert client.get("/auth/me", headers=other_h).status_code == 200
