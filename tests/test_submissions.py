"""Tests for T07: submit, view, edit/replace and withdraw papers (UR-10, FR-13, FR-14, SEC-06)."""
import io
import os
import pathlib
from datetime import timedelta

import pytest
from pypdf import PdfWriter

from conftest import auth_header, make_conference, signup

SECRET = "Alice Secret"


def pdf_bytes(author=SECRET):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Author": author, "/Title": f"Paper by {author}"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def form(title="My paper", abstract="About things", pdf=True, coauthors=None, **extra):
    data = {"title": title, "abstract": abstract, **extra}
    if pdf:
        data["pdf"] = (io.BytesIO(pdf_bytes() if pdf is True else pdf), "paper.pdf")
    if coauthors is not None:
        data["coauthors"] = coauthors
    return data


@pytest.fixture
def env(cmt, tmp_path, monkeypatch):
    """A conference, its chair, two authors, a stranger, and an empty upload folder."""
    client, module = cmt
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    e = {"client": client, "module": module, "dir": tmp_path}
    for name in ("chair", "author", "coauthor", "stranger"):
        e[name] = signup(client, f"{name}@example.com", name.title())
    e["conf"] = make_conference(client, e["chair"]).get_json()["id"]
    import models
    e["models"] = models
    return e


def submit(e, who="author", conf=None, **kwargs):
    return e["client"].post(f"/conferences/{conf or e['conf']}/submissions", data=form(**kwargs),
                            headers=e[who], content_type="multipart/form-data")


def files_in(e):
    return sorted(p.name for p in pathlib.Path(e["dir"]).iterdir())


def edit(e, sid, who="author", **fields):
    data = {k: v for k, v in fields.items()}
    return e["client"].patch(f"/submissions/{sid}", data=data, headers=e[who], content_type="multipart/form-data")


def expire_deadline(e):
    with e["module"].app.app_context():
        conf = e["models"].db.session.get(e["models"].Conference, e["conf"])
        conf.submission_deadline = e["models"].now() - timedelta(minutes=1)
        e["models"].db.session.commit()


# ---- submit (UR-10, FR-13) ----

def test_submit_paper(env):
    r = submit(env, coauthors="coauthor@example.com")
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "submitted" and body["title"] == "My paper"
    assert [(a["email"], a["is_corresponding"]) for a in body["authors"]] == [
        ("author@example.com", True), ("coauthor@example.com", False)]
    assert "pdf_path" not in body and "pdf_path" not in str(body)
    # both are now authors in this conference
    assert env["client"].get(f"/conferences/{env['conf']}", headers=env["coauthor"]).get_json()["my_roles"] == ["author"]


def test_stored_file_has_random_name_and_no_author_metadata(env):
    submit(env)
    names = files_in(env)
    assert len(names) == 1 and len(names[0]) == 36 and names[0].endswith(".pdf")   # 32 hex + .pdf
    assert SECRET.encode() not in (env["dir"] / names[0]).read_bytes()


def test_submit_needs_login_and_existing_conference(env):
    c = env["client"]
    assert c.post(f"/conferences/{env['conf']}/submissions", data=form()).status_code == 401
    assert submit(env, conf=999).status_code == 404


@pytest.mark.parametrize("bad", [{"title": ""}, {"title": "x" * 256}, {"abstract": ""}, {"abstract": "x" * 5001},
                                 {"pdf": False}, {"coauthors": "nobody@example.com"},
                                 {"coauthors": "not-an-email"}, {"coauthors": ",".join(f"u{i}@example.com" for i in range(21))}])
def test_invalid_submission_is_refused_and_nothing_is_stored(env, bad):
    assert submit(env, **bad).status_code == 400
    assert files_in(env) == []
    with env["module"].app.app_context():
        assert env["models"].Submission.query.count() == 0


def test_only_pdfs_are_accepted(env):
    assert submit(env, pdf=b"just some text, not a pdf").status_code == 415
    assert submit(env, pdf=b"%PDF-1.4 but broken").status_code == 422
    assert files_in(env) == []


def test_submitting_after_the_deadline_is_refused(env):
    """Security (SEC-06): checked on the server, nothing is stored."""
    expire_deadline(env)
    r = submit(env)
    assert r.status_code == 403
    assert files_in(env) == []


def test_unsafe_fields_in_the_form_are_ignored(env):
    r = submit(env, status="accepted", conference_id="999", pdf_path="../../etc/passwd", id="77")
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "submitted" and body["conference_id"] == env["conf"] and body["id"] != 77
    assert files_in(env) != ["../../etc/passwd"]


def test_coauthor_list_is_cleaned(env):
    r = submit(env, coauthors=["Coauthor@Example.com, coauthor@example.com", "author@example.com"])
    assert [a["email"] for a in r.get_json()["authors"]] == ["author@example.com", "coauthor@example.com"]


# ---- who can see what ----

def test_authors_see_their_papers_and_nobody_else_does(env):
    c = env["client"]
    sid = submit(env, coauthors="coauthor@example.com").get_json()["id"]
    for who in ("author", "coauthor"):
        assert [s["id"] for s in c.get("/submissions", headers=env[who]).get_json()] == [sid]
        assert c.get(f"/submissions/{sid}", headers=env[who]).status_code == 200
    for who in ("stranger", "chair"):             # the chair gets an overview of a conference in T09
        assert c.get("/submissions", headers=env[who]).get_json() == []
        assert c.get(f"/submissions/{sid}", headers=env[who]).status_code == 404
        assert c.get(f"/submissions/{sid}/pdf", headers=env[who]).status_code == 404
    assert c.get(f"/submissions/{sid}").status_code == 401


def test_author_can_download_own_pdf(env):
    sid = submit(env).get_json()["id"]
    r = env["client"].get(f"/submissions/{sid}/pdf", headers=env["author"])
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF-")


# ---- edit and replace (FR-14) ----

def test_edit_title_and_abstract(env):
    sid = submit(env).get_json()["id"]
    r = edit(env, sid, title="New title", abstract="New abstract")
    assert r.status_code == 200 and r.get_json()["title"] == "New title"
    assert env["client"].get(f"/submissions/{sid}", headers=env["author"]).get_json()["abstract"] == "New abstract"


def test_replace_pdf_removes_the_old_file(env):
    sid = submit(env).get_json()["id"]
    old = files_in(env)
    r = env["client"].patch(f"/submissions/{sid}", headers=env["author"], content_type="multipart/form-data",
                            data={"pdf": (io.BytesIO(pdf_bytes("Bob Other")), "v2.pdf")})
    assert r.status_code == 200
    new = files_in(env)
    assert len(new) == 1 and new != old                 # replaced, not piled up


def test_bad_replacement_pdf_keeps_the_old_one(env):
    sid = submit(env).get_json()["id"]
    old = files_in(env)
    r = env["client"].patch(f"/submissions/{sid}", headers=env["author"], content_type="multipart/form-data",
                            data={"pdf": (io.BytesIO(b"nope"), "v2.pdf")})
    assert r.status_code == 415 and files_in(env) == old
    assert env["client"].get(f"/submissions/{sid}/pdf", headers=env["author"]).status_code == 200


def test_edit_can_change_coauthors(env):
    sid = submit(env, coauthors="coauthor@example.com").get_json()["id"]
    r = edit(env, sid, coauthors="stranger@example.com")
    assert [a["email"] for a in r.get_json()["authors"]] == ["author@example.com", "stranger@example.com"]
    assert env["client"].get(f"/submissions/{sid}", headers=env["coauthor"]).status_code == 404
    r = edit(env, sid, coauthors="")                       # an empty value removes them all
    assert [a["email"] for a in r.get_json()["authors"]] == ["author@example.com"]


def test_edit_after_the_deadline_is_refused(env):
    """Security (SEC-06): the paper stays as it was."""
    sid = submit(env).get_json()["id"]
    before = files_in(env)
    expire_deadline(env)
    assert edit(env, sid, title="Too late").status_code == 403
    r = env["client"].patch(f"/submissions/{sid}", headers=env["author"], content_type="multipart/form-data",
                            data={"pdf": (io.BytesIO(pdf_bytes()), "late.pdf")})
    assert r.status_code == 403 and files_in(env) == before
    assert env["client"].get(f"/submissions/{sid}", headers=env["author"]).get_json()["title"] == "My paper"


def test_only_the_corresponding_author_can_edit_or_withdraw(env):
    sid = submit(env, coauthors="coauthor@example.com").get_json()["id"]
    assert edit(env, sid, who="coauthor", title="Mine now").status_code == 403
    assert env["client"].post(f"/submissions/{sid}/withdraw", headers=env["coauthor"]).status_code == 403
    assert edit(env, sid, who="stranger", title="x").status_code == 404
    assert env["client"].post(f"/submissions/{sid}/withdraw", headers=env["stranger"]).status_code == 404
    assert env["client"].get(f"/submissions/{sid}", headers=env["author"]).get_json()["title"] == "My paper"


def test_edit_validation(env):
    sid = submit(env).get_json()["id"]
    assert edit(env, sid, title="").status_code == 400
    assert edit(env, sid, abstract="x" * 5001).status_code == 400
    assert edit(env, sid, coauthors="nobody@example.com").status_code == 400


def test_edit_ignores_status_field(env):
    sid = submit(env).get_json()["id"]
    r = edit(env, sid, title="Fine", status="accepted", conference_id="5")
    assert r.get_json()["status"] == "submitted" and r.get_json()["conference_id"] == env["conf"]


# ---- withdraw (UR-10, FR-14) ----

def test_withdraw(env):
    sid = submit(env).get_json()["id"]
    r = env["client"].post(f"/submissions/{sid}/withdraw", headers=env["author"])
    assert r.status_code == 200 and r.get_json()["status"] == "withdrawn"
    assert env["client"].post(f"/submissions/{sid}/withdraw", headers=env["author"]).status_code == 409
    assert edit(env, sid, title="x").status_code == 409                # a withdrawn paper cannot be changed


def test_decided_paper_cannot_be_withdrawn_or_edited(env):
    sid = submit(env).get_json()["id"]
    with env["module"].app.app_context():
        env["models"].db.session.get(env["models"].Submission, sid).status = "accepted"
        env["models"].db.session.commit()
    assert env["client"].post(f"/submissions/{sid}/withdraw", headers=env["author"]).status_code == 409
    assert edit(env, sid, title="x").status_code == 409


def test_withdrawn_paper_disappears_for_the_reviewer(env):
    """Works together with T10: a reviewer no longer sees a withdrawn paper."""
    client, m = env["client"], env["models"]
    sid = submit(env).get_json()["id"]
    reviewer = env["stranger"]
    rid = client.get("/auth/me", headers=reviewer).get_json()["id"]
    with env["module"].app.app_context():
        m.db.session.add_all([m.ConferenceRole(user_id=rid, conference_id=env["conf"], role="reviewer"),
                              m.Assignment(submission_id=sid, reviewer_id=rid)])
        m.db.session.commit()
    assert len(client.get("/assignments", headers=reviewer).get_json()) == 1
    client.post(f"/submissions/{sid}/withdraw", headers=env["author"])
    assert client.get("/assignments", headers=reviewer).get_json() == []


def test_submission_actions_are_audited(env):
    sid = submit(env).get_json()["id"]
    edit(env, sid, title="Changed")
    env["client"].post(f"/submissions/{sid}/withdraw", headers=env["author"])
    expire_deadline(env)
    submit(env, who="coauthor")
    with env["module"].app.app_context():
        actions = [a.action for a in env["models"].AuditLog.query]
    for expected in (f"submitted paper {sid} to conference {env['conf']}", f"edited paper {sid}",
                     f"withdrew paper {sid}"):
        assert expected in actions
    assert any(a.startswith("denied: submission after the submission deadline") for a in actions)
