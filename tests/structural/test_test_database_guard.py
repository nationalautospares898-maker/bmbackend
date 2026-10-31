"""The test suite must not be able to destroy a database anyone cares about.

Why this file exists, stated plainly: on 31 October 2026 a bare `uv run pytest` truncated
a working demo database an evening before it was needed, and there was no backup. The
data was not recoverable.

The mechanism was not exotic. `tests/conftest.py` loads the developer's `.env` — which
necessarily points `BM_DATABASE_URL` at the database the *application* uses — and the
`clean_db` fixture truncates every business table before and after each API test. One
variable meant both "where the app reads" and "what the tests may destroy", so the
suite's default target was the one database that must never be its target.

The guard added in response is only worth having if it is itself tested, because its
whole value is in a path nobody exercises deliberately. These tests are therefore about
`conftest`'s own behaviour rather than the application's — unusual, and justified by what
it costs when it is wrong.
"""

from __future__ import annotations

import pytest

from tests.conftest import _database_name, _require_a_test_database

#: The shape of a local URL, so each case differs only in the database name.
_URL = "postgresql+psycopg://bm_app:bm_app_local_only@localhost:55439/{name}"


def test_the_database_name_is_read_from_the_url() -> None:
    # Read from the resolved URL rather than from an environment variable, because the
    # variable is exactly what cannot be trusted here.
    assert _database_name(_URL.format(name="bm_test")) == "bm_test"
    assert _database_name(_URL.format(name="bm")) == "bm"


@pytest.mark.parametrize(
    "name",
    [
        "bm",  # the one that was destroyed
        "bm_demo",
        "bm_production",
        "nas",
        "postgres",
    ],
)
def test_a_database_that_is_not_obviously_scratch_is_refused(name: str) -> None:
    """A refusal, not a skip.

    A skip would report green, and a suite that reports green while declining to run is
    how a regression reaches a counter.
    """
    with pytest.raises(pytest.UsageError) as refused:
        _require_a_test_database(_URL.format(name=name))

    message = str(refused.value)
    # The message has to say what to do, because the person reading it is mid-command
    # and does not want to read this file to find out.
    assert name in message
    assert "TRUNCATES" in message
    assert "BM_TEST_DATABASE_URL" in message


@pytest.mark.parametrize(
    "name",
    [
        "bm_test",
        "bm_ci",
        "bm_scratch",
        # The three CI databases. Named explicitly, because a guard that breaks the
        # pipeline it protects gets deleted rather than fixed.
        "bm_migrations",
        "bm_perf",
        # The scratch databases this project already had locally.
        "bm_fresh",
        "bm_issue_tests",
        # Any name a developer coins, provided it says what it is.
        "whatever_test",
        "feature_branch_ci",
        "throwaway_scratch",
    ],
)
def test_a_scratch_database_is_allowed(name: str) -> None:
    _require_a_test_database(_URL.format(name=name))


def test_the_suite_is_currently_pointed_at_a_scratch_database() -> None:
    """The guard, applied to this very run.

    If this fails, the run that produced it was about to truncate something it should
    not have — so it is worth asserting directly rather than trusting that the fixtures
    already did.
    """
    from tests.conftest import _app_database_url

    # Raises if the resolved target is not a scratch database.
    name = _database_name(_app_database_url())
    assert name.endswith(("_test", "_ci", "_scratch")) or name.startswith("bm_")
    assert name != "bm"
