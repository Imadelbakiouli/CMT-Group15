"""Tests for scripts/make_test_report.py, so that the test report cannot silently go wrong."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_test_report", ROOT / "scripts" / "make_test_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_every_test_file_is_in_the_requirements_table():
    """A new test file must be added to FILE_REQUIREMENTS, otherwise it shows up in the report without requirements."""
    files = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    assert files == set(report.FILE_REQUIREMENTS)


def test_requirement_ids_in_the_table_exist():
    for file, ids in report.FILE_REQUIREMENTS.items():
        assert set(ids) <= set(report.ALL_REQUIREMENTS), file


def test_every_test_function_gets_a_description():
    described = report.describe_tests()
    assert len(described) >= 200
    assert all(text for text in described.values())


def test_classification_examples():
    assert report.is_security("test_csrf.py", "test_anything", "")                       # a whole security file
    assert report.is_security("test_submissions.py", "test_submitting_after_the_deadline_is_refused", "")
    assert report.is_security("test_profile.py", "test_change_email_needs_correct_password", "")
    assert not report.is_security("test_conferences.py", "test_chair_invites_registered_user", "Chair invites")
    assert report.kind("test_orchestrator.py", "test_full_chain") == "Integration"
    assert report.kind("test_auth.py", "test_login_and_me") == "Unit"


def test_report_for_a_small_example_has_the_task_5_check():
    rows = [("test_auth.py", "test_login_and_me", "test_login_and_me", "pass")] * 10 \
        + [("test_orchestrator.py", "test_full_chain", "test_full_chain", "pass")] * 5 \
        + [("test_csrf.py", "test_x", "test_x", "FAIL")]
    text, failed = report.build_report(rows, 1.0)
    assert failed == 1
    assert "unit tests: 11 (at least 10): **met**" in text      # the failing csrf test is a unit test too
    assert "integration tests: 5 (at least 5): **met**" in text
    assert "**none yet**" in text                  # requirements without tests are shown
