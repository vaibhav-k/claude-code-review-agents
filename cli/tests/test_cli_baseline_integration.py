"""End-to-end tests for milestone 1 ("finding lifecycle and CI policy"):
--baseline, --update-baseline, --new-only, and --fail-on, exercised
through the real `agent-review` entry point (cli.main) the same way
test_cli_integration.py already does for --json/--sarif/--max-files/etc.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import baseline, cli

_FINDING_1 = (
    "[CRITICAL] handlers.py:3 — SQL injection\n"
    "Impact: attacker-controlled input reaches the query\n"
    "Fix: parameterize the query\n"
)
_FINDING_2 = (
    "[HIGH] handlers.py:6 — Hardcoded credential\n"
    "Impact: a hardcoded secret is committed to source control\n"
    "Fix: move the secret to a secrets manager\n"
)
_CONTENT_A = (
    "def get_user(request):\n"
    '    user_id = request.args.get("id")\n'
    '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
    "    return db.execute(query).fetchone()\n"
)
_CONTENT_B = _CONTENT_A + '\n# MARK_SECOND_FINDING\nAPI_KEY = "sk-hardcoded-1234567890"\n'


class ScriptedReviewer:
    """Like test_cli_integration.py's FakeReviewer, but the security-
    review response also depends on the user message content (not just
    which specialist is being asked) -- needed to simulate the diff
    genuinely changing between two runs (one finding becomes two), which
    is what actually exercises NEW vs EXISTING classification.
    """

    def __init__(self, model=None, resource=None, use_entra_id=None, **_ignored):
        self.model = model
        self.calls = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        if "security vulnerabilities" not in system_prompt:
            return "No high-impact issues found."
        if "MARK_SECOND_FINDING" in user_message:
            return _FINDING_1 + _FINDING_2
        return _FINDING_1


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


# -- creating / replacing / not-modifying the baseline -----------------


def test_update_baseline_creates_the_default_baseline_file(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    out = capsys.readouterr().out
    assert exit_code == 0

    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    assert baseline_path.is_file()
    assert "Baseline written to" in out
    loaded = baseline.load_baseline(baseline_path)
    assert len(loaded.entries) == 1
    assert loaded.entries[0].rule_id == "SEC-INJECTION-001"


def test_a_normal_review_never_modifies_the_baseline(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    before = baseline_path.read_bytes()
    before_mtime = baseline_path.stat().st_mtime_ns

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--baseline", str(baseline_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "EXISTING" in out  # classified, but the file itself is untouched
    assert baseline_path.read_bytes() == before
    assert baseline_path.stat().st_mtime_ns == before_mtime


def test_update_baseline_replaces_existing_content(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    loaded = baseline.load_baseline(baseline_path)
    assert len(loaded.entries) == 2  # both findings now captured


def test_bare_review_never_creates_a_baseline_file(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    cli.main(["--path", str(repo), "--jobs", "1"])
    capsys.readouterr()
    assert not (repo / baseline.DEFAULT_BASELINE_PATH).exists()


# -- new/existing classification + --new-only ---------------------------


def test_new_only_shows_only_the_finding_absent_from_the_baseline(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    exit_code = cli.main(
        [
            "--path",
            str(repo),
            "--jobs",
            "1",
            "--baseline",
            str(baseline_path),
            "--new-only",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Hardcoded credential" in out
    assert "SQL injection" not in out  # existing, filtered out by --new-only


def test_without_new_only_both_statuses_are_shown(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--baseline", str(baseline_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Hardcoded credential" in out
    assert "SQL injection" in out
    assert "NEW" in out
    assert "EXISTING" in out


# -- --fail-on, alone and combined with --baseline/--new-only ------------


def test_fail_on_high_exits_nonzero_for_a_new_high_finding_scoped_by_new_only(
    monkeypatch, tmp_path, capsys
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    exit_code = cli.main(
        [
            "--path",
            str(repo),
            "--jobs",
            "1",
            "--baseline",
            str(baseline_path),
            "--new-only",
            "--fail-on",
            "high",
        ]
    )
    capsys.readouterr()
    assert exit_code == 1  # the new HIGH finding meets the threshold


def test_fail_on_critical_with_new_only_ignores_the_existing_critical_finding(
    monkeypatch, tmp_path, capsys
):
    # This is the milestone's own worked example: --baseline --new-only
    # --fail-on should fail ONLY when a NEW finding meets the threshold.
    # The only CRITICAL finding here (the SQL injection) is EXISTING
    # (already in the baseline); the only NEW finding is HIGH, not
    # CRITICAL -- so --fail-on critical must NOT fail this run.
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    exit_code = cli.main(
        [
            "--path",
            str(repo),
            "--jobs",
            "1",
            "--baseline",
            str(baseline_path),
            "--new-only",
            "--fail-on",
            "critical",
        ]
    )
    capsys.readouterr()
    assert exit_code == 0


def test_fail_on_without_new_only_sees_the_existing_critical_finding(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    exit_code = cli.main(
        [
            "--path",
            str(repo),
            "--jobs",
            "1",
            "--baseline",
            str(baseline_path),
            "--fail-on",
            "critical",  # no --new-only: full set evaluated
        ]
    )
    capsys.readouterr()
    assert exit_code == 1


def test_fail_on_multiple_severities_is_equivalent_to_the_least_severe_one(
    monkeypatch, tmp_path, capsys
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_B)  # both findings present
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--fail-on", "critical,high"])
    capsys.readouterr()
    assert exit_code == 1  # both the CRITICAL and the HIGH finding qualify


def test_fail_on_does_not_fire_on_clean_diff(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text("def noop():\n    return 1\n")
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    exit_code = cli.main(["--path", str(repo), "--jobs", "1", "--fail-on", "low"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No high-impact issues found." in out


# -- errors ---------------------------------------------------------------


def test_missing_explicit_baseline_file_errors_cleanly(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--path", str(repo), "--jobs", "1", "--baseline", str(repo / "nope.json")])
    message = str(exc_info.value)
    assert "baseline file not found" in message
    assert "Traceback" not in message


def test_malformed_baseline_file_errors_cleanly(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    bad_baseline = repo / "bad-baseline.json"
    bad_baseline.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--path", str(repo), "--jobs", "1", "--baseline", str(bad_baseline)])
    assert "not valid JSON" in str(exc_info.value)


def test_invalid_fail_on_severity_errors_cleanly(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--path", str(repo), "--jobs", "1", "--fail-on", "extreme"])
    message = str(exc_info.value)
    assert "unknown severity" in message.lower()
    assert "Traceback" not in message


# -- JSON output ------------------------------------------------------------


def test_json_output_includes_rule_id_fingerprint_and_status_with_baseline(
    monkeypatch, tmp_path, capsys
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)
    cli.main(["--path", str(repo), "--jobs", "1", "--update-baseline"])
    capsys.readouterr()

    (repo / "handlers.py").write_text(_CONTENT_B)
    baseline_path = repo / baseline.DEFAULT_BASELINE_PATH
    cli.main(["--path", str(repo), "--jobs", "1", "--baseline", str(baseline_path), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert len(payload["findings"]) == 2
    statuses = {f["title"]: f["status"] for f in payload["findings"]}
    assert statuses["SQL injection"] == "existing"
    assert statuses["Hardcoded credential"] == "new"
    for finding in payload["findings"]:
        assert finding["rule_id"]
        assert len(finding["fingerprint"]) == 64
    assert payload["baseline"]["new_count"] == 1
    assert payload["baseline"]["existing_count"] == 1


def test_json_output_omits_status_and_baseline_key_without_a_baseline(
    monkeypatch, tmp_path, capsys
):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(_CONTENT_A)
    monkeypatch.setattr(cli, "AnthropicFoundryReviewer", ScriptedReviewer)

    cli.main(["--path", str(repo), "--jobs", "1", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "baseline" not in payload
    assert "status" not in payload["findings"][0]
    assert payload["findings"][0]["rule_id"] == "SEC-INJECTION-001"
