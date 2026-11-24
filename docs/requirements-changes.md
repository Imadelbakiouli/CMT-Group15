# Requirements: what changed while building (status 24 Nov 2026)

Task 3 asks us to update the requirements when needed and to keep the original so the differences can be traced.
The original document (Task 2) is kept unchanged in `docs/requirements/Task2_group_15_original.docx`.
This file lists, per requirement, what we built, where we chose something the requirement did not say, and what is not done yet.
Keep it up to date: when you change behaviour, change the line here in the same pull request.

Status: **Done** (as written) · **Detail** (done, we had to choose something the requirement left open) · **Changed** (differs from the requirement) · **Added** (not in the requirements) · **Partly** · **Not yet**

## User requirements

| ID | Status | What we built | PR |
|---|---|---|---|
| UR-01 | Detail | Register, log in, log out, edit profile. Password at least 10 characters (bcrypt reads at most 72 bytes). Email is stored in lower case and must be unique | #3, #8 |
| UR-02 | Changed | View and export personal data as JSON. "Request deletion" is carried out at once as anonymisation, because there is no admin who could approve a request (see FR-18) | #8 |
| UR-03 | Detail | Create a conference and change its details and deadlines. Any logged-in user may create one and becomes its chair. The three deadlines must be in order: submission, review, notification. Dates in the past are allowed | #4 |
| UR-04 | Changed | Chairs invite reviewers (only people who already have an account, found by email) and assign them to papers. No separate "add without invitation" | #4, #11 |
| UR-05 | Done | Chair sees every paper with assigned/completed reviews and who is missing, and which papers are overdue | #11 |
| UR-06 | Detail | Chair records accept/reject and releases it. Releasing is a separate step (see FR-08) | #12 |
| UR-07 | Done | Reviewers accept or decline an invitation, once | #4 |
| UR-08 | Done | Reviewer sees title and abstract, never author names; PDF is downloaded without metadata | #5 |
| UR-09 | Done | Overall score and confidence 1-5 and comments, editable until the review deadline | #5 |
| UR-10 | Detail | Submit, edit/replace and withdraw. Only the corresponding author (the person who submitted) can edit or withdraw; co-authors can read | #10 |
| UR-11 | Done | Authors see status, and decision plus reviews after release | #12 |
| UR-12 | Not yet | AI assistant (T13) | - |

## Functional requirements

| ID | Status | What we built | PR |
|---|---|---|---|
| FR-01 | Detail | Name, email, affiliation, password. Same rules as UR-01 | #3 |
| FR-02 | Added | Edit name, affiliation and email. Changing the email needs the current password. **Added:** change password (ends all other logins) | #8 |
| FR-03 | Detail | See UR-03. Description at most 5000 characters | #4 |
| FR-04 | Changed | Invite only (see UR-04). A declined invitation can be sent again | #4 |
| FR-05 | Added | Manual assignment. Authors and co-authors can never review their own paper. **Added:** a reviewer with the same affiliation as an author gives a warning; an assignment can be removed until the review is written | #11 |
| FR-06 | Done | Progress overview with missing reviews and overdue flag | #11 |
| FR-07 | Done | Reviews with average overall score and average confidence per paper | #12 |
| FR-08 | Added | Record, change until released. **Added:** a "release" step, and releasing all recorded decisions at once. Before release the paper keeps its old status and authors see nothing | #12 |
| FR-09 | Changed | Every author gets an email with the reviews as text attachments, without reviewer names. There is no mail server: by default mails are kept in memory (`MAIL_BACKEND=console`), a real SMTP server can be configured. A failed mail does not undo the release; the answer lists the failed addresses | #12 |
| FR-10 | Done | See UR-07 | #4 |
| FR-11 | Done | See UR-08 | #5 |
| FR-12 | Done | See UR-09 | #5 |
| FR-13 | Changed | Title (at most 255), abstract (at most 5000), PDF, up to 20 co-authors. **Co-authors must already have an account**, because the database links co-authors to users | #10 |
| FR-14 | Detail | Edit/replace before the submission deadline. Withdrawing is possible at any time until a decision exists (the requirement names no deadline). A replaced PDF is deleted from disk | #10 |
| FR-15 | Done | `GET /submissions/<id>/result`: status always, decision and reviews only after release | #12 |
| FR-16 | Not yet | AI assistant (T13) | - |
| FR-17 | Not yet | Confirmation before important actions (T13) | - |
| FR-18 | Changed | Export as JSON (own profile, roles, invitations, own papers, own reviews, own activity; nothing about others). Deletion = anonymise name, email and affiliation, end all logins, remove pending invitations. Papers and reviews stay in the conference but are no longer linked to a person. Not handled: a chair who deletes their account leaves a conference without chair | #8 |

## Non-functional requirements

| ID | Status | What we built | PR |
|---|---|---|---|
| NFR-01 | Not yet | Load test planned (T18) | - |
| NFR-02 | Not yet | Needs the AI assistant | - |
| NFR-03 | Detail | PDF at most 20 MB. **Added:** at most 100 pages, and a request limit of 21 MB. Capacity of 1000 submissions / 300 reviewers not measured yet | #6, #7 |
| NFR-04 | Not yet | There is no web interface yet, only the API | - |
| NFR-05 | Done | The orchestrator forwards requests to the CMT service and AI agent; the four components run as separate containers | #7 |
| NFR-06 | Partly | Access, export and deletion exist (FR-18). Retention periods and a privacy statement are not written | #8 |
| NFR-07 | Partly | Daily encrypted backup of database and PDFs (kept 14 days) with verify/restore script. **Not tried in Docker yet** | #13 |
| NFR-08 | Not yet | Needs the AI assistant | - |
| NFR-09 | Not yet | User test (T19) | - |
| NFR-10 | Not yet | Needs the AI assistant | - |

## Security requirements

| ID | Status | What we built | PR |
|---|---|---|---|
| SEC-01 | Detail | Login needed for everything except register/login/health. The login is a random token in the `Authorization` header (not a cookie); only a hash of it is stored. 30 minutes of inactivity ends the session | #3 |
| SEC-02 | Done | bcrypt with salt, cost 12 (`BCRYPT_ROUNDS`, the tests use 4) | #3 |
| SEC-03 | Detail | Roles belong to one conference (chair of A is nothing in B). Checked in the backend on every route, deny by default. Someone without access to another person's data gets "404 not found" (so ids are not revealed); a wrong role in a conference gets 403. Denied tries are logged | #4, #6 |
| SEC-04 | Done | An author can never be assigned to or review their own paper. A chair who wrote a paper cannot assign reviewers to it, read its reviews, record or release its decision, and sees no details of it in overviews. If the only chair wrote a paper nobody can decide on it | #5, #11, #12 |
| SEC-05 | Detail | Reviewers never see authors. PDF metadata and comments/annotations are removed at upload and again at download. Authors see reviews only after release, without any reviewer information. **Not possible:** names written inside the text of the paper itself | #5, #6, #12 |
| SEC-06 | Done | Deadlines for submitting, editing and reviewing are checked on the server; refused tries are logged and store nothing | #5, #10 |
| SEC-07 | Not yet | AI assistant (T13). The orchestrator already passes the user's own token on, so the agent can only do what that user can do | #7 |
| SEC-08 | Not yet | Prompt-injection defence (T14) | - |
| SEC-09 | Detail | Safe database queries (SQLAlchemy), strict checks on input, only JSON or file uploads are accepted, security headers, errors without details. Output escaping: text is stored as typed and sent as JSON, whoever shows it on a web page must insert it as text, never as HTML | #3, #6 |
| SEC-10 | Done | Content must start with `%PDF-` (name and Content-Type are not trusted), at most 20 MB, not encrypted, 1-100 pages, rewritten as a new PDF without annotations, page actions and metadata, stored under a random name, readable only by the service | #6 |
| SEC-11 | Partly | Backups are encrypted (AES-256). Optional HTTPS proxy (TLS 1.2+, HSTS). **Not done:** traffic between the containers is plain http; PDFs and the database are not encrypted on disk. **Not tried in Docker yet** | #13 |
| SEC-12 | Detail | 5 wrong passwords in a row lock the account for 15 minutes (also when guessed through profile routes). CSRF: signed double-submit token plus a check of the Origin header on the interface, SameSite=Strict cookie. Known limit: the CSRF token is not tied to one user | #3, #6, #9 |
| SEC-13 | Partly | Logged: register, login, failed logins, locks, profile changes, exports, deletion, conference changes, invitations, assignments, submissions, edits, withdrawals, review downloads and changes, decisions, releases, failed mails and every denied action. **Not yet:** a way to read the log in the system | all |

## Choices to confirm with the whole team
1. Anyone may create a conference (UR-03).
2. Co-authors and invited reviewers need an account first (FR-04, FR-13).
3. Deleting an account anonymises it at once (FR-18).
4. Releasing a decision is possible at any time, not only after the review or notification deadline (FR-08).
5. Same affiliation as an author is only a warning (FR-05).
6. If the only chair is an author, nobody can decide on that paper (SEC-04).
