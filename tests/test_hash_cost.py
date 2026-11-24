"""Tests for the password hashing cost: real use must stay at 12 rounds, only the tests use fewer (SEC-02)."""


def test_default_cost_is_12(cmt, monkeypatch):
    import auth
    monkeypatch.delenv("BCRYPT_ROUNDS", raising=False)
    assert auth.bcrypt_rounds() == 12
    assert auth.hash_password("a-long-enough-password").startswith("$2b$12$")


def test_cost_can_be_set_but_stays_within_limits(cmt, monkeypatch):
    import auth
    for value, expected in [("4", 4), ("10", 10), ("1", 4), ("99", 15), ("abc", 12), ("", 12)]:
        monkeypatch.setenv("BCRYPT_ROUNDS", value)
        assert auth.bcrypt_rounds() == expected, value


def test_tests_run_with_the_cheap_setting(cmt):
    import auth
    assert auth.hash_password("a-long-enough-password").startswith("$2b$04$")
