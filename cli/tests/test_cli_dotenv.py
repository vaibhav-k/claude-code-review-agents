"""Tests for cli._load_dotenv_if_present() specifically.

Imports the real function by reference at module load time, before
conftest.py's autouse `_no_real_dotenv_lookup` fixture patches
`agent_review.cli._load_dotenv_if_present` to a no-op for every other
test in this suite -- that patch replaces the module attribute, it
doesn't touch a name already bound to the original function object, so
these tests exercise the real implementation regardless.
"""

import builtins
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.cli import _load_dotenv_if_present

_ENV_VARS = (
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_USE_ENTRA_ID",
)


def _clear_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_loads_dotenv_from_the_current_directory(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    (tmp_path / ".env").write_text("ANTHROPIC_FOUNDRY_RESOURCE=from-dotenv\n")
    monkeypatch.chdir(tmp_path)

    _load_dotenv_if_present()

    assert os.environ["ANTHROPIC_FOUNDRY_RESOURCE"] == "from-dotenv"


def test_finds_dotenv_in_a_parent_directory(tmp_path, monkeypatch):
    # find_dotenv(usecwd=True) searches upward, matching most .env-aware
    # tools -- confirms a .env need not live in the exact cwd.
    _clear_env(monkeypatch)
    (tmp_path / ".env").write_text("ANTHROPIC_FOUNDRY_RESOURCE=from-parent\n")
    nested = tmp_path / "some" / "nested" / "cwd"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    _load_dotenv_if_present()

    assert os.environ["ANTHROPIC_FOUNDRY_RESOURCE"] == "from-parent"


def test_a_real_environment_variable_always_wins_over_dotenv(tmp_path, monkeypatch):
    # An explicit `$env:FOO=...` / `export FOO=...` must never be
    # silently overridden by a stale value sitting in a .env file.
    _clear_env(monkeypatch)
    (tmp_path / ".env").write_text("ANTHROPIC_FOUNDRY_RESOURCE=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "from-real-shell")

    _load_dotenv_if_present()

    assert os.environ["ANTHROPIC_FOUNDRY_RESOURCE"] == "from-real-shell"


def test_no_dotenv_file_anywhere_is_a_silent_no_op(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    _load_dotenv_if_present()  # must not raise

    assert "ANTHROPIC_FOUNDRY_RESOURCE" not in os.environ


def test_missing_python_dotenv_package_is_a_silent_no_op(monkeypatch):
    # python-dotenv is a normal dependency, but this function is written
    # to degrade gracefully (not crash every command) if it's somehow
    # absent -- simulate that by making the import fail.
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dotenv":
            raise ImportError("simulated: python-dotenv not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    _load_dotenv_if_present()  # must not raise
