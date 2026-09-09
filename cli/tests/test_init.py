import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.init import init_repo


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_init_creates_agent_rules_agents_and_design_md(tmp_path: Path):
    repo = make_repo(tmp_path)
    result = init_repo(repo)

    claude_md = repo / ".agent-rules" / "CLAUDE.md"
    assert claude_md.exists()

    agent_files = sorted((repo / ".agent-rules" / "agents").glob("*.md"))
    assert len(agent_files) == 7
    names = {p.name for p in agent_files}
    assert "security-review.md" in names
    assert "triage-router.md" not in names  # routing is pure Python, not a prompt

    assert (repo / "DESIGN.md").exists()
    assert not result.already_initialized
    assert ".agent-rules/CLAUDE.md" in result.created_files
    assert "DESIGN.md" in result.created_files


def test_init_adds_agent_cache_to_gitignore(tmp_path: Path):
    repo = make_repo(tmp_path)
    init_repo(repo)
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".agent-cache/" in gitignore.splitlines()


def test_init_is_idempotent_and_never_overwrites(tmp_path: Path):
    repo = make_repo(tmp_path)
    init_repo(repo)

    custom_marker = "# TEAM CUSTOMIZATION -- do not remove\n"
    claude_md = repo / ".agent-rules" / "CLAUDE.md"
    claude_md.write_text(custom_marker + claude_md.read_text(encoding="utf-8"))
    design_md = repo / "DESIGN.md"
    design_md.write_text("# Our real design doc\n")

    result = init_repo(repo)

    assert claude_md.read_text(encoding="utf-8").startswith(custom_marker)
    assert design_md.read_text(encoding="utf-8") == "# Our real design doc\n"
    assert result.already_initialized
    assert ".agent-rules/CLAUDE.md" in result.skipped_files
    assert "DESIGN.md" in result.skipped_files


def test_init_on_repo_with_existing_claude_dir_still_creates_agent_rules(
    tmp_path: Path,
):
    # A repo that already has a Claude-Code-native .claude/agents/ should
    # still get its own .agent-rules/ copy from init -- .agent-rules/ is
    # what makes the *CLI* portable across machines without depending on
    # Claude Code being installed there too.
    repo = make_repo(tmp_path)
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("existing rules\n")

    result = init_repo(repo)
    assert (repo / ".agent-rules" / "CLAUDE.md").exists()
    assert not result.already_initialized
