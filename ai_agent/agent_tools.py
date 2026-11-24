"""The things the AI assistant is allowed to do (T13, SEC-07).

Every tool is one call to the CMT service API, made with the token of the logged-in user. So the assistant can
never do more than that user can do: the backend checks roles, deadlines and conflicts exactly like for a normal
request (SEC-03, SEC-04, SEC-06). The assistant has no login of its own and no extra rights.

Tools that change something important are marked `risky`: the assistant asks the user for confirmation first (FR-17).
The `description` and `params` are written so a language model can be given this list as its set of tools later.
"""
import re
from dataclasses import dataclass

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class ToolError(Exception):
    """The arguments are not acceptable."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    method: str
    path: str                  # the URL path, with {name} where an argument goes
    params: tuple = ()         # (name, kind) with kind "int", "email" or "outcome"
    body: tuple = ()           # names of the params that go in the JSON body instead of the path
    risky: bool = False        # important action: ask for confirmation first (FR-17)
    confirm_text: str = ""     # what we ask the user to confirm; {name} is filled in


TOOLS = {t.name: t for t in [
    Tool("list_conferences", "List all conferences with their deadlines and my roles.", "GET", "/conferences"),
    Tool("show_conference", "Show one conference.", "GET", "/conferences/{conference_id}", (("conference_id", "int"),)),
    Tool("list_my_submissions", "List the papers I wrote, with their status.", "GET", "/submissions"),
    Tool("show_my_result", "Show decision and reviews of my paper (only after the chair released them).", "GET",
         "/submissions/{submission_id}/result", (("submission_id", "int"),)),
    Tool("list_my_assignments", "List the papers I have to review.", "GET", "/assignments"),
    Tool("list_my_invitations", "List my invitations to join a programme committee.", "GET", "/invitations"),
    Tool("accept_invitation", "Accept an invitation to review.", "POST", "/invitations/{invitation_id}/accept",
         (("invitation_id", "int"),)),
    Tool("decline_invitation", "Decline an invitation to review.", "POST", "/invitations/{invitation_id}/decline",
         (("invitation_id", "int"),)),
    Tool("list_conference_submissions", "Chair: list all papers of a conference.", "GET",
         "/conferences/{conference_id}/submissions", (("conference_id", "int"),)),
    Tool("review_progress", "Chair: which reviews are done and which are missing.", "GET",
         "/conferences/{conference_id}/review-progress", (("conference_id", "int"),)),
    Tool("list_committee", "Chair: the reviewers of a conference and their workload.", "GET",
         "/conferences/{conference_id}/committee", (("conference_id", "int"),)),
    Tool("conference_results", "Chair: average scores and decision state of every paper.", "GET",
         "/conferences/{conference_id}/results", (("conference_id", "int"),)),
    Tool("assign_reviewer", "Chair: assign a reviewer (by email) to a paper.", "POST",
         "/conferences/{conference_id}/submissions/{submission_id}/assignments",
         (("conference_id", "int"), ("submission_id", "int"), ("reviewer_email", "email")), body=("reviewer_email",)),
    Tool("record_decision", "Chair: record accept or reject for a paper.", "PUT",
         "/conferences/{conference_id}/submissions/{submission_id}/decision",
         (("conference_id", "int"), ("submission_id", "int"), ("outcome", "outcome")), body=("outcome",), risky=True,
         confirm_text="Record the decision '{outcome}' for paper {submission_id} in conference {conference_id}"),
    Tool("release_decision", "Chair: release the decision to the authors and email them the reviews.", "POST",
         "/conferences/{conference_id}/submissions/{submission_id}/decision/release",
         (("conference_id", "int"), ("submission_id", "int")), risky=True,
         confirm_text="Release the decision for paper {submission_id} in conference {conference_id} and email its authors"),
    Tool("withdraw_submission", "Author: withdraw my paper.", "POST", "/submissions/{submission_id}/withdraw",
         (("submission_id", "int"),), risky=True, confirm_text="Withdraw your paper {submission_id}"),
]}


def clean_value(kind, value):
    if kind == "int":
        if isinstance(value, bool):
            raise ToolError("a number is needed")
        if isinstance(value, str) and value.isascii() and value.isdigit():
            value = int(value)
        if not isinstance(value, int) or not 0 < value < 10**9:
            raise ToolError("a whole number above zero is needed")
        return value
    if kind == "email":
        if not isinstance(value, str) or len(value) > 255 or not EMAIL.fullmatch(value.strip()):
            raise ToolError("a valid email address is needed")
        return value.strip().lower()
    if kind == "outcome":
        if value not in ("accept", "reject"):
            raise ToolError('the outcome must be "accept" or "reject"')
        return value
    raise ToolError("unknown kind of value")


def validate(tool, args):
    """Check the arguments for a tool. Returns the cleaned arguments. Unknown or missing arguments are refused."""
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    wanted = dict(tool.params)
    if set(args) != set(wanted):
        raise ToolError(f"{tool.name} needs exactly: {', '.join(wanted) or 'nothing'}")
    return {name: clean_value(kind, args[name]) for name, kind in wanted.items()}


def build_request(tool, args):
    """(method, path, json body or None). Only validated numbers go in the path, so nothing can change where it goes."""
    args = validate(tool, args)
    path = tool.path.format(**{k: v for k, v in args.items() if k not in tool.body})
    body = {k: args[k] for k in tool.body} or None
    return tool.method, path, body


def confirm_text(tool, args):
    return tool.confirm_text.format(**args)
