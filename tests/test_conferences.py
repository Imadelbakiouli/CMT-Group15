"""Tests for T06: conferences, invitations, accept and decline."""
from conftest import CONFERENCE, make_conference, signup


def setup_chair_and_reviewer(client):
    chair = signup(client, "chair@example.com", "Chair")
    reviewer = signup(client, "reviewer@example.com", "Reviewer")
    conf_id = make_conference(client, chair).get_json()["id"]
    return chair, reviewer, conf_id


def invite(client, chair, conf_id, email="reviewer@example.com"):
    return client.post(f"/conferences/{conf_id}/invitations", json={"email": email}, headers=chair)


# ---- create / read / update (UR-03, FR-03) ----

def test_create_conference_makes_creator_chair(cmt):
    client, _ = cmt
    chair = signup(client, "chair@example.com")
    r = make_conference(client, chair)
    assert r.status_code == 201
    assert r.get_json()["my_roles"] == ["chair"]
    assert r.get_json()["submission_deadline"].startswith("2028-03-01T12:00:00")


def test_conference_routes_need_login(cmt):
    client, _ = cmt
    assert client.post("/conferences", json=CONFERENCE).status_code == 401
    assert client.get("/conferences").status_code == 401
    assert client.get("/invitations").status_code == 401


def test_create_conference_rejects_bad_input(cmt):
    client, _ = cmt
    chair = signup(client, "chair@example.com")
    assert make_conference(client, chair, name="").status_code == 400
    assert make_conference(client, chair, submission_deadline="soon").status_code == 400
    assert make_conference(client, chair, review_deadline=None).status_code == 400
    # review deadline before submission deadline
    assert make_conference(client, chair, review_deadline="2028-02-01T00:00:00Z").status_code == 400
    # notification before review
    assert make_conference(client, chair, notification_deadline="2028-03-15T00:00:00Z").status_code == 400


def test_chair_can_update_details_and_deadlines(cmt):
    client, _ = cmt
    chair = signup(client, "chair@example.com")
    conf_id = make_conference(client, chair).get_json()["id"]
    r = client.patch(f"/conferences/{conf_id}", headers=chair,
                     json={"name": "EAMT 2028 (updated)", "submission_deadline": "2028-02-15T12:00:00Z"})
    assert r.status_code == 200
    assert r.get_json()["name"] == "EAMT 2028 (updated)"
    assert r.get_json()["submission_deadline"].startswith("2028-02-15")
    # an update that messes up the order is refused, nothing changes
    bad = client.patch(f"/conferences/{conf_id}", headers=chair, json={"review_deadline": "2028-01-01T00:00:00Z"})
    assert bad.status_code == 400
    assert client.get(f"/conferences/{conf_id}", headers=chair).get_json()["review_deadline"].startswith("2028-04-01")


def test_non_chair_cannot_update_conference(cmt):
    client, _ = cmt
    chair, other, conf_id = setup_chair_and_reviewer(client)
    r = client.patch(f"/conferences/{conf_id}", headers=other, json={"name": "Hacked"})
    assert r.status_code == 403
    assert client.get(f"/conferences/{conf_id}", headers=chair).get_json()["name"] == "EAMT 2028"


def test_chair_of_one_conference_is_not_chair_of_another(cmt):
    client, _ = cmt
    chair_a = signup(client, "a@example.com")
    chair_b = signup(client, "b@example.com")
    conf_a = make_conference(client, chair_a).get_json()["id"]
    make_conference(client, chair_b)
    assert client.patch(f"/conferences/{conf_a}", headers=chair_b, json={"name": "x"}).status_code == 403
    assert invite(client, chair_b, conf_a, "a@example.com").status_code == 403


def test_unknown_conference_gives_404(cmt):
    client, _ = cmt
    chair = signup(client, "chair@example.com")
    assert client.get("/conferences/999", headers=chair).status_code == 404
    assert client.patch("/conferences/999", headers=chair, json={"name": "x"}).status_code == 404


# ---- invite (UR-04, FR-04) ----

def test_chair_invites_registered_user(cmt):
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    r = invite(client, chair, conf_id)
    assert r.status_code == 201 and r.get_json()["status"] == "pending"
    listing = client.get(f"/conferences/{conf_id}/invitations", headers=chair).get_json()
    assert [i["user"]["email"] for i in listing] == ["reviewer@example.com"]


def test_only_chair_can_invite_or_list_invitations(cmt):
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    assert invite(client, reviewer, conf_id, "chair@example.com").status_code == 403
    assert client.get(f"/conferences/{conf_id}/invitations", headers=reviewer).status_code == 403


def test_invite_edge_cases(cmt):
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    assert invite(client, chair, conf_id, "nobody@example.com").status_code == 404
    assert client.post(f"/conferences/{conf_id}/invitations", json={}, headers=chair).status_code == 400
    assert invite(client, chair, conf_id).status_code == 201
    assert invite(client, chair, conf_id).status_code == 409          # still pending


# ---- accept / decline (UR-07, FR-10) ----

def test_accept_makes_user_reviewer(cmt):
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    inv_id = invite(client, chair, conf_id).get_json()["id"]
    mine = client.get("/invitations", headers=reviewer).get_json()
    assert [(i["id"], i["status"]) for i in mine] == [(inv_id, "pending")]
    assert client.post(f"/invitations/{inv_id}/accept", headers=reviewer).status_code == 200
    assert client.get(f"/conferences/{conf_id}", headers=reviewer).get_json()["my_roles"] == ["reviewer"]
    assert invite(client, chair, conf_id).status_code == 409          # already a reviewer


def test_decline_gives_no_role_and_can_be_reinvited(cmt):
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    inv_id = invite(client, chair, conf_id).get_json()["id"]
    assert client.post(f"/invitations/{inv_id}/decline", headers=reviewer).status_code == 200
    assert client.get(f"/conferences/{conf_id}", headers=reviewer).get_json()["my_roles"] == []
    again = invite(client, chair, conf_id)
    assert again.status_code == 201 and again.get_json()["id"] == inv_id and again.get_json()["status"] == "pending"


def test_invitation_cannot_be_answered_twice(cmt):
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    inv_id = invite(client, chair, conf_id).get_json()["id"]
    client.post(f"/invitations/{inv_id}/decline", headers=reviewer)
    assert client.post(f"/invitations/{inv_id}/accept", headers=reviewer).status_code == 409
    assert client.get(f"/conferences/{conf_id}", headers=reviewer).get_json()["my_roles"] == []


def test_cannot_answer_someone_elses_invitation(cmt):
    """Security: you can not accept an invitation that was meant for someone else."""
    client, _ = cmt
    chair, reviewer, conf_id = setup_chair_and_reviewer(client)
    intruder = signup(client, "intruder@example.com")
    inv_id = invite(client, chair, conf_id).get_json()["id"]
    assert client.post(f"/invitations/{inv_id}/accept", headers=intruder).status_code == 404
    assert client.get("/invitations", headers=intruder).get_json() == []
    assert client.get(f"/conferences/{conf_id}", headers=intruder).get_json()["my_roles"] == []


def test_self_promotion_is_not_possible(cmt):
    """Security: nobody can give themselves a role."""
    client, _ = cmt
    chair, other, conf_id = setup_chair_and_reviewer(client)
    r = client.post(f"/conferences/{conf_id}/invitations", json={"email": "reviewer@example.com"}, headers=other)
    assert r.status_code == 403
    assert client.get(f"/conferences/{conf_id}", headers=other).get_json()["my_roles"] == []


def test_denied_actions_are_audited(cmt):
    client, module = cmt
    chair, other, conf_id = setup_chair_and_reviewer(client)
    client.patch(f"/conferences/{conf_id}", headers=other, json={"name": "x"})
    import models
    with module.app.app_context():
        actions = [a.action for a in models.AuditLog.query.all()]
    assert f"denied: chair action on conference {conf_id}" in actions
