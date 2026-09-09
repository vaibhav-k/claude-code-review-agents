"""Shared fixtures for the cli/tests suite."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_review import cli


@pytest.fixture(autouse=True)
def _no_real_dotenv_lookup(monkeypatch):
    """`cli.main()` / `cli.main_init()` call `_load_dotenv_if_present()`,
    which searches upward from the real process working directory for a
    `.env` file. Left alone, running this suite from `cli/` on a machine
    that has followed cli/README.md and created a real `cli/.env` (with
    real Foundry credentials, per the setup this project's own docs walk
    a user through) would load those values into every test process --
    making test behavior depend on whatever happens to be on that
    developer's disk instead of what each test sets up explicitly.

    Neutralize the lookup by default for every test in this suite.
    test_cli_dotenv.py imports the real, unpatched function directly (by
    reference, before this fixture ever runs) to test it on its own
    terms without being affected by this patch.
    """
    monkeypatch.setattr(cli, "_load_dotenv_if_present", lambda: None)
