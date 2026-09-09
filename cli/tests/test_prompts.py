import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import prompts


def test_loads_bundled_defaults_when_repo_has_no_rules(tmp_path: Path):
    loaded = prompts.load_agent_prompts(tmp_path)
    assert "security-review" in loaded
    assert "testing-coverage-review" in loaded
    assert "triage-router" not in loaded
    assert len(loaded) == 7
    sec = loaded["security-review"]
    assert "security vulnerabilities" in sec.description
    assert "Evidence Bar" in sec.system_prompt  # shared CLAUDE.md rules included
    assert "SQL injection" in sec.system_prompt or "Injection" in sec.system_prompt


def test_prefers_agent_rules_dir_over_bundled(tmp_path: Path):
    rules = tmp_path / ".agent-rules"
    (rules / "agents").mkdir(parents=True)
    (rules / "CLAUDE.md").write_text("SHARED RULES MARKER\n")
    (rules / "agents" / "security-review.md").write_text(
        "---\nname: security-review\ndescription: custom override\n---\nCUSTOM BODY\n"
    )
    loaded = prompts.load_agent_prompts(tmp_path)
    assert len(loaded) == 1
    assert loaded["security-review"].description == "custom override"
    assert "SHARED RULES MARKER" in loaded["security-review"].system_prompt
    assert "CUSTOM BODY" in loaded["security-review"].system_prompt


def test_prefers_dot_claude_when_no_agent_rules(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("REPO SHARED RULES\n")
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "security-review.md").write_text(
        "---\nname: security-review\ndescription: from dot claude\n---\nBODY\n"
    )
    loaded = prompts.load_agent_prompts(tmp_path)
    assert loaded["security-review"].description == "from dot claude"
