"""Tests for T09: chair overview, assigning reviewers with conflict check, review progress (UR-04, UR-05, FR-05, FR-06, SEC-04)."""
from datetime import timedelta

import pytest

from conftest import make_conference, signup


@pytest.fixture
def env(cmt):
    """A conference with: a chair, two reviewers (committee), author Alice (UvA), an outsider and two papers.

    Papers are put straight in the database (the submit route is T07).
    """
    client, module = cmt
    import models as m
    e = {"client": client, "module": module, "m": m}
    for name, affiliation in [("chair", "VU"), ("rev1", "UvA"), ("rev2", "TU"), ("alice", "UvA"), ("outsider", "X")]:
        e[name] = signup(client, f"{name}@example.com", name.title())
        e[name + "_id"] = client.get("/auth/me", headers=e[name]).get_json()["id"]
        with module.app.app_context():     # set directly, so this test file does not depend on T04
            m.db.session.get(m.User, e[name + "_id"]).affiliation = affiliation
            m.db.session.commit()
    e["conf"] = make_conference(client, e["chair"]).get_json()["id"]
    with module.app.app_context():
        for r in ("rev1", "rev2"):
            m.db.session.add(m.ConferenceRole(user_id=e[r + "_id"], conference_id=e["conf"], role="reviewer"))
        papers = []
        for title in ("Paper A", "Paper B"):
            s = m.Submission(conference_id=e["conf"], title=title, abstract=f"About {title}")
            m.db.session.add(s)
            m.db.session.flush()
            m.db.session.add(m.SubmissionAuthor(submission_id=s.id, user_id=e["alice_id"], is_corresponding=True))
            papers.append(s.id)
        m.db.session.commit()
        e["paper_a"], e["paper_b"] = papers
    return e


def url(e, paper="paper_a", tail="assignments"):
    return f"/conferences/{e['conf']}/submissions/{e[paper]}/{tail}"


def assign(e, who="rev2", paper="paper_a", by="chair", **body):
    body = body or {"reviewer_email": f"{who}@example.com"}
    return e["client"].post(url(e, paper), json=body, headers=e[by])


def write_review(e, assignment_id):
    with e["module"].app.app_context():
        e["m"].db.session.add(e["m"].Review(assignment_id=assignment_id, overall_score=3, confidence=3, comments="ok"))
        e["m"].db.session.commit()


# ---- who may use these routes (SEC-03) ----

def test_only_the_chair_can_use_the_chair_routes(env):
    c = env["client"]
    paths = [("get", f"/conferences/{env['conf']}/submissions"), ("get", f"/conferences/{env['conf']}/committee"),
             ("get", f"/conferences/{env['conf']}/review-progress"), ("post", url(env)),
             ("delete", url(env, tail="assignments/1"))]
    for who in ("rev1", "alice", "outsider"):
        for method, path in paths:
            assert getattr(c, method)(path, json={"reviewer_email": "rev2@example.com"}, headers=env[who]).status_code == 403
    for method, path in paths:
        assert getattr(c, method)(path).status_code == 401


def test_chair_of_another_conference_has_no_access(env):
    c = env["client"]
    other = signup(c, "otherchair@example.com")
    make_conference(c, other)
    assert c.get(f"/conferences/{env['conf']}/submissions", headers=other).status_code == 403
    assert c.post(url(env), json={"reviewer_email": "rev2@example.com"}, headers=other).status_code == 403


# ---- overview (FR-05) ----

def test_chair_sees_submissions_with_authors_and_counts(env):
    r = env["client"].get(f"/conferences/{env['conf']}/submissions", headers=env["chair"])
    assert r.status_code == 200
    papers = r.get_json()
    assert [p["title"] for p in papers] == ["Paper A", "Paper B"]
    assert papers[0]["authors"][0]["email"] == "alice@example.com"
    assert papers[0]["reviewers_assigned"] == 0 and papers[0]["reviews_done"] == 0


def test_committee_list_with_workload(env):
    assign(env, "rev1")
    assign(env, "rev1", paper="paper_b")
    members = env["client"].get(f"/conferences/{env['conf']}/committee", headers=env["chair"]).get_json()
    assert {m["email"]: m["assigned"] for m in members} == {"rev1@example.com": 2, "rev2@example.com": 0}


# ---- assign (UR-04, FR-05) ----

def test_assign_reviewer_by_email_or_id(env):
    r = assign(env, "rev2")
    assert r.status_code == 201 and r.get_json()["reviewer"]["email"] == "rev2@example.com"
    r = assign(env, paper="paper_b", reviewer_id=env["rev2_id"])
    assert r.status_code == 201
    # the reviewer now sees the paper (T10) and the status changed
    assert len(env["client"].get("/assignments", headers=env["rev2"]).get_json()) == 2
    papers = env["client"].get(f"/conferences/{env['conf']}/submissions", headers=env["chair"]).get_json()
    assert [p["status"] for p in papers] == ["under review", "under review"]
    assert papers[0]["reviewers_assigned"] == 1


def test_assigned_paper_shows_no_author_to_the_reviewer(env):
    """The reviewer side still hides the authors (SEC-05), also after assignment by the chair."""
    assign(env, "rev2")
    text = env["client"].get("/assignments", headers=env["rev2"]).get_data(as_text=True)
    assert "alice" not in text.lower() and "UvA" not in text


def test_author_cannot_be_assigned_to_own_paper(env):
    """Security (SEC-04, FR-05): conflict of interest, also for a co-author."""
    with env["module"].app.app_context():
        env["m"].db.session.add(env["m"].ConferenceRole(user_id=env["alice_id"], conference_id=env["conf"], role="reviewer"))
        env["m"].db.session.commit()
    r = assign(env, "alice")
    assert r.status_code == 409 and "Conflict of interest" in r.get_json()["error"]
    # alice can review Paper-B-like papers by others, but not her own co-authored ones
    with env["module"].app.app_context():
        env["m"].db.session.add(env["m"].SubmissionAuthor(submission_id=env["paper_b"], user_id=env["rev1_id"]))
        env["m"].db.session.commit()
    assert assign(env, "rev1", paper="paper_b").status_code == 409
    assert assign(env, "rev1", paper="paper_a").status_code == 201      # not an author of paper A


def test_reviewer_must_be_in_the_committee(env):
    assert assign(env, "outsider").status_code == 409
    assert assign(env, reviewer_email="nobody@example.com").status_code == 409
    assert assign(env, reviewer_id=9999).status_code == 409
    assert assign(env, reviewer_id=True).status_code == 400
    assert env["client"].post(url(env), json={}, headers=env["chair"]).status_code == 400


def test_committee_of_another_conference_does_not_count(env):
    other_chair = signup(env["client"], "chair2@example.com")
    conf2 = make_conference(env["client"], other_chair).get_json()["id"]
    with env["module"].app.app_context():
        env["m"].db.session.add(env["m"].ConferenceRole(user_id=env["outsider_id"], conference_id=conf2, role="reviewer"))
        env["m"].db.session.commit()
    assert assign(env, "outsider").status_code == 409


def test_same_reviewer_twice_is_refused(env):
    assert assign(env, "rev2").status_code == 201
    assert assign(env, "rev2").status_code == 409


def test_same_affiliation_gives_a_warning_but_is_allowed(env):
    r = assign(env, "rev1")                       # rev1 and alice are both UvA
    assert r.status_code == 201 and len(r.get_json()["warnings"]) == 1
    assert assign(env, "rev2").get_json()["warnings"] == []


def test_paper_of_another_conference_or_unknown_paper(env):
    other_chair = signup(env["client"], "chair2@example.com")
    conf2 = make_conference(env["client"], other_chair).get_json()["id"]
    r = env["client"].post(f"/conferences/{conf2}/submissions/{env['paper_a']}/assignments",
                           json={"reviewer_email": "rev2@example.com"}, headers=other_chair)
    assert r.status_code == 404               # paper A belongs to another conference
    assert env["client"].post(f"/conferences/{env['conf']}/submissions/999/assignments",
                              json={"reviewer_email": "rev2@example.com"}, headers=env["chair"]).status_code == 404


def test_withdrawn_or_decided_paper_cannot_get_reviewers(env):
    with env["module"].app.app_context():
        env["m"].db.session.get(env["m"].Submission, env["paper_a"]).status = "withdrawn"
        env["m"].db.session.get(env["m"].Submission, env["paper_b"]).status = "accepted"
        env["m"].db.session.commit()
    assert assign(env, "rev2").status_code == 409
    assert assign(env, "rev2", paper="paper_b").status_code == 409


# ---- chair who is an author (A3, SEC-04) ----

@pytest.fixture
def chair_paper(env):
    with env["module"].app.app_context():
        s = env["m"].Submission(conference_id=env["conf"], title="Chair's own paper", abstract="secret abstract")
        env["m"].db.session.add(s)
        env["m"].db.session.flush()
        env["m"].db.session.add(env["m"].SubmissionAuthor(submission_id=s.id, user_id=env["chair_id"], is_corresponding=True))
        env["m"].db.session.commit()
        env["own"] = s.id
    return env


def test_chair_cannot_assign_reviewers_to_own_paper(chair_paper):
    e = chair_paper
    r = e["client"].post(f"/conferences/{e['conf']}/submissions/{e['own']}/assignments",
                         json={"reviewer_email": "rev2@example.com"}, headers=e["chair"])
    assert r.status_code == 403
    with e["module"].app.app_context():
        assert e["m"].Assignment.query.filter_by(submission_id=e["own"]).count() == 0


def test_chair_gets_no_details_of_own_paper(chair_paper):
    e = chair_paper
    # a reviewer is assigned to it by other means (here: directly), still no details for the chair
    with e["module"].app.app_context():
        e["m"].db.session.add(e["m"].Assignment(submission_id=e["own"], reviewer_id=e["rev1_id"]))
        e["m"].db.session.commit()
    listing = e["client"].get(f"/conferences/{e['conf']}/submissions", headers=e["chair"]).get_data(as_text=True)
    assert "secret abstract" not in listing
    own = [p for p in e["client"].get(f"/conferences/{e['conf']}/submissions", headers=e["chair"]).get_json()
           if p["id"] == e["own"]][0]
    assert own == {"id": e["own"], "title": "Chair's own paper", "status": "submitted", "chair_is_author": True}
    progress = e["client"].get(f"/conferences/{e['conf']}/review-progress", headers=e["chair"]).get_json()
    assert e["own"] not in [p["submission_id"] for p in progress["papers"]]
    assert e["client"].delete(f"/conferences/{e['conf']}/submissions/{e['own']}/assignments/1", headers=e["chair"]).status_code in (403, 404)


# ---- unassign ----

def test_unassign_reviewer(env):
    aid = assign(env, "rev2").get_json()["assignment_id"]
    r = env["client"].delete(url(env, tail=f"assignments/{aid}"), headers=env["chair"])
    assert r.status_code == 200
    assert env["client"].get("/assignments", headers=env["rev2"]).get_json() == []
    papers = env["client"].get(f"/conferences/{env['conf']}/submissions", headers=env["chair"]).get_json()
    assert papers[0]["status"] == "submitted"                  # no reviewers left


def test_cannot_unassign_after_the_review_is_written(env):
    aid = assign(env, "rev2").get_json()["assignment_id"]
    write_review(env, aid)
    assert env["client"].delete(url(env, tail=f"assignments/{aid}"), headers=env["chair"]).status_code == 409
    with env["module"].app.app_context():
        assert env["m"].Assignment.query.count() == 1


def test_unassign_wrong_paper_or_unknown(env):
    aid = assign(env, "rev2").get_json()["assignment_id"]
    assert env["client"].delete(url(env, "paper_b", f"assignments/{aid}"), headers=env["chair"]).status_code == 404
    assert env["client"].delete(url(env, tail="assignments/999"), headers=env["chair"]).status_code == 404


# ---- review progress (UR-05, FR-06) ----

def test_progress_shows_missing_reviews(env):
    a1 = assign(env, "rev1").get_json()["assignment_id"]
    assign(env, "rev2")
    write_review(env, a1)
    r = env["client"].get(f"/conferences/{env['conf']}/review-progress", headers=env["chair"]).get_json()
    paper_a, paper_b = r["papers"]
    assert (paper_a["assigned"], paper_a["completed"]) == (2, 1)
    assert [m["reviewer"]["email"] for m in paper_a["missing_reviews"]] == ["rev2@example.com"]
    assert paper_b["needs_reviewers"] is True and paper_a["needs_reviewers"] is False
    assert r["deadline_passed"] is False and r["overdue"] == []


def test_progress_flags_overdue_papers_after_the_review_deadline(env):
    assign(env, "rev2")
    with env["module"].app.app_context():
        conf = env["m"].db.session.get(env["m"].Conference, env["conf"])
        conf.review_deadline = env["m"].now() - timedelta(days=1)
        env["m"].db.session.commit()
    r = env["client"].get(f"/conferences/{env['conf']}/review-progress", headers=env["chair"]).get_json()
    assert r["deadline_passed"] is True and r["overdue"] == [env["paper_a"]]


def test_progress_leaves_out_withdrawn_papers(env):
    with env["module"].app.app_context():
        env["m"].db.session.get(env["m"].Submission, env["paper_b"]).status = "withdrawn"
        env["m"].db.session.commit()
    papers = env["client"].get(f"/conferences/{env['conf']}/review-progress", headers=env["chair"]).get_json()["papers"]
    assert [p["title"] for p in papers] == ["Paper A"]


# ---- audit log (SEC-13) ----

def test_assignments_and_refusals_are_audited(env):
    aid = assign(env, "rev2").get_json()["assignment_id"]
    with env["module"].app.app_context():
        env["m"].db.session.add(env["m"].ConferenceRole(user_id=env["alice_id"], conference_id=env["conf"], role="reviewer"))
        env["m"].db.session.commit()
    assign(env, "alice")
    env["client"].delete(url(env, tail=f"assignments/{aid}"), headers=env["chair"])
    with env["module"].app.app_context():
        actions = [a.action for a in env["m"].AuditLog.query]
    assert f"assigned reviewer {env['rev2_id']} to submission {env['paper_a']}" in actions
    assert f"denied: reviewer {env['alice_id']} is an author of paper {env['paper_a']}" in actions
    assert f"removed reviewer {env['rev2_id']} from submission {env['paper_a']}" in actions
