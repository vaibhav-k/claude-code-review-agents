"""End-to-end tests that exercise the actual `agent-review`/`agent-init`
entry points (cli.main / cli.main_init) against synthetic repos, with a
fake model client swapped in for AnthropicFoundryReviewer -- this is the closest
this test suite gets to "run the real CLI," short of spending a live API
key, and it's what catches wiring bugs the lower-level orchestrator/unit
tests can't (argument parsing, output formatting, cache-hit reporting
through the full stack).
"""

import json
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


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "handlers.py").write_text("def noop():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_full_cli_review_end_to_end_with_fake_model(monkeypatch, tmp_path, capsys):
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


def test_json_flag_emits_structured_findings_instead_of_text(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0

    payload = json.loads(out)  # must be the ONLY thing on stdout, and valid JSON
    assert payload["cache_misses"] == 1
    assert payload["failed_files"] == []
    assert payload["missing_agents"] == []
    assert payload["malformed_agents"] == []
    assert len(payload["findings"]) == 1
    finding = payload["findings"][0]
    assert finding["severity"] == "CRITICAL"
    assert "SQL injection" in finding["title"]


def test_sarif_flag_emits_a_valid_sarif_log_instead_of_text(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--sarif"])
    out = capsys.readouterr().out
    assert exit_code == 0

    log = json.loads(out)  # must be the ONLY thing on stdout, and valid JSON
    assert log["version"] == "2.1.0"
    sarif_run = log["runs"][0]
    assert sarif_run["tool"]["driver"]["name"] == "agent-review"
    result = sarif_run["results"][0]
    # The diff contains an f-string-built SELECT (see FakeReviewer's
    # scripted handlers.py content above), which rules.py's
    # security-review INJECTION category pattern matches -- see rules.py
    # for the full deterministic rule_id derivation.
    assert result["ruleId"] == "SEC-INJECTION-001"
    assert result["properties"]["agent"] == "security-review"
    assert result["level"] == "error"  # CRITICAL -> error
    assert "SQL injection" in result["message"]["text"]


def test_json_and_sarif_flags_are_mutually_exclusive(tmp_path):
    repo = make_repo(tmp_path)
    parser = cli.build_review_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["review", "--path", str(repo), "--json", "--sarif"])
    assert exc_info.value.code == 2  # argparse's usage-error exit code


def test_max_files_flag_skips_lower_priority_files_and_reports_them(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    (repo / "narrow.py").write_text("CACHE_SIZE = 128\ndef compute(x):\n    return x * 2\n")
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--max-files", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "skipped due to --max-files" in out
    assert "narrow.py" in out
    assert "SQL injection" in out  # the higher-priority file was still reviewed


def test_ignore_findings_yml_suppresses_a_finding_and_reports_it(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    (claude_dir / "ignore-findings.yml").write_text(
        "suppressions:\n"
        "  - agent: security-review\n"
        '    location: "handlers.py:*"\n'
        '    reason: "triaged as a non-issue"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SQL injection" not in out  # suppressed out of the report
    assert "1 finding(s) suppressed" in out
    assert "triaged as a non-issue" in out
    assert "No high-impact issues found." in out


def test_fail_on_findings_flag_sets_nonzero_exit(monkeypatch, tmp_path, capsys):
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


def test_clean_diff_yields_zero_exit_even_with_fail_on_findings(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text("def noop():\n    return 1\n")
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", FakeReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--fail-on-findings"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No high-impact issues found." in out


def test_init_then_review_uses_the_scaffolded_agent_rules(monkeypatch, tmp_path, capsys):
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
