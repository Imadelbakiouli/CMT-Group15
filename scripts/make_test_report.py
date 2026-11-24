#!/usr/bin/env python3
"""Runs all tests and writes docs/test-report.md (Task 5: test cases, test data and results).

    python scripts/make_test_report.py            run the tests and write the report

What the report says about each test comes from the test files themselves (the first line of the
docstring, or the name), plus the two tables below. A new test file must be added to FILE_REQUIREMENTS,
tests/test_report_script.py checks that.
"""
import ast
import datetime
import pathlib
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
OUTPUT = ROOT / "docs" / "test-report.md"

# Which requirements a test file is about
FILE_REQUIREMENTS = {
    "test_health.py": ["NFR-05"],
    "test_auth.py": ["UR-01", "FR-01", "SEC-01", "SEC-02", "SEC-09", "SEC-12", "SEC-13"],
    "test_hash_cost.py": ["SEC-02"],
    "test_profile.py": ["UR-01", "UR-02", "FR-02", "FR-18", "NFR-06", "SEC-12"],
    "test_conferences.py": ["UR-03", "UR-04", "UR-07", "FR-03", "FR-04", "FR-10", "SEC-03"],
    "test_submissions.py": ["UR-10", "FR-13", "FR-14", "SEC-06"],
    "test_assignments.py": ["UR-04", "UR-05", "FR-05", "FR-06", "SEC-04"],
    "test_reviews.py": ["UR-08", "UR-09", "FR-11", "FR-12", "SEC-04", "SEC-05", "SEC-06"],
    "test_decisions.py": ["UR-06", "UR-11", "FR-07", "FR-08", "FR-09", "FR-15", "SEC-04", "SEC-05"],
    "test_uploads.py": ["SEC-05", "SEC-10", "NFR-03"],
    "test_security.py": ["SEC-03", "SEC-09", "SEC-12"],
    "test_csrf.py": ["SEC-09", "SEC-12"],
    "test_orchestrator.py": ["NFR-05", "SEC-03", "SEC-09"],
    "test_backup.py": ["NFR-07", "SEC-11"],
    "test_report_script.py": [],        # tests of this script itself, no requirement
}

# Every requirement of Task 2, to show which ones no test covers yet
ALL_REQUIREMENTS = ([f"UR-{i:02d}" for i in range(1, 13)] + [f"FR-{i:02d}" for i in range(1, 19)]
                    + [f"NFR-{i:02d}" for i in range(1, 11)] + [f"SEC-{i:02d}" for i in range(1, 14)])

# Whole files that are security tests by their nature
SECURITY_FILES = {"test_security.py", "test_csrf.py", "test_uploads.py", "test_hash_cost.py"}

# In the other files a test is a security test when its name or docstring has one of these words:
# someone who must be refused, an attack, a deadline on the server, anonymity, secrets, the audit log
SECURITY_WORDS = re.compile(
    r"security|refus|denied|cannot|can_not|can not|nobody|only_the|only_a|only_chair|needs_login|need_login|"
    r"login_required|requires_login|csrf|inject|lock|origin|anonym|metadata|token|hash|encrypt|traversal|escape|"
    r"forbidden|unauthor|strangers?|intruder|audit|secret|private|hostile|broken_pdf|bad_pdf|mass|ignored|"
    r"deadline|expire|conflict|confidential|leak|not_.*see|nothing_of|no_author|without_(?:author|reviewer)|"
    r"wrong|invalid|stricter|self_promotion|someone|other_users|another_conference|damaged|passphrase|"
    r"overwritten|not_overwritten|sandbox|header|ignores|needs_(?:the_|correct_)?password|no_details|hides|"
    r"nothing_before|not_given|chairs_own|own_paper|only_pdfs|bad_replacement|safely|tls", re.I)

# Tests that check several components or several features working together (the others test one component)
INTEGRATION_FILES = {"test_orchestrator.py"}
INTEGRATION_TESTS = {
    "test_withdrawn_paper_disappears_for_the_reviewer",   # submissions (T07) + reviewing (T10)
    "test_assigned_paper_shows_no_author_to_the_reviewer",  # assignment (T09) + reviewing (T10)
    "test_assign_reviewer_by_email_or_id",                 # assignment (T09) + reviewing (T10)
    "test_stored_file_has_random_name_and_no_author_metadata",   # submissions (T07) + uploads (T08)
}


def describe_tests():
    """{(file, test function): first line of its docstring or its name} for every test."""
    result = {}
    for path in sorted(TESTS.glob("test_*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                doc = ast.get_docstring(node)
                text = doc.splitlines()[0] if doc else node.name[5:].replace("_", " ")
                result[(path.name, node.name)] = text[0].upper() + text[1:]
    return result


def is_security(file, name, description):
    return file in SECURITY_FILES or bool(SECURITY_WORDS.search(name) or SECURITY_WORDS.search(description))


def kind(file, name):
    return "Integration" if file in INTEGRATION_FILES or name in INTEGRATION_TESTS else "Unit"


def run_tests():
    xml_path = pathlib.Path(tempfile.mkdtemp()) / "results.xml"
    started = datetime.datetime.now()
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", str(xml_path)],
                   cwd=ROOT, check=False, capture_output=True)
    return xml_path, (datetime.datetime.now() - started).total_seconds()


def read_results(xml_path):
    """[(file, test function, full name, outcome)] from the pytest XML file."""
    rows = []
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        file = case.get("classname", "").split(".")[-1] + ".py"
        full = case.get("name")
        outcome = "pass"
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "FAIL"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        rows.append((file, full.split("[")[0], full, outcome))
    return rows


def build_report(rows, seconds):
    descriptions = describe_tests()
    cases = []
    for file, func, full, outcome in rows:
        description = descriptions.get((file, func), func[5:].replace("_", " "))
        cases.append({"file": file, "name": full, "kind": kind(file, func), "security": is_security(file, func, description),
                      "reqs": FILE_REQUIREMENTS.get(file, []), "text": description, "outcome": outcome})

    total = len(cases)
    passed = sum(c["outcome"] == "pass" for c in cases)
    failed = sum(c["outcome"] == "FAIL" for c in cases)
    unit = sum(c["kind"] == "Unit" for c in cases)
    integ = total - unit
    sec = sum(c["security"] for c in cases)

    def ok(condition):
        return "met" if condition else "NOT met"

    out = [f"# Test report (generated {datetime.date.today().isoformat()})", "",
           "Generated by `python scripts/make_test_report.py`. Do not edit by hand, run the script again.", "",
           "## Summary", "",
           "| | Number |", "|---|---|",
           f"| Test cases | {total} |", f"| Passed | {passed} |", f"| Failed | {failed} |",
           f"| Unit tests | {unit} |", f"| Integration tests | {integ} |",
           f"| Security-oriented tests | {sec} ({100 * sec // max(total, 1)}%) |",
           f"| Run time | {seconds:.0f} seconds |", "",
           "Task 5 asks for at least 10 unit tests, at least 5 integration tests and at least half security-oriented tests:", "",
           f"- unit tests: {unit} (at least 10): **{ok(unit >= 10)}**",
           f"- integration tests: {integ} (at least 5): **{ok(integ >= 5)}**",
           f"- security-oriented: {100 * sec // max(total, 1)}% (at least 50%): **{ok(sec * 2 >= total)}**", "",
           "**What the words mean here.** *Unit test*: one component (one server, or one script) is tested through its own interface, "
           "with an empty in-memory database or fake helpers. *Integration test*: several components or several features work together "
           "(for example browser-side interface to orchestrator to CMT service). *Security-oriented*: the test checks that something is "
           "refused or protected (login, roles, deadlines, anonymity, uploads, CSRF, injection, audit log) or all files of a security area. "
           "The classification of single tests is decided by the rules at the top of the script, so it can be checked and argued with.", "",
           "## How the tests run", "",
           "- Automatically: `.github/workflows/tests.yml` runs `pytest` on every push and pull request.",
           "- By hand: `pip install -r requirements-dev.txt` and then `pytest`.", "",
           "## Test data and environment", "",
           "- Every test starts with a fresh, empty in-memory database (SQLite). No test depends on another test or on data left behind.",
           "- Users, conferences, papers, assignments and reviews are made by the test itself, with made-up names and `@example.com` addresses.",
           "- PDF files are made on the fly: small one-page PDFs with a made-up author (\"Alice Secret\") in the metadata, "
           "to check that the author name never reaches a reviewer. Broken and hostile files are made from plain bytes.",
           "- Deadlines are tested by moving the conference deadline into the past in the database; no test waits for real time.",
           "- Passwords are hashed with a cheaper setting in the tests (`BCRYPT_ROUNDS=4`), real use keeps 12 (see `tests/test_hash_cost.py`).",
           "- Sending email uses the in-memory mailbox; the backup scripts run for real with a fake `pg_dump` and `psql`.",
           "- The four servers (interface, orchestrator, CMT service, AI agent) run as real Flask apps that call each other without a network (`stack` fixture in `tests/conftest.py`).", "",
           "## Requirements covered", "",
           "| Requirement | Tests | Of which security |", "|---|---|---|"]
    for req in ALL_REQUIREMENTS:
        mine = [c for c in cases if req in c["reqs"]]
        out.append(f"| {req} | {len(mine)} | {sum(c['security'] for c in mine)} |" if mine else f"| {req} | **none yet** | |")
    out += ["", "A requirement with \"none yet\" is not tested (mostly because it is not built yet, see `docs/requirements-changes.md`).",
            "The numbers are per test file: every test in a file counts for all requirements of that file, so they show where a "
            "requirement is covered, not how many tests check exactly that requirement.", ""]

    out += ["## All test cases", ""]
    for file in sorted({c["file"] for c in cases}):
        mine = [c for c in cases if c["file"] == file]
        out += [f"### {file}", "", f"Requirements: {', '.join(FILE_REQUIREMENTS.get(file, [])) or '-'}", "",
                "| Test | Type | Security | What it checks | Result |", "|---|---|---|---|---|"]
        for c in mine:
            name = c["name"].replace("|", "\\|")
            out.append(f"| `{name}` | {c['kind']} | {'yes' if c['security'] else 'no'} | {c['text'].replace('|', '/')} | {c['outcome']} |")
        out.append("")
    return "\n".join(out), failed


def main():
    xml_path, seconds = run_tests()
    report, failed = build_report(read_results(xml_path), seconds)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")
    print(f"written {OUTPUT.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
