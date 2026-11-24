"""Submissions (T07): authors submit, edit, replace and withdraw papers.

Related requirements: UR-10, FR-13, FR-14, SEC-06 (and SEC-04, SEC-10 through uploads.py).

A paper is sent as a form (multipart/form-data), because it has a PDF with it:
    title, abstract, pdf (the file), coauthors (emails of co-authors, optional, repeat or separate with commas)

Rules:
- only before the submission deadline (checked here on the server, SEC-06)
- co-authors must have an account (we link them by email)
- the person who submits is the corresponding author. Only that person can edit or withdraw
- every author can see the paper and its status, nobody else can (not even the chair, that is T09)
- the PDF is checked and cleaned by uploads.py (T08) before it is stored
"""
import re

from flask import Blueprint, g, jsonify, request, send_file

from audit import log_action
from auth import EMAIL_RE, clean_text, error, login_required
from conferences import as_utc
from models import Conference, ConferenceRole, Submission, SubmissionAuthor, User, db, now
from uploads import UploadError, delete_pdf, store_pdf, upload_dir

submission_bp = Blueprint("submissions", __name__)

MAX_COAUTHORS = 20
MAX_ABSTRACT = 5000
FINAL_STATUSES = ("accepted", "rejected", "withdrawn")


def submission_json(submission):
    """What an author may see. The name of the stored file is internal and is not shown."""
    authors = (db.session.query(SubmissionAuthor, User)
               .join(User, User.id == SubmissionAuthor.user_id)
               .filter(SubmissionAuthor.submission_id == submission.id)
               .order_by(SubmissionAuthor.is_corresponding.desc(), User.id).all())
    return {
        "id": submission.id, "conference_id": submission.conference_id,
        "title": submission.title, "abstract": submission.abstract, "status": submission.status,
        "has_pdf": bool(submission.pdf_path),
        "authors": [{"name": u.name, "email": u.email, "is_corresponding": a.is_corresponding} for a, u in authors],
    }


def get_my_submission(submission_id, corresponding_only=False):
    """Find a submission that the logged-in user wrote. Returns (submission, None) or (None, error response).

    Not an author gives the same 404 as a paper that does not exist.
    """
    submission = db.session.get(Submission, submission_id)
    link = SubmissionAuthor.query.filter_by(submission_id=submission_id, user_id=g.user.id).first() \
        if submission else None
    if link is None:
        return None, error("Submission not found", 404)
    if corresponding_only and not link.is_corresponding:
        return None, error("Only the corresponding author (the person who submitted) can do this", 403)
    return submission, None


def parse_coauthors():
    """Co-author emails from the form. Returns (list of users, None) or (None, error response)."""
    emails = []
    for value in request.form.getlist("coauthors"):
        emails += [e.strip().lower() for e in re.split(r"[,;\s]+", value) if e.strip()]
    emails = [e for e in dict.fromkeys(emails) if e != g.user.email]   # no doubles, not yourself
    if len(emails) > MAX_COAUTHORS:
        return None, error(f"At most {MAX_COAUTHORS} co-authors", 400)
    if any(len(e) > 255 or not EMAIL_RE.match(e) for e in emails):
        return None, error("A co-author email address is not valid", 400)
    users = User.query.filter(User.email.in_(emails)).all() if emails else []
    missing = sorted(set(emails) - {u.email for u in users})
    if missing:
        return None, error("These co-authors have no account yet: " + ", ".join(missing), 400)
    return users, None


def check_deadline(conference, user_id, what):
    """SEC-06: after the submission deadline nothing can be created or changed."""
    if now() > as_utc(conference.submission_deadline):
        log_action(user_id, f"denied: {what} after the submission deadline of conference {conference.id}")
        db.session.commit()
        return error("The submission deadline has passed", 403)
    return None


def pdf_from_form(required):
    """Store the uploaded PDF. Returns (new file name or None, None) or (None, error response)."""
    file = request.files.get("pdf")
    if file is None:
        return (None, error("A PDF file is required", 400)) if required else (None, None)
    try:
        return store_pdf(file), None
    except UploadError as e:
        return None, error(e.message, e.status)


def give_author_role(user_id, conference_id):
    if not ConferenceRole.query.filter_by(user_id=user_id, conference_id=conference_id, role="author").first():
        db.session.add(ConferenceRole(user_id=user_id, conference_id=conference_id, role="author"))


# ---- submit (UR-10, FR-13) ----

@submission_bp.post("/conferences/<int:conference_id>/submissions")
@login_required
def submit_paper(conference_id):
    conference = db.session.get(Conference, conference_id)
    if conference is None:
        return error("Conference not found", 404)
    if (problem := check_deadline(conference, g.user.id, "submission")):
        return problem

    title = clean_text(request.form.get("title"), 255, required=True)
    abstract = clean_text(request.form.get("abstract"), MAX_ABSTRACT, required=True)
    if title is None:
        return error("A title is required (max 255 characters)", 400)
    if abstract is None:
        return error(f"An abstract is required (max {MAX_ABSTRACT} characters)", 400)
    coauthors, problem = parse_coauthors()
    if problem:
        return problem
    pdf_name, problem = pdf_from_form(required=True)
    if problem:
        return problem

    try:
        submission = Submission(conference_id=conference_id, title=title, abstract=abstract, pdf_path=pdf_name)
        db.session.add(submission)
        db.session.flush()
        db.session.add(SubmissionAuthor(submission_id=submission.id, user_id=g.user.id, is_corresponding=True))
        give_author_role(g.user.id, conference_id)
        for user in coauthors:
            db.session.add(SubmissionAuthor(submission_id=submission.id, user_id=user.id))
            give_author_role(user.id, conference_id)
        log_action(g.user.id, f"submitted paper {submission.id} to conference {conference_id}")
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_pdf(pdf_name)             # no stored file without a paper
        raise
    return jsonify(submission_json(submission)), 201


# ---- see my papers ----

@submission_bp.get("/submissions")
@login_required
def my_submissions():
    """Only the papers the logged-in user is an author of."""
    submissions = (Submission.query.join(SubmissionAuthor, SubmissionAuthor.submission_id == Submission.id)
                   .filter(SubmissionAuthor.user_id == g.user.id).order_by(Submission.id).all())
    return jsonify([submission_json(s) for s in submissions])


@submission_bp.get("/submissions/<int:submission_id>")
@login_required
def get_submission(submission_id):
    submission, problem = get_my_submission(submission_id)
    return problem or jsonify(submission_json(submission))


@submission_bp.get("/submissions/<int:submission_id>/pdf")
@login_required
def download_own_pdf(submission_id):
    submission, problem = get_my_submission(submission_id)
    if problem:
        return problem
    path = upload_dir() / submission.pdf_path if submission.pdf_path else None
    if path is None or upload_dir() not in path.resolve().parents or not path.is_file():
        return error("No PDF available for this paper", 404)
    return send_file(path, mimetype="application/pdf", download_name=f"paper-{submission.id}.pdf")


# ---- edit or replace (FR-14) ----

@submission_bp.patch("/submissions/<int:submission_id>")
@login_required
def edit_submission(submission_id):
    """Change title, abstract, co-authors and/or the PDF. Fields that are not sent stay as they are."""
    submission, problem = get_my_submission(submission_id, corresponding_only=True)
    if problem:
        return problem
    if submission.status in FINAL_STATUSES:
        return error(f"This paper is {submission.status} and can no longer be changed", 409)
    conference = db.session.get(Conference, submission.conference_id)
    if (problem := check_deadline(conference, g.user.id, f"edit of paper {submission_id}")):
        return problem

    changes = {}
    if "title" in request.form:
        changes["title"] = clean_text(request.form["title"], 255, required=True)
        if changes["title"] is None:
            return error("A title is required (max 255 characters)", 400)
    if "abstract" in request.form:
        changes["abstract"] = clean_text(request.form["abstract"], MAX_ABSTRACT, required=True)
        if changes["abstract"] is None:
            return error(f"An abstract is required (max {MAX_ABSTRACT} characters)", 400)
    coauthors = None
    if "coauthors" in request.form:
        coauthors, problem = parse_coauthors()
        if problem:
            return problem
    new_pdf, problem = pdf_from_form(required=False)
    if problem:
        return problem

    old_pdf = submission.pdf_path
    try:
        for field, value in changes.items():
            setattr(submission, field, value)
        if new_pdf:
            submission.pdf_path = new_pdf
        if coauthors is not None:
            SubmissionAuthor.query.filter_by(submission_id=submission.id, is_corresponding=False).delete()
            for user in coauthors:
                db.session.add(SubmissionAuthor(submission_id=submission.id, user_id=user.id))
                give_author_role(user.id, submission.conference_id)
        log_action(g.user.id, f"edited paper {submission.id}" + (" (new PDF)" if new_pdf else ""))
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_pdf(new_pdf)
        raise
    if new_pdf:
        delete_pdf(old_pdf)              # only after the new name is safely in the database
    return jsonify(submission_json(submission))


# ---- withdraw (UR-10, FR-14) ----

@submission_bp.post("/submissions/<int:submission_id>/withdraw")
@login_required
def withdraw_submission(submission_id):
    submission, problem = get_my_submission(submission_id, corresponding_only=True)
    if problem:
        return problem
    if submission.status in FINAL_STATUSES:
        return error(f"This paper is already {submission.status}", 409)
    submission.status = "withdrawn"      # the record stays for the chair, reviewers lose access (reviews.py)
    log_action(g.user.id, f"withdrew paper {submission.id}")
    db.session.commit()
    return jsonify(submission_json(submission))
