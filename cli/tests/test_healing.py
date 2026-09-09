import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.healing import (
    HealingProposal,
    NoTestRunnerFoundError,
    _split_explanation_and_diff,
    apply_patch,
    check_patch_applies,
    propose_fix,
    run_tests,
)


class ScriptedReviewer:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


def make_pytest_repo(tmp_path: Path, test_body: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "pytest.ini").write_text("[pytest]\n")
    (repo / "test_sample.py").write_text(test_body)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_no_runner_found_raises(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    with pytest.raises(NoTestRunnerFoundError):
        run_tests(repo)


def test_run_tests_reports_passing_suite(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_ok():\n    assert 1 + 1 == 2\n")
    result = run_tests(repo)
    assert result.passed
    assert result.runner.name == "pytest"


def test_run_tests_reports_failing_suite(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_broken():\n    assert 1 + 1 == 3\n")
    result = run_tests(repo)
    assert not result.passed
    assert "assert" in result.stdout.lower() or "assert" in result.stderr.lower()


def test_run_tests_reports_a_timeout_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression coverage for the TimeoutExpired branch (and
    # _decode_partial_output, which exists specifically to handle
    # TimeoutExpired's bytes|str|None stdout/stderr) -- previously
    # untested, so a regression here would only ever surface during a
    # real hung test suite in production.
    repo = make_pytest_repo(tmp_path, "def test_ok():\n    assert True\n")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["pytest"],
            timeout=1,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    result = run_tests(repo, timeout=1)
    assert result.returncode == -1
    assert result.stdout == "partial stdout"
    assert "partial stderr" in result.stderr
    assert "timed out after 1s" in result.stderr


def test_propose_fix_rejects_a_passing_result(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_ok():\n    assert True\n")
    result = run_tests(repo)
    with pytest.raises(ValueError):
        propose_fix(result, ScriptedReviewer("irrelevant"))


def test_propose_fix_parses_explanation_and_diff(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_broken():\n    assert 1 + 1 == 3\n")
    result = run_tests(repo)
    scripted = (
        "The test asserts 1 + 1 == 3, which is false; the expected value is wrong.\n\n"
        "```diff\n"
        "--- a/test_sample.py\n"
        "+++ b/test_sample.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def test_broken():\n"
        "-    assert 1 + 1 == 3\n"
        "+    assert 1 + 1 == 2\n"
        "```\n"
    )
    proposal = propose_fix(result, ScriptedReviewer(scripted))
    assert "expected value is wrong" in proposal.explanation
    assert proposal.has_diff
    assert "+    assert 1 + 1 == 2" in proposal.diff
    assert "```" not in proposal.diff


def test_check_and_apply_patch_end_to_end(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_broken():\n    assert 1 + 1 == 3\n")
    result = run_tests(repo)
    assert not result.passed

    scripted = (
        "Wrong expected value.\n\n"
        "```diff\n"
        "--- a/test_sample.py\n"
        "+++ b/test_sample.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def test_broken():\n"
        "-    assert 1 + 1 == 3\n"
        "+    assert 1 + 1 == 2\n"
        "```\n"
    )
    proposal = propose_fix(result, ScriptedReviewer(scripted))

    ok, _err = check_patch_applies(repo, proposal)
    assert ok

    # Nothing should be written to disk until apply_patch is explicitly called.
    assert "1 + 1 == 3" in (repo / "test_sample.py").read_text()

    apply_patch(repo, proposal)
    assert "assert 1 + 1 == 2" in (repo / "test_sample.py").read_text()

    rerun = run_tests(repo)
    assert rerun.passed


def test_check_patch_applies_reports_failure_for_bad_patch(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_ok():\n    assert True\n")
    bad_scripted = (
        "Not a real fix.\n\n"
        "```diff\n"
        "--- a/does_not_exist.py\n"
        "+++ b/does_not_exist.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
        "```\n"
    )
    # passing repo -- we only need _split_explanation_and_diff's parsing here,
    # not a real failing TestRunResult.
    explanation, diff = _split_explanation_and_diff(bad_scripted)
    proposal = HealingProposal(explanation=explanation, diff=diff, raw_response=bad_scripted)
    ok, err = check_patch_applies(repo, proposal)
    assert not ok
    assert err

    with pytest.raises(RuntimeError):
        apply_patch(repo, proposal)


def test_empty_diff_block_is_not_applicable(tmp_path: Path):
    repo = make_pytest_repo(tmp_path, "def test_ok():\n    assert True\n")
    proposal = HealingProposal(
        explanation="could not identify a safe fix", diff="", raw_response=""
    )
    ok, err = check_patch_applies(repo, proposal)
    assert not ok
    assert "no diff" in err.lower()
