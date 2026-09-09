import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.commit import NoStagedChangesError, generate_commit_message


class ScriptedReviewer:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_raises_when_nothing_staged(tmp_path: Path):
    repo = make_repo(tmp_path)
    reviewer = ScriptedReviewer("feat: add thing")
    with pytest.raises(NoStagedChangesError):
        generate_commit_message(repo, reviewer)


def test_returns_clean_message_for_staged_diff(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    reviewer = ScriptedReviewer("fix: correct default value of x")
    message = generate_commit_message(repo, reviewer)
    assert message == "fix: correct default value of x"
    assert reviewer.calls  # the model was actually invoked with the staged diff
    assert "x = 2" in reviewer.calls[0][1]


def test_strips_trailer_lines_even_if_model_adds_them(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("x = 3\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    reviewer = ScriptedReviewer(
        "fix: bump default value\n\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_xyz\n"
    )
    message = generate_commit_message(repo, reviewer)
    assert message == "fix: bump default value"
    assert "Co-Authored-By" not in message
    assert "Claude-Session" not in message


def test_strips_code_fences(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("x = 4\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    reviewer = ScriptedReviewer("```\nchore: tweak constant\n```")
    message = generate_commit_message(repo, reviewer)
    assert message == "chore: tweak constant"
