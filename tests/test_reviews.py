"""Tests for T10: assigned papers, anonymous PDF download, writing and editing reviews."""
import io
from datetime import timedelta

import pytest
from pypdf import PdfReader, PdfWriter

from conftest import auth_header, login, make_conference, register, signup

SECRET_AUTHOR = "Alice Secret"


def make_pdf(path, author=SECRET_AUTHOR):
    """A tiny PDF with the author's name in the metadata and in a comment."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Author": author, "/Title": f"Paper by {author}", "/Creator": author})
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject, FloatObject
    annotation = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(10), FloatObject(10)]),
        NameObject("/T"): TextStringObject(author), NameObject("/Contents"): TextStringObject("note"),
    })
    page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture
def setup(cmt, tmp_path, monkeypatch):
    """A conference with a paper (written by Alice), a reviewer with an assignment, and an outsider.

    Papers and assignments are put straight in the database, those features belong to T07 to T09.
    """
    client, module = cmt
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    make_pdf(tmp_path / "abc123.pdf")
    chair = signup(client, "chair@example.com", "Chair")
    reviewer = signup(client, "reviewer@example.com", "Reviewer")
    author = signup(client, "alice@example.com", SECRET_AUTHOR)
    outsider = signup(client, "outsider@example.com", "Outsider")
    conf_id = make_conference(client, chair).get_json()["id"]
    ids = {e: client.get("/auth/me", headers=h).get_json()["id"]
           for e, h in [("reviewer", reviewer), ("author", author)]}
    import models
    with module.app.app_context():
        db = models.db
        db.session.add(models.ConferenceRole(user_id=ids["reviewer"], conference_id=conf_id, role="reviewer"))
        sub = models.Submission(conference_id=conf_id, title="A paper", abstract="About things", pdf_path="abc123.pdf")
        db.session.add(sub)
        db.session.flush()
        db.session.add(models.SubmissionAuthor(submission_id=sub.id, user_id=ids["author"], is_corresponding=True))
        assignment = models.Assignment(submission_id=sub.id, reviewer_id=ids["reviewer"])
        db.session.add(assignment)
        db.session.commit()
        info = {"assignment": assignment.id, "submission": sub.id, "conference": conf_id}
    return {"client": client, "module": module, "models": models, "reviewer": reviewer, "author": author,
            "outsider": outsider, "chair": chair, "ids": ids, **info}


REVIEW = {"overall_score": 4, "confidence": 3, "comments": "Solid work, clear method."}


def put_review(s, headers=None, **overrides):
    return s["client"].put(f"/assignments/{s['assignment']}/review", json={**REVIEW, **overrides},
                           headers=headers or s["reviewer"])


# ---- assigned papers (UR-08, FR-11) ----

def test_reviewer_sees_assigned_paper_without_author_info(setup):
    s = setup
    papers = s["client"].get("/assignments", headers=s["reviewer"]).get_json()
    assert len(papers) == 1 and papers[0]["title"] == "A paper"
    detail = s["client"].get(f"/assignments/{s['assignment']}", headers=s["reviewer"]).get_json()
    assert SECRET_AUTHOR not in str(detail) and "alice@example.com" not in str(detail)
    assert "authors" not in detail and "author" not in "".join(detail.keys())


def test_routes_need_login(setup):
    c = setup["client"]
    for url in ["/assignments", f"/assignments/{setup['assignment']}", f"/assignments/{setup['assignment']}/pdf"]:
        assert c.get(url).status_code == 401
    assert c.put(f"/assignments/{setup['assignment']}/review", json=REVIEW).status_code == 401


def test_other_users_cannot_see_the_assignment(setup):
    """Security: only the assigned reviewer, not the chair, the author or an outsider."""
    s = setup
    for who in ("outsider", "author", "chair"):
        h = s[who]
        assert s["client"].get(f"/assignments/{s['assignment']}", headers=h).status_code == 404
        assert s["client"].get(f"/assignments/{s['assignment']}/pdf", headers=h).status_code == 404
        assert put_review(s, headers=h).status_code == 404
        assert s["client"].get("/assignments", headers=h).get_json() == []


def test_unknown_assignment_gives_404(setup):
    assert setup["client"].get("/assignments/9999", headers=setup["reviewer"]).status_code == 404


def test_author_cannot_review_own_paper_even_if_assigned(setup):
    """Security (SEC-04): even if the assignment exists by mistake, the author is blocked."""
    s = setup
    with s["module"].app.app_context():
        m = s["models"]
        m.db.session.add(m.ConferenceRole(user_id=s["ids"]["author"], conference_id=s["conference"], role="reviewer"))
        a = m.Assignment(submission_id=s["submission"], reviewer_id=s["ids"]["author"])
        m.db.session.add(a)
        m.db.session.commit()
        aid = a.id
    c = s["client"]
    assert c.get(f"/assignments/{aid}", headers=s["author"]).status_code == 404
    assert c.put(f"/assignments/{aid}/review", json=REVIEW, headers=s["author"]).status_code == 404
    assert c.get("/assignments", headers=s["author"]).get_json() == []


def test_reviewer_without_role_in_conference_is_blocked(setup):
    s = setup
    with s["module"].app.app_context():
        m = s["models"]
        m.ConferenceRole.query.filter_by(user_id=s["ids"]["reviewer"], role="reviewer").delete()
        m.db.session.commit()
    assert s["client"].get(f"/assignments/{s['assignment']}", headers=s["reviewer"]).status_code == 404


def test_withdrawn_paper_disappears(setup):
    s = setup
    with s["module"].app.app_context():
        s["models"].db.session.get(s["models"].Submission, s["submission"]).status = "withdrawn"
        s["models"].db.session.commit()
    assert s["client"].get("/assignments", headers=s["reviewer"]).get_json() == []


# ---- anonymized PDF (FR-11, SEC-05) ----

def test_downloaded_pdf_has_no_author_metadata(setup):
    s = setup
    r = s["client"].get(f"/assignments/{s['assignment']}/pdf", headers=s["reviewer"])
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert SECRET_AUTHOR.encode() not in r.data                      # nowhere in the raw bytes
    reader = PdfReader(io.BytesIO(r.data))
    assert len(reader.pages) == 1                                    # content is still there
    assert not reader.metadata or "/Author" not in reader.metadata
    assert "/Annots" not in reader.pages[0]


def test_original_pdf_really_contains_the_name(setup, tmp_path):
    """Sanity check for the test above: the stored file does have the name in it."""
    assert SECRET_AUTHOR.encode() in (tmp_path / "abc123.pdf").read_bytes()


def test_pdf_path_cannot_escape_upload_folder(setup, tmp_path):
    s = setup
    outside = tmp_path.parent / "outside.pdf"
    make_pdf(outside)
    with s["module"].app.app_context():
        s["models"].db.session.get(s["models"].Submission, s["submission"]).pdf_path = "../outside.pdf"
        s["models"].db.session.commit()
    assert s["client"].get(f"/assignments/{s['assignment']}/pdf", headers=s["reviewer"]).status_code == 404


def test_broken_pdf_is_not_handed_out(setup, tmp_path):
    s = setup
    (tmp_path / "abc123.pdf").write_bytes(b"not a pdf, author: " + SECRET_AUTHOR.encode())
    r = s["client"].get(f"/assignments/{s['assignment']}/pdf", headers=s["reviewer"])
    assert r.status_code == 422 and SECRET_AUTHOR.encode() not in r.data


def test_missing_pdf_gives_404(setup, tmp_path):
    (tmp_path / "abc123.pdf").unlink()
    assert setup["client"].get(f"/assignments/{setup['assignment']}/pdf", headers=setup["reviewer"]).status_code == 404


# ---- writing and editing a review (UR-09, FR-12) ----

def test_submit_then_edit_review(setup):
    s = setup
    assert s["client"].get(f"/assignments/{s['assignment']}/review", headers=s["reviewer"]).status_code == 404
    r = put_review(s)
    assert r.status_code == 201 and r.get_json()["overall_score"] == 4
    r = put_review(s, overall_score=2, comments="On second thought, weak.")
    assert r.status_code == 200
    stored = s["client"].get(f"/assignments/{s['assignment']}/review", headers=s["reviewer"]).get_json()
    assert stored["overall_score"] == 2 and stored["comments"].startswith("On second")
    with s["module"].app.app_context():
        assert s["models"].Review.query.count() == 1               # edited, not duplicated
    paper = s["client"].get(f"/assignments/{s['assignment']}", headers=s["reviewer"]).get_json()
    assert paper["has_review"] is True


@pytest.mark.parametrize("bad", [
    {"overall_score": 0}, {"overall_score": 6}, {"overall_score": "5"}, {"overall_score": True},
    {"overall_score": 3.5}, {"confidence": 0}, {"confidence": None}, {"comments": ""}, {"comments": "   "},
    {"comments": ["x"]}, {"comments": "x" * 10001},
])
def test_invalid_review_is_rejected(setup, bad):
    assert put_review(setup, **bad).status_code == 400
    with setup["module"].app.app_context():
        assert setup["models"].Review.query.count() == 0


def test_missing_fields_rejected(setup):
    r = setup["client"].put(f"/assignments/{setup['assignment']}/review", json={"comments": "only text"},
                            headers=setup["reviewer"])
    assert r.status_code == 400


# ---- deadline (SEC-06) ----

def expire_review_deadline(s):
    with s["module"].app.app_context():
        conf = s["models"].db.session.get(s["models"].Conference, s["conference"])
        conf.review_deadline = s["models"].now() - timedelta(minutes=1)
        s["models"].db.session.commit()


def test_cannot_create_review_after_deadline(setup):
    s = setup
    expire_review_deadline(s)
    assert put_review(s).status_code == 403
    with s["module"].app.app_context():
        assert s["models"].Review.query.count() == 0


def test_cannot_edit_review_after_deadline(setup):
    s = setup
    assert put_review(s).status_code == 201
    expire_review_deadline(s)
    assert put_review(s, overall_score=1, comments="changed my mind").status_code == 403
    stored = s["client"].get(f"/assignments/{s['assignment']}/review", headers=s["reviewer"]).get_json()
    assert stored["overall_score"] == 4                              # unchanged


def test_review_is_still_readable_after_deadline(setup):
    s = setup
    put_review(s)
    expire_review_deadline(s)
    assert s["client"].get(f"/assignments/{s['assignment']}/review", headers=s["reviewer"]).status_code == 200


def test_denied_review_actions_are_audited(setup):
    s = setup
    expire_review_deadline(s)
    put_review(s)
    put_review(s, headers=s["outsider"])
    with s["module"].app.app_context():
        actions = [a.action for a in s["models"].AuditLog.query.all()]
    assert f"denied: review after deadline on assignment {s['assignment']}" in actions
