"""Reviewing (T10): assigned papers, anonymous PDF download, write and edit a review.

Related requirements: UR-08, UR-09, FR-11, FR-12, SEC-05, SEC-06.

Who can touch an assignment: only the reviewer it belongs to, who must still have the
reviewer role in that conference and must not be an author of the paper.
Assignments are made in T09, the PDFs are stored by T08 (folder from UPLOAD_DIR).
"""
import io
import os
import pathlib

from flask import Blueprint, g, jsonify, request, send_file
from pypdf import PdfReader, PdfWriter

from audit import log_action
from auth import clean_text, error, login_required
from conferences import as_utc
from models import Assignment, Conference, ConferenceRole, Review, Submission, SubmissionAuthor, db, now

review_bp = Blueprint("reviews", __name__)

MAX_COMMENTS = 10000


def get_my_assignment(assignment_id, audit=True):
    """Find an assignment the logged-in user is allowed to work on.

    Returns (assignment, submission, conference), or (None, error_response, None).
    Everything that is not allowed gives the same 404, so ids of other people's
    assignments are not revealed.
    """
    not_found = (None, error("Assignment not found", 404), None)
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None or assignment.reviewer_id != g.user.id:
        return not_found
    submission = db.session.get(Submission, assignment.submission_id)
    if submission is None or submission.status == "withdrawn":
        return not_found
    is_reviewer = ConferenceRole.query.filter_by(
        conference_id=submission.conference_id, user_id=g.user.id, role="reviewer").first()
    is_author = SubmissionAuthor.query.filter_by(submission_id=submission.id, user_id=g.user.id).first()
    if not is_reviewer or is_author:   # SEC-04: never review your own paper
        if audit:
            log_action(g.user.id, f"denied: review access to assignment {assignment_id}")
            db.session.commit()
        return not_found
    return assignment, submission, db.session.get(Conference, submission.conference_id)


def paper_json(assignment, submission, conference):
    """What a reviewer may see about a paper. No author names, ever (SEC-05)."""
    return {
        "assignment_id": assignment.id,
        "submission_id": submission.id,
        "conference_id": conference.id,
        "title": submission.title,
        "abstract": submission.abstract,
        "review_deadline": as_utc(conference.review_deadline).isoformat(),
        "has_review": Review.query.filter_by(assignment_id=assignment.id).first() is not None,
    }


def review_json(review):
    return {"assignment_id": review.assignment_id, "overall_score": review.overall_score,
            "confidence": review.confidence, "comments": review.comments,
            "updated_at": as_utc(review.updated_at).isoformat()}


# ---- assigned papers (UR-08, FR-11) ----

@review_bp.get("/assignments")
@login_required
def my_assignments():
    result = []
    for a in Assignment.query.filter_by(reviewer_id=g.user.id).order_by(Assignment.id):
        assignment, submission, conference = get_my_assignment(a.id, audit=False)
        if assignment is not None:   # skips withdrawn papers and conflicts
            result.append(paper_json(assignment, submission, conference))
    return jsonify(result)


@review_bp.get("/assignments/<int:assignment_id>")
@login_required
def get_assignment(assignment_id):
    assignment, submission, conference = get_my_assignment(assignment_id)
    if assignment is None:
        return submission    # this is the error response
    return jsonify(paper_json(assignment, submission, conference))


def anonymize_pdf(raw):
    """Copy only the pages into a new PDF, so the metadata is left behind (SEC-05).

    The document info (author, title, ...) and the XMP data are not copied.
    Comments and annotations are removed as well, they can contain a name.
    """
    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        if "/Annots" in page:
            del page["/Annots"]
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def stored_pdf_path(submission):
    """Full path of the PDF, only if it really is inside the upload folder."""
    if not submission.pdf_path:
        return None
    base = pathlib.Path(os.environ.get("UPLOAD_DIR", "uploads")).resolve()
    path = (base / submission.pdf_path).resolve()
    if base not in path.parents or not path.is_file():   # also stops ../ tricks
        return None
    return path


@review_bp.get("/assignments/<int:assignment_id>/pdf")
@login_required
def download_pdf(assignment_id):
    assignment, submission, conference = get_my_assignment(assignment_id)
    if assignment is None:
        return submission
    path = stored_pdf_path(submission)
    if path is None:
        return error("No PDF available for this paper", 404)
    try:
        pdf = anonymize_pdf(path.read_bytes())
    except Exception:    # broken or unreadable PDF: better nothing than the original with metadata
        return error("This PDF could not be processed", 422)
    log_action(g.user.id, f"downloaded anonymized PDF of submission {submission.id}")
    db.session.commit()
    return send_file(pdf, mimetype="application/pdf", download_name=f"paper-{submission.id}.pdf")


# ---- the review itself (UR-09, FR-12, SEC-06) ----

def valid_score(value):
    """A whole number from 1 to 5 (True/False are numbers in Python, so we exclude those)."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


@review_bp.get("/assignments/<int:assignment_id>/review")
@login_required
def get_review(assignment_id):
    assignment, submission, conference = get_my_assignment(assignment_id)
    if assignment is None:
        return submission
    review = Review.query.filter_by(assignment_id=assignment.id).first()
    if review is None:
        return error("No review yet", 404)
    return jsonify(review_json(review))


@review_bp.put("/assignments/<int:assignment_id>/review")
@login_required
def save_review(assignment_id):
    """Create the review, or replace it if it already exists. Only before the review deadline."""
    assignment, submission, conference = get_my_assignment(assignment_id)
    if assignment is None:
        return submission

    if now() > as_utc(conference.review_deadline):   # SEC-06: checked on the server
        log_action(g.user.id, f"denied: review after deadline on assignment {assignment_id}")
        db.session.commit()
        return error("The review deadline has passed", 403)

    data = request.get_json(silent=True) or {}
    comments = clean_text(data.get("comments"), MAX_COMMENTS, required=True)
    if not valid_score(data.get("overall_score")) or not valid_score(data.get("confidence")):
        return error("overall_score and confidence must be whole numbers from 1 to 5", 400)
    if comments is None:
        return error(f"Comments are required (max {MAX_COMMENTS} characters)", 400)

    review = Review.query.filter_by(assignment_id=assignment.id).first()
    created = review is None
    if created:
        review = Review(assignment_id=assignment.id, overall_score=data["overall_score"],
                        confidence=data["confidence"], comments=comments)
        db.session.add(review)
    else:
        review.overall_score, review.confidence, review.comments = data["overall_score"], data["confidence"], comments
    log_action(g.user.id, f"{'submitted' if created else 'edited'} review for submission {submission.id}")
    db.session.commit()
    return jsonify(review_json(review)), 201 if created else 200
