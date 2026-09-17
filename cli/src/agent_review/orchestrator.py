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
from . import suppressions as suppressions_mod
from .agents_client import Reviewer
from .cache import CACHE_DIRNAME, AgentCache
from .prompts import AgentPrompt

_ALWAYS_IGNORED_PREFIXES = (f"{CACHE_DIRNAME}/", ".agent-rules/")

# A single file's diff beyond this many lines gets truncated before it's
# ever sent to a specialist -- a large generated-content commit, a wide
# multi-file rename, or a genuinely huge refactor can otherwise burn most
# of a model's context window on one file, at real cost and with no
# guarantee the response even completes. Truncating with a clear marker
# (see _truncate_diff) beats either silently dropping the file or letting
# the call fail outright -- the specialist still reviews what it can see,
# and FileReviewResult.truncated says so instead of implying full coverage.
MAX_DIFF_LINES = 4000


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
    # Agents that DID run but returned a response findings_mod.parse()
    # can't tell apart from a genuine clean review (empty, or text that
    # doesn't match the output contract at all) -- see
    # findings_mod.is_malformed_response(). Surfaced the same way as
    # missing_agents: a visible warning instead of silently reporting
    # "no findings," and never cached as a completed review (see
    # _review_one_file), so it's retried on the next run instead of
    # permanently masking whatever that specialist would have said.
    malformed_agents: list[str] = dataclasses.field(default_factory=list)
    # True if this file's diff exceeded MAX_DIFF_LINES and was cut before
    # being sent to any specialist -- see _truncate_diff(). Findings for a
    # truncated file only reflect the portion that was actually sent.
    truncated: bool = False


@dataclasses.dataclass(frozen=True)
class ReviewRun:
    base_ref: str
    files: list[FileReviewResult]
    # Paths that were routed (would otherwise have been reviewed) but got
    # dropped by a --max-files budget before any model call was made --
    # see run_review()'s max_files parameter. Distinct from failed_files
    # (which DID get attempted) and from routing's own is_excluded_from_review
    # (which never counted as reviewable in the first place).
    skipped_for_budget: list[str] = dataclasses.field(default_factory=list)
    # Findings that DID come back from a specialist but were then dropped
    # per the target repo's own .claude/ignore-findings.yml (see
    # suppressions.py) -- e.g. an accepted false positive or accepted
    # risk a team has already triaged. Distinct from every other
    # exclusion above: this is the only one that happens after a real
    # model call and a real parsed finding, purely because a human
    # already decided it doesn't need to keep coming back.
    suppressed: list[suppressions_mod.SuppressedFinding] = dataclasses.field(default_factory=list)

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


# Real-world friction (2026-09-17): a first live-model run against the
# tests/fixtures/ validation matrix (see DESIGN.md Section G) surfaced a
# genuine false negative traceable to this gap -- testing-coverage-review's
# own prompt body instructs it to "confirm by reading the test diff... do
# not assume absence without checking," language written for a real
# Claude Code session where that agent has live Read/Grep/Bash tools. Sent
# through this CLI as a single text completion (see agents_client.Reviewer),
# it has no such tools -- and, taken literally, that instruction gives a
# model a defensible-sounding reason to withhold an otherwise-clear finding
# ("I was told to verify by checking; I have no way to check; therefore I
# shouldn't assume") rather than treat the diff in front of it as complete.
# The same run also showed several specialists that already have an
# explicit exclusion rule for a subtle case (a migration that widens rather
# than narrows a column, a loop bounded to a fixed small constant, a class
# that delegates resource release to its own close()) still misapply it
# live -- not a capability gap, a judgment one, addressed instead by adding
# a concrete counter-example directly to each affected agent's own
# Explicit exclusions section. This note is the fix for the FIRST kind of
# gap; it's appended after the diff (not before) so it's the last thing a
# specialist reads before responding.
_NO_TOOL_ACCESS_NOTE = (
    "Note: you are running in text-completion mode here, not a live Claude "
    "Code session -- you have no Read, Grep, or Bash tool calls available, "
    "regardless of what your instructions above say about using them. The "
    "diff above is your complete and only evidence. Where your instructions "
    'say to "check", "open", "confirm by reading", or "Grep for" '
    "something not shown above, treat that as something you cannot do here "
    "-- reason from the diff alone, and do not withhold an otherwise-"
    "supported finding merely because you cannot perform a check your "
    "instructions describe."
)


def build_review_user_message(file_label: str, diff_text: str) -> str:
    """The exact user-message shape sent to a specialist for one review
    call. `file_label` is the caller's own already-formatted "File: x.py"
    or "Files: a.py, b.py" line (singular vs. plural differs between this
    module's own single-file-per-call convention and
    scripts/validate_fixtures.py's multi-file fixture cases).

    Deliberately shared by both real callers (this module's
    _review_one_file and scripts/validate_fixtures.py's _run_one_case /
    _seed_placeholders) rather than each building its own copy: the
    fixture harness exists BECAUSE it's supposed to be predictive of the
    real orchestrator's behavior, which it can only be if both send
    literally the same message shape -- including _NO_TOOL_ACCESS_NOTE
    above, which is exactly the kind of detail that's easy to add in one
    place and silently forget to mirror in the other.
    """
    return (
        f"{file_label}\n\nDiff (base..working tree):\n```diff\n{diff_text}\n```"
        f"\n\n{_NO_TOOL_ACCESS_NOTE}"
    )


def _truncate_diff(diff_text: str, max_lines: int = MAX_DIFF_LINES) -> tuple[str, bool]:
    """Cuts `diff_text` to `max_lines` with an explicit marker at the cut
    point, so a specialist sees plainly that it's looking at a partial
    diff rather than silently reviewing less than it appears to. Returns
    the (possibly unchanged) text and whether truncation happened.
    """
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return diff_text, False
    omitted = len(lines) - max_lines
    marker = f"\n\n... [diff truncated, {omitted} more line(s) omitted] ...\n"
    return "\n".join(lines[:max_lines]) + marker, True


def _review_one_file(
    path: str,
    agents_for_file: list[str],
    diff_text: str,
    blob_hash: str | None,
    prompts: dict[str, AgentPrompt],
    cache: AgentCache,
    reviewer: Reviewer,
    truncated: bool = False,
) -> FileReviewResult:
    cache_key_hash = blob_hash or "deleted"

    cached = cache.get_if_fresh(path, cache_key_hash, agents_for_file)
    if cached is not None:
        parsed = findings_mod.parse("cached", cached)
        return FileReviewResult(
            path=path,
            agents=agents_for_file,
            from_cache=True,
            findings=parsed,
            truncated=truncated,
        )

    completed_agents: list[str] = []
    missing_agents: list[str] = []
    malformed_agents: list[str] = []
    raw_by_agent: dict[str, str] = {}
    parsed_all: list[findings_mod.Finding] = []
    try:
        for agent_name in agents_for_file:
            agent = prompts.get(agent_name)
            if agent is None:
                missing_agents.append(agent_name)
                continue
            user_message = build_review_user_message(f"File: {path}", diff_text)
            raw = reviewer.complete(agent.system_prompt, user_message)
            agent_findings = findings_mod.parse(agent_name, raw)
            if findings_mod.is_malformed_response(raw, agent_findings):
                # Don't add to completed_agents: a malformed response must
                # not be cached as a clean review (see the cache.put()
                # comment below), and there's nothing useful to merge into
                # parsed_all from a response that didn't match the
                # contract in the first place.
                malformed_agents.append(agent_name)
                continue
            raw_by_agent[agent_name] = raw
            completed_agents.append(agent_name)
            parsed_all.extend(agent_findings)
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
            malformed_agents=malformed_agents,
            truncated=truncated,
        )

    # Cache only the agents that actually ran AND returned a response that
    # matched the output contract. An agent whose prompt couldn't be
    # loaded, or whose response was malformed (see
    # findings_mod.is_malformed_response), was never meaningfully
    # reviewed, so it must not be recorded as having cleanly reviewed the
    # file -- that would permanently and silently hide it from every
    # future run against this same content. (Keying the cache off a
    # smaller set than what's currently routed also means the next run's
    # `get_if_fresh` naturally misses and retries the full routed set,
    # rather than staying silently stuck.)
    if completed_agents:
        combined_raw = "\n".join(raw_by_agent[a] for a in completed_agents)
        cache.put(path, cache_key_hash, completed_agents, combined_raw)

    return FileReviewResult(
        path=path,
        agents=completed_agents,
        from_cache=False,
        findings=parsed_all,
        missing_agents=missing_agents,
        malformed_agents=malformed_agents,
        truncated=truncated,
    )


def run_review(
    repo_root: Path,
    base_ref: str | None,
    prompts: dict[str, AgentPrompt],
    cache: AgentCache,
    reviewer: Reviewer,
    max_workers: int = 4,
    max_files: int | None = None,
) -> ReviewRun:
    resolved_base = git_utils.resolve_base_ref(repo_root, base_ref)
    changed = [
        c
        for c in git_utils.changed_files(repo_root, resolved_base)
        if c.status != "D"
        and not c.path.startswith(_ALWAYS_IGNORED_PREFIXES)
        and not routing.is_excluded_from_review(c.path)
    ]

    # Each changed file's diff is fetched exactly once here and threaded
    # through to _review_one_file, instead of being fetched again inside
    # it -- a duplicate git subprocess call per file that used to double
    # the git overhead of every run (and a fresh-repo/agent-init scan,
    # which diffs against the empty-tree sentinel and so treats every
    # tracked file as "added," is exactly the case where that overhead
    # scales with the whole repo, not just a PR's file count).
    routed: list[tuple[str, list[str], str, bool]] = []
    for changed_file in changed:
        diff_text = git_utils.diff_for_file(repo_root, resolved_base, changed_file.path)
        if routing.is_semantic_noise(diff_text):
            continue
        decision = routing.route_file(changed_file.path, diff_text)
        if decision.agents:
            diff_text, was_truncated = _truncate_diff(diff_text)
            routed.append((changed_file.path, decision.agents, diff_text, was_truncated))

    skipped_for_budget: list[str] = []
    if max_files is not None and len(routed) > max_files:
        # Prioritize by how many specialists routing.py's own (zero-cost,
        # already-computed) decision matched, as a proxy for how broad a
        # file's risk surface is -- a file that tripped patterns for
        # three specialists is a more informative use of a limited budget
        # than one that tripped a single narrow pattern. Ties broken by
        # path for determinism.
        routed.sort(key=lambda r: (-len(r[1]), r[0]))
        skipped_for_budget = [r[0] for r in routed[max_files:]]
        routed = routed[:max_files]

    cache.prune_missing({path for path, _, _, _ in routed})

    # One batched call instead of one `git hash-object` subprocess per
    # routed file.
    blob_hashes = git_utils.blob_hashes(repo_root, [path for path, _, _, _ in routed])

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
                    truncated=was_truncated,
                )
                for path, agents, diff_text, was_truncated in routed
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

    cache.save()

    # Suppression is applied here -- after every result is final, after
    # caching (cache.put() above already recorded the raw, unsuppressed
    # findings_text) -- never earlier. That ordering is what lets editing
    # .claude/ignore-findings.yml take effect on the very next run with
    # zero cache invalidation and zero fresh model calls: caching only
    # ever sees the specialist's real output, and this is a pure
    # presentation-layer filter on top of it.
    all_suppressed: list[suppressions_mod.SuppressedFinding] = []
    loaded_suppressions = suppressions_mod.load_suppressions(repo_root)
    if loaded_suppressions:
        filtered_results: list[FileReviewResult] = []
        for original in results:
            kept, suppressed_here = suppressions_mod.filter_findings(
                original.path, original.findings, loaded_suppressions
            )
            if suppressed_here:
                all_suppressed.extend(suppressed_here)
                filtered_results.append(dataclasses.replace(original, findings=kept))
            else:
                filtered_results.append(original)
        results = filtered_results
        suppressions_mod.log_suppressions(repo_root, all_suppressed)

    # Deterministic output order regardless of thread completion order.
    results.sort(key=lambda r: r.path)
    return ReviewRun(
        base_ref=resolved_base,
        files=results,
        skipped_for_budget=sorted(skipped_for_budget),
        suppressed=sorted(all_suppressed, key=lambda s: (s.path, s.finding.location)),
    )
