import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import cli, discovery, healing


def _set_resource_only(monkeypatch: pytest.MonkeyPatch, resource: str = "test-resource"):
    """Configure a Foundry resource but leave both auth modes unset, so a
    test can isolate the "no API key" validation step specifically."""
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", resource)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_USE_ENTRA_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_MODEL", raising=False)


def _clear_all_foundry_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_USE_ENTRA_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_MODEL", raising=False)


def make_git_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


# -- argument parsing / implicit "review" default ----------------------


def test_bare_path_implies_review_command(tmp_path: Path):
    repo = make_git_repo(tmp_path)
    parser = cli.build_review_parser()
    argv = ["review", "--path", str(repo)]  # main() would have inserted "review"
    args = parser.parse_args(argv)
    assert args.command == "review"
    assert args.func is cli.cmd_review
    assert args.path == str(repo)


def test_main_inserts_review_when_no_known_subcommand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = make_git_repo(tmp_path)
    _set_resource_only(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--path", str(repo)])
    # Should reach cmd_review's API-key check, not an argparse usage error.
    assert "API key" in str(exc_info.value)


def test_main_routes_commit_subcommand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = make_git_repo(tmp_path)
    _clear_all_foundry_env(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["commit", "--path", str(repo)])
    # Hit before a reviewer is ever built -- no Foundry config needed.
    assert "No staged changes" in str(exc_info.value)


def test_main_routes_heal_subcommand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = make_git_repo(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["heal", "--path", str(repo)])
    assert "No supported test runner" in str(exc_info.value)


def test_help_does_not_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0


# -- repo path validation -------------------------------------------------


def test_resolve_git_repo_rejects_nonexistent_path(tmp_path: Path):
    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_git_repo(str(tmp_path / "does_not_exist"))
    assert "is not a directory" in str(exc_info.value)


def test_resolve_git_repo_rejects_non_git_directory(tmp_path: Path):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()
    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_git_repo(str(plain_dir))
    assert "is not a git repository" in str(exc_info.value)


# -- missing API key surfaces cleanly, not as a raw traceback ------------


def test_review_without_api_key_fails_cleanly_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = make_git_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    _set_resource_only(monkeypatch)
    parser = cli.build_review_parser()
    args = parser.parse_args(["review", "--path", str(repo)])
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_review(args)
    message = str(exc_info.value)
    assert "No Microsoft Foundry API key found" in message
    assert "Traceback" not in message


def test_review_without_resource_fails_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = make_git_repo(tmp_path)
    _clear_all_foundry_env(monkeypatch)
    parser = cli.build_review_parser()
    args = parser.parse_args(["review", "--path", str(repo)])
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_review(args)
    message = str(exc_info.value)
    assert "No Microsoft Foundry resource configured" in message
    assert "Traceback" not in message


def test_use_entra_id_flag_defaults_to_none_and_parses_true(tmp_path: Path):
    repo = make_git_repo(tmp_path)
    parser = cli.build_review_parser()
    default_args = parser.parse_args(["review", "--path", str(repo)])
    assert default_args.use_entra_id is None

    entra_args = parser.parse_args(["review", "--path", str(repo), "--use-entra-id"])
    assert entra_args.use_entra_id is True


def test_model_flag_defaults_to_none_so_the_env_var_can_apply(tmp_path: Path):
    # Must NOT default to DEFAULT_MODEL here -- that would make it
    # impossible for _build_reviewer to tell "flag not given" apart from
    # "flag given, happens to equal the default," which is exactly the
    # distinction needed to let ANTHROPIC_FOUNDRY_MODEL apply.
    repo = make_git_repo(tmp_path)
    parser = cli.build_review_parser()
    args = parser.parse_args(["review", "--path", str(repo)])
    assert args.model is None


def test_build_reviewer_uses_the_model_env_var_when_no_flag_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = make_git_repo(tmp_path)
    _set_resource_only(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "claude-haiku-4-5")
    parser = cli.build_review_parser()
    args = parser.parse_args(["review", "--path", str(repo)])
    reviewer = cli._build_reviewer(args)
    assert reviewer._model == "claude-haiku-4-5"


def test_build_reviewer_model_flag_overrides_the_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = make_git_repo(tmp_path)
    _set_resource_only(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "claude-haiku-4-5")
    parser = cli.build_review_parser()
    args = parser.parse_args(["review", "--path", str(repo), "--model", "claude-opus-5"])
    reviewer = cli._build_reviewer(args)
    assert reviewer._model == "claude-opus-5"


# -- heal's --apply safety gate ------------------------------------------

_FAKE_RUNNER: discovery.TestRunner = discovery.TestRunner(name="pytest", command=["pytest"])
_FAKE_FAILING_RESULT: healing.TestRunResult = healing.TestRunResult(
    runner=_FAKE_RUNNER, returncode=1, stdout="", stderr="assert 1 == 2"
)
_FAKE_PASSING_RESULT: healing.TestRunResult = healing.TestRunResult(
    runner=_FAKE_RUNNER, returncode=0, stdout="", stderr=""
)
_FAKE_PROPOSAL: healing.HealingProposal = healing.HealingProposal(
    explanation="root cause",
    diff="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n",
    raw_response="root cause\n```diff\n-a\n+b\n```\n",
)


def _configure_heal_env(monkeypatch: pytest.MonkeyPatch):
    _set_resource_only(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")


def test_heal_without_apply_never_calls_apply_patch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # cli.py:171's `if not args.apply: return 1` is the only thing
    # stopping a plain `agent-review heal` from writing a model-proposed
    # patch to the target repo -- a regression that drops or inverts it
    # would ship undetected without this test, since the only other heal
    # test exits earlier on "no test runner found."
    repo = make_git_repo(tmp_path)
    _configure_heal_env(monkeypatch)
    monkeypatch.setattr(cli, "run_tests", lambda repo, runner=None: _FAKE_FAILING_RESULT)
    monkeypatch.setattr(cli, "propose_fix", lambda result, reviewer: _FAKE_PROPOSAL)
    apply_calls = []
    monkeypatch.setattr(cli, "apply_patch", lambda repo, proposal: apply_calls.append(proposal))
    monkeypatch.setattr(cli, "check_patch_applies", lambda repo, proposal: (True, ""))

    parser = cli.build_review_parser()
    args = parser.parse_args(["heal", "--path", str(repo)])
    exit_code = cli.cmd_heal(args)

    assert exit_code == 1
    assert apply_calls == []


def test_heal_with_apply_flag_writes_and_reruns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = make_git_repo(tmp_path)
    _configure_heal_env(monkeypatch)
    run_tests_calls = []

    def _fake_run_tests(repo: Path, runner: str | None = None):
        run_tests_calls.append(runner)
        return _FAKE_FAILING_RESULT if len(run_tests_calls) == 1 else _FAKE_PASSING_RESULT

    monkeypatch.setattr(cli, "run_tests", _fake_run_tests)
    monkeypatch.setattr(cli, "propose_fix", lambda result, reviewer: _FAKE_PROPOSAL)
    monkeypatch.setattr(cli, "check_patch_applies", lambda repo, proposal: (True, ""))
    apply_calls = []
    monkeypatch.setattr(cli, "apply_patch", lambda repo, proposal: apply_calls.append(proposal))

    parser = cli.build_review_parser()
    args = parser.parse_args(["heal", "--path", str(repo), "--apply"])
    exit_code = cli.cmd_heal(args)

    assert len(apply_calls) == 1
    assert len(run_tests_calls) == 2  # initial failing run + post-apply rerun
    assert exit_code == 0


# -- agent-init through the CLI wiring -----------------------------------


def test_cmd_init_scaffolds_target_repo(tmp_path: Path):
    repo = make_git_repo(tmp_path)
    parser = cli.build_init_parser()
    args = parser.parse_args(["--path", str(repo)])
    exit_code = cli.cmd_init(args)
    assert exit_code == 0
    assert (repo / ".agent-rules" / "CLAUDE.md").exists()
    assert (repo / "DESIGN.md").exists()
    assert ".agent-cache/" in (repo / ".gitignore").read_text().splitlines()


def test_main_init_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    repo = make_git_repo(tmp_path)
    cli.main_init(["--path", str(repo)])
    capsys.readouterr()
    cli.main_init(["--path", str(repo)])
    out = capsys.readouterr().out
    assert "already initialized" in out
