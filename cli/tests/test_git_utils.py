import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import git_utils


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


def test_is_git_repo(tmp_path: Path):
    repo = make_repo(tmp_path)
    assert git_utils.is_git_repo(repo)
    assert not git_utils.is_git_repo(tmp_path)


def test_changed_files_detects_modification_and_untracked(tmp_path: Path):
    repo = make_repo(tmp_path)
    base = git_utils.current_commit(repo)
    (repo / "a.py").write_text("x = 2\n")
    (repo / "b.py").write_text("y = 1\n")  # untracked, never `git add`ed
    changed = git_utils.changed_files(repo, base)
    paths = {c.path: c.status for c in changed}
    assert paths["a.py"] == "M"
    # untracked files must still surface as reviewable ("added") -- a
    # brand-new file someone just wrote is exactly what a review tool
    # must not silently skip.
    assert paths["b.py"] == "A"


def test_diff_for_file_untracked_shows_whole_file_as_added(tmp_path: Path):
    repo = make_repo(tmp_path)
    base = git_utils.current_commit(repo)
    (repo / "b.py").write_text("y = 1\n")
    diff = git_utils.diff_for_file(repo, base, "b.py")
    assert "+y = 1" in diff


def test_diff_for_file_contains_change(tmp_path: Path):
    repo = make_repo(tmp_path)
    base = git_utils.current_commit(repo)
    (repo / "a.py").write_text("x = 2\n")
    diff = git_utils.diff_for_file(repo, base, "a.py")
    assert "-x = 1" in diff
    assert "+x = 2" in diff


def test_blob_hash_changes_with_content(tmp_path: Path):
    repo = make_repo(tmp_path)
    h1 = git_utils.blob_hash(repo, "a.py")
    (repo / "a.py").write_text("x = 999\n")
    h2 = git_utils.blob_hash(repo, "a.py")
    assert h1 != h2
    assert git_utils.blob_hash(repo, "does_not_exist.py") is None


def test_resolve_base_ref_falls_back_to_empty_tree_when_nothing_resolves(
    tmp_path: Path,
):
    repo = make_repo(tmp_path)
    # Neither "main" nor "master" (local or origin/) exists once the
    # current branch is renamed to something else entirely -- this must
    # fall all the way through to the literal empty-tree sentinel, not
    # just "some truthy ref" (which the host's default branch name could
    # satisfy by accident, hiding a regression that drops the fallback).
    subprocess.run(["git", "branch", "-M", "trunk"], cwd=repo, check=True)
    ref = git_utils.resolve_base_ref(repo, None)
    assert ref == git_utils.EMPTY_TREE_SENTINEL


def test_resolve_base_ref_honors_an_explicit_resolvable_ref(tmp_path: Path):
    repo = make_repo(tmp_path)
    subprocess.run(["git", "branch", "feature"], cwd=repo, check=True)
    ref = git_utils.resolve_base_ref(repo, "feature")
    assert ref == "feature"


def test_resolve_base_ref_prefers_origin_main_over_local_main(tmp_path: Path):
    repo = make_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    base_sha = git_utils.current_commit(repo)
    # A real `origin/main` remote-tracking ref, without needing an actual
    # remote -- just a ref under refs/remotes/, which is all `rev-parse
    # --verify origin/main` needs to resolve it.
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base_sha],
        cwd=repo,
        check=True,
    )
    ref = git_utils.resolve_base_ref(repo, None)
    assert ref == "origin/main"
