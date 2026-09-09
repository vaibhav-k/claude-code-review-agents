"""Ties together routing, caching, prompts, and the model client into one
run: diff -> route -> (cache hit or model call) -> parse -> aggregate.

This is the direct equivalent of .claude/commands/review-pr.md, reimplemented
as real control flow instead of instructions for an orchestrating Claude
session to follow -- so it runs the same way whether or not Claude Code is
what invoked it.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
from pathlib import Path

from . import findings as findings_mod
from . import git_utils, routing
from .agents_client import Reviewer
from .cache import CACHE_DIRNAME, AgentCache
from .prompts import AgentPrompt

_ALWAYS_IGNORED_PREFIXES = (f"{CACHE_DIRNAME}/", ".agent-rules/")


@dataclasses.dataclass(frozen=True)
class FileReviewResult:
    path: str
    agents: list[str]
    from_cache: bool
    findings: list[findings_mod.Finding]
    # None on success; set (and `findings`/`agents` reflect only whatever
    # completed before the failure) if a per-agent model call raised.
    error: str | None = None
    # Agents that were routed to this file but never actually ran because
    # their prompt couldn't be loaded (e.g. a customized .agent-rules/
    # agents/ file was renamed or removed) -- surfaced so this is a
    # visible warning, not a silent gap.
    missing_agents: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class ReviewRun:
    base_ref: str
    files: list[FileReviewResult]

    @property
    def all_findings(self) -> list[findings_mod.Finding]:
        combined: list[findings_mod.Finding] = []
        for file_result in self.files:
            combined.extend(file_result.findings)
        return findings_mod.sort_findings(combined)

    @property
    def cache_hits(self) -> int:
        return sum(1 for f in self.files if f.from_cache)

    @property
    def cache_misses(self) -> int:
        return sum(1 for f in self.files if not f.from_cache and f.error is None)

    @property
    def failed_files(self) -> list[FileReviewResult]:
        return [f for f in self.files if f.error is not None]


def _review_one_file(
    path: str,
    agents_for_file: list[str],
    diff_text: str,
    blob_hash: str | None,
    prompts: dict[str, AgentPrompt],
    cache: AgentCache,
    reviewer: Reviewer,
) -> FileReviewResult:
    cache_key_hash = blob_hash or "deleted"

    cached = cache.get_if_fresh(path, cache_key_hash, agents_for_file)
    if cached is not None:
        parsed = findings_mod.parse("cached", cached)
        return FileReviewResult(path=path, agents=agents_for_file, from_cache=True, findings=parsed)

    completed_agents: list[str] = []
    missing_agents: list[str] = []
    raw_by_agent: dict[str, str] = {}
    parsed_all: list[findings_mod.Finding] = []
    try:
        for agent_name in agents_for_file:
            agent = prompts.get(agent_name)
            if agent is None:
                missing_agents.append(agent_name)
                continue
            user_message = f"File: {path}\n\nDiff (base..working tree):\n```diff\n{diff_text}\n```"
            raw = reviewer.complete(agent.system_prompt, user_message)
            raw_by_agent[agent_name] = raw
            completed_agents.append(agent_name)
            parsed_all.extend(findings_mod.parse(agent_name, raw))
    except Exception as exc:  # deliberately broad: a single
        # file's model/network failure (a transient Foundry timeout, rate
        # limit, or the enriched connection-error RuntimeError from
        # agents_client.py) must not abort every other file's
        # already-completed work in this run's ThreadPoolExecutor fan-out
        # (see run_review()) -- reported back as a per-file error instead
        # of raised, and deliberately NOT cached, since a transient
        # failure must be retried on the next run, never remembered as if
        # it were a completed (or clean) review.
        return FileReviewResult(
            path=path,
            agents=agents_for_file,
            from_cache=False,
            findings=parsed_all,
            error=str(exc),
            missing_agents=missing_agents,
        )

    # Cache only the agents that actually ran. An agent whose prompt
    # couldn't be loaded was never invoked, so it must not be recorded as
    # having cleanly reviewed the file -- that would permanently and
    # silently hide it from every future run against this same content.
    # (Keying the cache off a smaller set than what's currently routed
    # also means the next run's `get_if_fresh` naturally misses and
    # retries the full routed set, rather than staying silently stuck.)
    if completed_agents:
        combined_raw = "\n".join(raw_by_agent[a] for a in completed_agents)
        cache.put(path, cache_key_hash, completed_agents, combined_raw)

    return FileReviewResult(
        path=path,
        agents=completed_agents,
        from_cache=False,
        findings=parsed_all,
        missing_agents=missing_agents,
    )


def run_review(
    repo_root: Path,
    base_ref: str | None,
    prompts: dict[str, AgentPrompt],
    cache: AgentCache,
    reviewer: Reviewer,
    max_workers: int = 4,
) -> ReviewRun:
    resolved_base = git_utils.resolve_base_ref(repo_root, base_ref)
    changed = [
        c
        for c in git_utils.changed_files(repo_root, resolved_base)
        if c.status != "D" and not c.path.startswith(_ALWAYS_IGNORED_PREFIXES)
    ]

    # Each changed file's diff is fetched exactly once here and threaded
    # through to _review_one_file, instead of being fetched again inside
    # it -- a duplicate git subprocess call per file that used to double
    # the git overhead of every run (and a fresh-repo/agent-init scan,
    # which diffs against the empty-tree sentinel and so treats every
    # tracked file as "added," is exactly the case where that overhead
    # scales with the whole repo, not just a PR's file count).
    routed: list[tuple[str, list[str], str]] = []
    for changed_file in changed:
        diff_text = git_utils.diff_for_file(repo_root, resolved_base, changed_file.path)
        if routing.is_semantic_noise(diff_text):
            continue
        decision = routing.route_file(changed_file.path, diff_text)
        if decision.agents:
            routed.append((changed_file.path, decision.agents, diff_text))

    cache.prune_missing({path for path, _, _ in routed})

    # One batched call instead of one `git hash-object` subprocess per
    # routed file.
    blob_hashes = git_utils.blob_hashes(repo_root, [path for path, _, _ in routed])

    results: list[FileReviewResult] = []
    if routed:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    _review_one_file,
                    path,
                    agents,
                    diff_text,
                    blob_hashes.get(path),
                    prompts,
                    cache,
                    reviewer,
                )
                for path, agents, diff_text in routed
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

    cache.save()
    # Deterministic output order regardless of thread completion order.
    results.sort(key=lambda r: r.path)
    return ReviewRun(base_ref=resolved_base, files=results)
