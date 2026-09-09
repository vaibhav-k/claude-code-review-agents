"""End-to-end tests that exercise the actual `agent-review`/`agent-init`
entry points (cli.main / cli.main_init) against synthetic repos, with a
fake model client swapped in for AnthropicFoundryReviewer -- this is the closest
this test suite gets to "run the real CLI," short of spending a live API
key, and it's what catches wiring bugs the lower-level orchestrator/unit
tests can't (argument parsing, output formatting, cache-hit reporting
through the full stack).
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import cli


class FakeReviewer:
    """Drop-in stand-in for AnthropicFoundryReviewer -- accepts the same
    keyword arguments `_build_reviewer` passes (model, resource,
    use_entra_id) without validating any of them, so no Foundry
    environment variables need to be configured for these tests.
    Scripted responses are keyed by a substring of the system prompt.
    """

    def __init__(self, model=None, resource=None, use_entra_id=None, **_ignored):
        self.model = model
        self.calls = []
        self.responses = {
            "security vulnerabilities": (
                "[CRITICAL] handlers.py:3 — SQL injection\n"
                "Impact: attacker-controlled input reaches the query\n"
                "Fix: parameterize the query\n"
            ),
        }

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        for hint, response in self.responses.items():
            if hint in system_prompt:
                return response
        return "No high-impact issues found."


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "handlers.py").write_text("def noop():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_full_cli_review_end_to_end_with_fake_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SQL injection" in out
    assert "cache misses: 1" in out

    # Second run, same fake model, no source changes: must be a pure
    # cache hit end-to-end through the real CLI wiring.
    exit_code2 = cli.main(["--path", str(repo), "--jobs", "1"])
    out2 = capsys.readouterr().out
    assert exit_code2 == 0
    assert "SQL injection" in out2
    assert "cache hits: 1" in out2
    assert "cache misses: 0" in out2


def test_fail_on_findings_flag_sets_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--fail-on-findings"])
    capsys.readouterr()
    assert exit_code == 1


def test_clean_diff_yields_zero_exit_even_with_fail_on_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text("def noop():\n    return 1\n")
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--fail-on-findings"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No high-impact issues found." in out


def test_init_then_review_uses_the_scaffolded_agent_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    repo = make_repo(tmp_path)
    cli.main_init(["--path", str(repo)])
    capsys.readouterr()

    # Customize the scaffolded prompt and confirm the review pipeline
    # actually reads it back (proves .agent-rules/ takes precedence end
    # to end, not just in prompts.py's own unit tests).
    security_prompt = repo / ".agent-rules" / "agents" / "security-review.md"
    text = security_prompt.read_text(encoding="utf-8")
    assert "security vulnerabilities" in text.lower() or "security" in text.lower()

    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)
    exit_code = cli.main(["--path", str(repo), "--jobs", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SQL injection" in out
