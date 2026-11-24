"""Tests for T11: reading reviews, recording and releasing decisions, email, author result (UR-06, UR-11, FR-07, FR-08, FR-09, FR-15, SEC-04, SEC-05)."""
import smtplib

import pytest

from conftest import make_conference, signup


@pytest.fixture
def env(cmt, monkeypatch):
    """Conference with a chair, two reviewers, author Alice with co-author Carl, an outsider and papers.

    Paper A has two reviews (scores 4 and 2). Paper B has one assignment without a review.
    """
    client, module = cmt
    import mailer
    import models as m
    mailer.OUTBOX.clear()
    monkeypatch.delenv("MAIL_BACKEND", raising=False)
    e = {"client": client, "module": module, "m": m, "mailer": mailer}
    for name in ("chair", "rev1", "rev2", "alice", "carl", "outsider"):
        e[name] = signup(client, f"{name}@example.com", name.title())
        e[name + "_id"] = client.get("/auth/me", headers=e[name]).get_json()["id"]
    e["conf"] = make_conference(client, e["chair"]).get_json()["id"]
    with module.app.app_context():
        for r in ("rev1", "rev2"):
            m.db.session.add(m.ConferenceRole(user_id=e[r + "_id"], conference_id=e["conf"], role="reviewer"))
        ids = []
        for title in ("Paper A", "Paper B"):
            s = m.Submission(conference_id=e["conf"], title=title, abstract=f"About {title}")
            m.db.session.add(s)
            m.db.session.flush()
            m.db.session.add_all([m.SubmissionAuthor(submission_id=s.id, user_id=e["alice_id"], is_corresponding=True),
                                  m.SubmissionAuthor(submission_id=s.id, user_id=e["carl_id"])])
            ids.append(s.id)
        e["paper_a"], e["paper_b"] = ids
        a1 = m.Assignment(submission_id=e["paper_a"], reviewer_id=e["rev1_id"])
        a2 = m.Assignment(submission_id=e["paper_a"], reviewer_id=e["rev2_id"])
        a3 = m.Assignment(submission_id=e["paper_b"], reviewer_id=e["rev1_id"])
        m.db.session.add_all([a1, a2, a3])
        m.db.session.flush()
        m.db.session.add_all([m.Review(assignment_id=a1.id, overall_score=4, confidence=5, comments="Strong paper, clear."),
                              m.Review(assignment_id=a2.id, overall_score=2, confidence=3, comments="Weak evaluation.")])
        m.db.session.commit()
    return e


def base(e, paper="paper_a"):
    return f"/conferences/{e['conf']}/submissions/{e[paper]}"


def decide(e, outcome="accept", paper="paper_a", by="chair"):
    return e["client"].put(base(e, paper) + "/decision", json={"outcome": outcome}, headers=e[by])


def release(e, paper="paper_a", by="chair"):
    return e["client"].post(base(e, paper) + "/decision/release", headers=e[by])


def result(e, who="alice", paper="paper_a"):
    return e["client"].get(f"/submissions/{e[paper]}/result", headers=e[who])


# ---- the chair reads reviews (FR-07) ----

def test_chair_sees_reviews_and_averages(env):
    r = env["client"].get(base(env) + "/reviews", headers=env["chair"])
    assert r.status_code == 200
    body = r.get_json()
    assert body["average_overall_score"] == 3.0 and body["average_confidence"] == 4.0
    assert (body["assigned"], body["completed"], body["decision_state"]) == (2, 2, "none")
    assert sorted(x["comments"] for x in body["reviews"]) == ["Strong paper, clear.", "Weak evaluation."]
    assert {x["reviewer"]["name"] for x in body["reviews"]} == {"Rev1", "Rev2"}      # the chair may know them


def test_paper_without_reviews_has_no_average(env):
    body = env["client"].get(base(env, "paper_b") + "/reviews", headers=env["chair"]).get_json()
    assert body["average_overall_score"] is None and body["completed"] == 0 and body["assigned"] == 1


def test_results_overview(env):
    decide(env, "accept")
    rows = {p["title"]: p for p in env["client"].get(f"/conferences/{env['conf']}/results", headers=env["chair"]).get_json()}
    assert rows["Paper A"]["average_overall_score"] == 3.0 and rows["Paper A"]["decision_state"] == "recorded"
    assert rows["Paper A"]["outcome"] == "accept"
    assert rows["Paper B"]["decision_state"] == "none" and rows["Paper B"]["outcome"] is None


def test_only_the_chair_can_use_chair_routes(env):
    c = env["client"]
    for who in ("rev1", "alice", "outsider"):
        h = env[who]
        assert c.get(base(env) + "/reviews", headers=h).status_code == 403
        assert c.get(f"/conferences/{env['conf']}/results", headers=h).status_code == 403
        assert decide(env, by=who).status_code == 403
        assert release(env, by=who).status_code == 403
        assert c.post(f"/conferences/{env['conf']}/decisions/release", headers=h).status_code == 403
    assert c.get(base(env) + "/reviews").status_code == 401
    other = signup(c, "otherchair@example.com")
    make_conference(c, other)
    assert c.get(base(env) + "/reviews", headers=other).status_code == 403          # chair of ANOTHER conference
    with env["module"].app.app_context():
        assert env["m"].Decision.query.count() == 0


def test_paper_of_another_conference_gives_404(env):
    c = env["client"]
    other = signup(c, "otherchair@example.com")
    conf2 = make_conference(c, other).get_json()["id"]
    assert c.get(f"/conferences/{conf2}/submissions/{env['paper_a']}/reviews", headers=other).status_code == 404
    assert c.put(f"/conferences/{conf2}/submissions/{env['paper_a']}/decision", json={"outcome": "accept"},
                 headers=other).status_code == 404


# ---- conflict of interest: chair who is an author (SEC-04) ----

@pytest.fixture
def chair_paper(env):
    with env["module"].app.app_context():
        m = env["m"]
        s = m.Submission(conference_id=env["conf"], title="Chair paper", abstract="hidden")
        m.db.session.add(s)
        m.db.session.flush()
        m.db.session.add(m.SubmissionAuthor(submission_id=s.id, user_id=env["chair_id"], is_corresponding=True))
        a = m.Assignment(submission_id=s.id, reviewer_id=env["rev1_id"])
        m.db.session.add(a)
        m.db.session.flush()
        m.db.session.add(m.Review(assignment_id=a.id, overall_score=1, confidence=1, comments="CONFIDENTIAL about chair"))
        m.db.session.add(m.Decision(submission_id=s.id, outcome="reject"))
        m.db.session.commit()
        env["own"] = s.id
    return env


def test_chair_cannot_read_or_decide_on_own_paper(chair_paper):
    e = chair_paper
    c, p = e["client"], f"/conferences/{e['conf']}/submissions/{e['own']}"
    assert c.get(p + "/reviews", headers=e["chair"]).status_code == 403
    assert c.put(p + "/decision", json={"outcome": "accept"}, headers=e["chair"]).status_code == 403
    assert c.post(p + "/decision/release", headers=e["chair"]).status_code == 403
    with e["module"].app.app_context():
        assert e["m"].Decision.query.filter_by(submission_id=e["own"]).one().outcome == "reject"   # unchanged
        assert e["m"].db.session.get(e["m"].Submission, e["own"]).status == "submitted"


def test_results_overview_hides_chair_paper_details(chair_paper):
    e = chair_paper
    text = e["client"].get(f"/conferences/{e['conf']}/results", headers=e["chair"]).get_data(as_text=True)
    assert "CONFIDENTIAL" not in text
    rows = e["client"].get(f"/conferences/{e['conf']}/results", headers=e["chair"]).get_json()
    own = [r for r in rows if r["submission_id"] == e["own"]][0]
    assert own == {"submission_id": e["own"], "title": "Chair paper", "chair_is_author": True}


def test_bulk_release_skips_the_chairs_own_paper(chair_paper):
    e = chair_paper
    decide(e, "accept")
    r = e["client"].post(f"/conferences/{e['conf']}/decisions/release", headers=e["chair"])
    assert r.get_json()["released"] == [e["paper_a"]]
    with e["module"].app.app_context():
        assert e["m"].Decision.query.filter_by(submission_id=e["own"]).one().released is False


# ---- record (FR-08) ----

def test_record_and_change_a_decision(env):
    r = decide(env, "accept")
    assert r.status_code == 200 and r.get_json()["released"] is False and r.get_json()["warnings"] == []
    assert decide(env, "reject").status_code == 200
    with env["module"].app.app_context():
        assert [d.outcome for d in env["m"].Decision.query] == ["reject"]      # changed, not doubled


def test_invalid_outcome_is_refused(env):
    for bad in ("maybe", "ACCEPT", "", None, 1, ["accept"]):
        r = env["client"].put(base(env) + "/decision", json={"outcome": bad}, headers=env["chair"])
        assert r.status_code == 400
    assert env["client"].put(base(env) + "/decision", data="x", headers=env["chair"]).status_code == 400


def test_warning_when_reviews_are_missing(env):
    w = decide(env, "accept", "paper_b").get_json()["warnings"]
    assert w == ["There are no reviews for this paper yet"]
    with env["module"].app.app_context():
        a = env["m"].Assignment(submission_id=env["paper_a"], reviewer_id=env["outsider_id"])
        env["m"].db.session.add(a)
        env["m"].db.session.commit()
    assert decide(env, "accept").get_json()["warnings"] == ["1 of 3 reviews are still missing"]


def test_withdrawn_paper_gets_no_decision(env):
    with env["module"].app.app_context():
        env["m"].db.session.get(env["m"].Submission, env["paper_a"]).status = "withdrawn"
        env["m"].db.session.commit()
    assert decide(env).status_code == 409


# ---- the author sees nothing before release (SEC-05) ----

def test_authors_see_nothing_before_release(env):
    decide(env, "accept")
    for who in ("alice", "carl"):
        r = result(env, who)
        assert r.status_code == 200
        assert r.get_json() == {"submission_id": env["paper_a"], "status": "submitted", "decision_released": False}
        assert "Strong paper" not in r.get_data(as_text=True)
    assert env["mailer"].OUTBOX == []


def test_only_authors_can_see_the_result(env):
    decide(env)
    release(env)
    for who in ("outsider", "rev1", "chair"):          # not even a reviewer of that paper or the chair
        assert result(env, who).status_code == 404
    assert env["client"].get(f"/submissions/{env['paper_a']}/result").status_code == 401
    assert result(env, "alice", paper="paper_b").get_json()["decision_released"] is False


# ---- release and email (FR-09, FR-15) ----

def test_release_sets_status_and_authors_see_decision_and_anonymous_reviews(env):
    decide(env, "accept")
    r = release(env)
    assert r.status_code == 200 and r.get_json()["released"] is True
    for who in ("alice", "carl"):
        body = result(env, who).get_json()
        assert body["decision_released"] is True and body["decision"] == "accept" and body["status"] == "accepted"
        assert sorted(x["comments"] for x in body["reviews"]) == ["Strong paper, clear.", "Weak evaluation."]
        text = str(body).lower()
        for secret in ("rev1", "rev2", "reviewer_id", "assignment", "name", "email"):
            assert secret not in text, secret


def test_reject_sets_status_rejected(env):
    decide(env, "reject")
    release(env)
    assert result(env).get_json()["status"] == "rejected"


def test_emails_go_to_every_author_with_reviews_and_without_reviewer_names(env):
    decide(env, "accept")
    r = release(env)
    assert sorted(r.get_json()["emails"]["sent"]) == ["alice@example.com", "carl@example.com"]
    outbox = env["mailer"].OUTBOX
    assert sorted(m["To"] for m in outbox) == ["alice@example.com", "carl@example.com"]
    msg = outbox[0]
    assert "accepted" in msg.get_body().get_content() and "Paper A" in msg.get_body().get_content()
    attachments = list(msg.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["review-1.txt", "review-2.txt"]
    everything = msg.as_string() + "".join(a.get_content() for a in attachments)
    assert "Strong paper" in everything and "Weak evaluation" in everything
    for secret in ("Rev1", "Rev2", "rev1@example.com", "rev2@example.com"):
        assert secret not in everything, secret


def test_release_needs_a_recorded_decision_and_works_once(env):
    assert release(env).status_code == 409                       # nothing recorded
    decide(env)
    assert release(env).status_code == 200
    assert release(env).status_code == 409                       # not twice, no second email round
    assert len(env["mailer"].OUTBOX) == 2


def test_released_decision_cannot_be_changed(env):
    decide(env, "accept")
    release(env)
    assert decide(env, "reject").status_code == 409
    assert result(env).get_json()["decision"] == "accept"


def test_mail_problem_does_not_undo_the_release(env, monkeypatch):
    def broken(*args, **kwargs):
        raise smtplib.SMTPException("server down")
    monkeypatch.setattr("decisions.send_email", broken)
    decide(env)
    r = release(env)
    assert r.status_code == 200 and sorted(r.get_json()["emails"]["failed"]) == ["alice@example.com", "carl@example.com"]
    assert result(env).get_json()["decision_released"] is True
    with env["module"].app.app_context():
        assert any("email failed" in a.action for a in env["m"].AuditLog.query)


def test_newline_in_title_cannot_add_mail_headers(env):
    with env["module"].app.app_context():
        env["m"].db.session.get(env["m"].Submission, env["paper_a"]).title = "Nice\r\nBcc: spy@evil.example"
        env["m"].db.session.commit()
    decide(env)
    release(env)
    for msg in env["mailer"].OUTBOX:
        assert msg["Bcc"] is None and "\n" not in msg["Subject"]


def test_bulk_release(env):
    decide(env, "accept")
    decide(env, "reject", "paper_b")
    r = env["client"].post(f"/conferences/{env['conf']}/decisions/release", headers=env["chair"])
    assert r.get_json() == {"released": [env["paper_a"], env["paper_b"]], "emails_failed": 0}
    assert len(env["mailer"].OUTBOX) == 4
    again = env["client"].post(f"/conferences/{env['conf']}/decisions/release", headers=env["chair"])
    assert again.get_json()["released"] == []


# ---- audit (SEC-13) ----

def test_decisions_are_audited(env):
    decide(env, "accept")
    decide(env, "reject")
    release(env)
    decide(env, "accept", by="outsider")
    with env["module"].app.app_context():
        actions = [a.action for a in env["m"].AuditLog.query]
    for expected in (f"recorded decision on paper {env['paper_a']}: accept",
                     f"changed decision on paper {env['paper_a']}: reject",
                     f"released decision on paper {env['paper_a']}: reject",
                     f"denied: chair action on conference {env['conf']}"):
        assert expected in actions


# ---- mailer ----

def test_mailer_uses_smtp_when_configured(env, monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): calls.append(("starttls",))
        def login(self, user, password): calls.append(("login", user))
        def send_message(self, message): calls.append(("send", message["To"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "mail.example.org")
    monkeypatch.setenv("SMTP_USER", "cmt")
    env["mailer"].send_email("a@example.com", "Hi", "text")
    assert calls == [("connect", "mail.example.org", 587), ("starttls",), ("login", "cmt"), ("send", "a@example.com")]
    assert env["mailer"].OUTBOX == []
