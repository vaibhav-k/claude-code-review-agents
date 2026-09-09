import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.cache import AgentCache, ensure_gitignored


def test_miss_when_empty(tmp_path: Path):
    cache = AgentCache(tmp_path)
    assert cache.get_if_fresh("a.py", "hash1", ["security-review"]) is None


def test_hit_after_put_with_same_hash_and_agents(tmp_path: Path):
    cache = AgentCache(tmp_path)
    cache.put("a.py", "hash1", ["security-review"], "No high-impact issues found.")
    hit = cache.get_if_fresh("a.py", "hash1", ["security-review"])
    assert hit == "No high-impact issues found."


def test_miss_when_hash_changes(tmp_path: Path):
    cache = AgentCache(tmp_path)
    cache.put("a.py", "hash1", ["security-review"], "findings")
    assert cache.get_if_fresh("a.py", "hash2", ["security-review"]) is None


def test_miss_when_agent_set_changes(tmp_path: Path):
    cache = AgentCache(tmp_path)
    cache.put("a.py", "hash1", ["security-review"], "findings")
    assert cache.get_if_fresh("a.py", "hash1", ["security-review", "performance-review"]) is None
    # order shouldn't matter for a hit
    cache.put("b.py", "hash1", ["security-review", "performance-review"], "findings-b")
    assert (
        cache.get_if_fresh("b.py", "hash1", ["performance-review", "security-review"])
        == "findings-b"
    )


def test_persists_across_instances(tmp_path: Path):
    cache1 = AgentCache(tmp_path)
    cache1.put("a.py", "hash1", ["security-review"], "findings")
    cache1.save()

    cache2 = AgentCache(tmp_path)
    hit = cache2.get_if_fresh("a.py", "hash1", ["security-review"])
    assert hit == "findings"


def test_corrupt_manifest_is_treated_as_empty(tmp_path: Path):
    cache_dir = tmp_path / ".agent-cache"
    cache_dir.mkdir()
    (cache_dir / "manifest.json").write_text("{not valid json")
    cache = AgentCache(tmp_path)
    assert cache.get_if_fresh("a.py", "hash1", ["security-review"]) is None


def test_prune_missing_drops_stale_entries(tmp_path: Path):
    cache = AgentCache(tmp_path)
    cache.put("a.py", "hash1", ["security-review"], "findings-a")
    cache.put("b.py", "hash1", ["security-review"], "findings-b")
    cache.prune_missing({"a.py"})
    assert cache.get_if_fresh("a.py", "hash1", ["security-review"]) == "findings-a"
    assert cache.get_if_fresh("b.py", "hash1", ["security-review"]) is None


def test_ensure_gitignored_adds_entry_once(tmp_path: Path):
    added_first = ensure_gitignored(tmp_path)
    added_second = ensure_gitignored(tmp_path)
    assert added_first is True
    assert added_second is False
    content = (tmp_path / ".gitignore").read_text()
    assert content.count(".agent-cache/") == 1
