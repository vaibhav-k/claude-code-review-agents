# claude-code-review-agents

A production-grade, inference-efficient Claude Code subagent system for
automated diff review. It caps out at **8 total agents** — one triage
router plus seven razor-scoped defect-class specialists (security, data
integrity, concurrency/resources, reliability, performance, API/type
contracts, testing/maintainability) — and covers Python, Java, C#, C/C++,
JavaScript, TypeScript, SQL, and Bash/Shell without a single
language-specific agent.

The design goal: catch security, correctness, reliability, and
maintainability defects that a diff actually introduces, say nothing when
the evidence isn't there, and spend inference budget only on the specialist
domains a given change actually touches.

## How it works

```
git diff
   │
   ▼
triage-router  (haiku — reads diff metadata only, decides who else runs)
   │
   ├─▶ security-review                    (routed only if triggered)
   ├─▶ data-integrity-review              (routed only if triggered)
   ├─▶ concurrency-resource-review        (routed only if triggered)
   ├─▶ reliability-availability-review    (routed only if triggered)
   ├─▶ performance-review                 (routed only if triggered)
   ├─▶ api-type-contract-review           (routed only if triggered)
   └─▶ testing-coverage-review           (routed only if triggered)
   │
   ▼
merged findings, sorted by severity
```

A typical single-concern change (say, a pure SQL migration) triggers triage
plus one specialist — not all seven. See [`DESIGN.md`](DESIGN.md)
for the full routing matrix, every agent's exact trigger conditions, and why
the domains are grouped the way they are.

## Installation

**Option A — start from this repo:**

```
git clone https://github.com/vaibhav-k/claude-code-review-agents.git
```

Open the cloned folder in Claude Code and it's ready to use immediately —
skip to [Usage](#usage).

**Option B — add these agents to an existing project:**

1. Copy `CLAUDE.md` into the root of the repository you want reviewed (or
   merge its contents into an existing project `CLAUDE.md` — see the note
   at the top of that file if you already have one).
2. Copy `.claude/agents/*.md` and `.claude/commands/review-pr.md` into the
   corresponding `.claude/` folders of that repository.
3. Open the repository in Claude Code (VS Code extension).

No build step, no server, no container — these are plain agent definition
files that Claude Code discovers automatically.

## Usage

Run the full pipeline against the current diff:

```
/review-pr [base-branch-or-commit]
```

Defaults to diffing against `origin/main` if no argument is given. Output is
either a list of findings in the form

```
[SEVERITY] file:line — Short issue
Impact: ...
Fix: ...
```

or, if nothing clears the evidence bar, exactly:

```
No high-impact issues found.
```

You can also invoke any specialist directly by name (e.g. `@security-review`)
if you want a single domain reviewed without running triage.

## Agent roster

| Agent | Domain |
|---|---|
| `triage-router` | Reads the diff, decides which specialists run. Produces no findings. |
| `security-review` | Injection, authN/authZ, secrets, unsafe deserialization, SSRF/path traversal, crypto misuse. |
| `data-integrity-review` | Data loss/corruption and functional correctness — transactions, migrations, SQL correctness, business-logic arithmetic, and correctness risk from duplicated or dead logic. |
| `concurrency-resource-review` | Races, deadlocks, unsynchronized shared state, leaked handles/connections/memory. |
| `reliability-availability-review` | Error handling that hides failure, missing timeouts/retries, cascading-failure risk, startup/shutdown/health-check correctness. |
| `performance-review` | N+1 queries, algorithmic complexity regressions, blocking calls in non-blocking contexts, unbounded growth. |
| `api-type-contract-review` | Breaking signature/schema changes, unsafe type widenings, contract drift across language boundaries. |
| `testing-coverage-review` | Untested non-trivial new logic, tests that can't fail, weakened assertions, flaky-prone or isolation-breaking test patterns. |

Full razor-thin scope, triggers, and explicit exclusions for each agent are
in [`DESIGN.md`](DESIGN.md), Section A.

## Design principles

- **Diff-centric.** Agents look at what changed first, and read surrounding
  code only when the diff alone doesn't settle the question. Pre-existing
  issues the diff doesn't touch, worsen, or expose are out of scope.
- **Evidence over coverage.** A finding must have a concrete trigger, a
  causal link to the diff, and a specific fix, or it isn't reported. A
  missed issue beats a low-confidence guess.
- **Zero overlap.** Every agent's scope names the adjacent agent it defers
  to at each boundary (e.g. a race condition that's really an auth bypass
  is a `security-review` finding, not a `concurrency-resource-review`
  finding), so the same line of code never produces two findings.
- **Shared rules, once.** Severity model, evidence bar, output format, and
  risk ordering live in the project's `CLAUDE.md`, which Claude Code loads
  into every agent automatically — no agent file repeats them.

## Run it against any repo from the command line

The agents above only run inside a Claude Code session. `cli/` is a
separate, standalone Python package — `agent-review` and `agent-init` —
that runs the same review philosophy (same severity model, same evidence
bar, same 7 specialists) against **any** git repository, with no Claude
Code installation required:

```
cd cli
pip install -e .
export ANTHROPIC_FOUNDRY_RESOURCE=your-foundry-resource-name
export ANTHROPIC_FOUNDRY_API_KEY=...      # or ANTHROPIC_FOUNDRY_USE_ENTRA_ID=1

agent-init --path /path/to/some/repo      # one-time scaffolding
agent-review --path /path/to/some/repo    # review the current diff
```

(On Windows PowerShell, `export FOO=bar` above is bash syntax and won't
run as-is — use `$env:FOO = "bar"` instead; see
[`cli/README.md`](cli/README.md#install) for the PowerShell-specific
commands.)

It talks to Claude exclusively via Microsoft Foundry (Azure AI Foundry) —
there's no direct-to-Anthropic-API path, by design.

It also generates commit messages for your staged diff (no co-author
trailer added — that's your commit) and can run a repo's detected test
suite, proposing a fix on failure that's only ever written to disk if you
pass `--apply`. Beyond the core review loop, the CLI adds three things the
in-Claude-Code agents above don't need for themselves: a diff/file budget
so a huge changeset degrades gracefully (truncating an oversized file's
diff, skipping the tail of a file list past `--max-files`) rather than
blowing past context limits or silently doing less than it claims;
structured-output guards that detect and warn about a specialist response
that doesn't match the expected finding format instead of silently
treating it as "no finding"; and an optional `.claude/ignore-findings.yml`
so a team can mark an accepted false positive as suppressed (logged, not
silently dropped) without editing the agent's prompt. Findings can be
printed as human-readable text (the default), `--json` for programmatic
consumption, or `--sarif` for a SARIF 2.1.0 log — the format GitHub code
scanning, Azure DevOps, and most CI security dashboards expect, for
inline PR annotations and a persistent, deduplicated alerts list instead
of a build-log-only report. Every finding also carries a stable rule ID
and fingerprint, and `--baseline`/`--update-baseline`/`--new-only`/
`--fail-on SEVERITY` turn that into an actual CI policy — snapshot
today's findings once, then only fail a PR when it introduces something
*new* at or above a given severity, instead of gating on a repo's entire
existing backlog. See [`cli/README.md`](cli/README.md) for full usage,
and `DESIGN.md` Section G for the architecture — including "Honest
limitations," which now also covers what a real end-to-end run against a
live Foundry resource surfaced (and how it was fixed) beyond what the
automated test suite alone could catch.

## Development

`tests/fixtures/` holds the validation matrix from `DESIGN.md` Section E as
real source files, one folder per true-positive / false-positive-trap /
boundary case, each with an `EXPECTED.md` describing the correct verdict —
useful for sanity-checking a prompt change before opening a PR, and run
automatically on every PR by `.github/workflows/validate-agents.yml` via
`scripts/validate_fixtures.py` (see `tests/fixtures/README.md` for how the
record/replay cassette behind it works). That script prints a `[i/N]
agent/case ...` progress marker before each case and its PASS/FAIL result
the instant it finishes, rather than going silent until the whole manifest
is done — worth knowing about since `--live` mode makes a real network
call per case and can take a while. `.github/workflows/cli-ci.yml`
separately runs `cli/`'s own pytest/ruff/mypy/pyright suite, which now
includes `cli/tests/test_cli_integration_live.py` — an opt-in live/replay
integration test for the orchestrator, cassette-backed by default and
switchable to a real Foundry call with `AGENT_REVIEW_RECORD_LIVE=1`. See
`DESIGN.md`'s "CI/CD validation harness and cassette testing" for the full
design of both. A bare `pytest` run from the repo root runs `cli/tests/`
only, per the root `pytest.ini` — `tests/fixtures/` is deliberately not a
pytest suite (see that file's own comment for why).

## Documentation

- [`DESIGN.md`](DESIGN.md) — the full system design: agent
  architecture table, routing strategy matrix, the triage agent's routing
  logic, a validation matrix (true positive / false positive trap / boundary
  case per agent, with real code snippets), and the optimization rationale
  behind the 8-agent cap.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to propose changes to an
  existing agent or add a new one without breaking the 8-agent ceiling or
  introducing scope overlap.
- [`CHANGELOG.md`](CHANGELOG.md) — release history.
- [`cli/README.md`](cli/README.md) — install/usage for the standalone
  `agent-review`/`agent-init` command-line tool.

## Known limitations

- The frontmatter fields used here (`name`, `description`, `tools`,
  `model`, `color`, and argument-scoped `Bash(git diff *)`-style tool
  grants) are the conservative, well-established subset. If your Claude
  Code version supports additional fields, that's fine — nothing here
  depends on more than this subset.
- The validation matrix in `DESIGN.md` Section E has, at this point, been
  run against a real Foundry-hosted model six separate times (see
  `CHANGELOG.md` 0.9.1 through 0.9.9 and DESIGN.md's six "real `--live`
  run" write-ups), each round fixing whatever the previous round's real
  model responses actually got wrong — not just the two placeholder-vs-
  wiring checks CI runs on every PR. That history is worth reading before
  assuming a "no finding" verdict from these prompts is bulletproof: a
  live LLM's judgment on a genuinely ambiguous boundary case is
  probabilistic, not a deterministic test suite, and a few cases in that
  history needed the underlying fixture fixed (not just the prompt) after
  multiple rounds of prompt-only fixes failed to change a live model's
  verdict — see "Fourth real `--live` run" in `DESIGN.md` for why. Run
  `scripts/validate_fixtures.py --live` again in your own environment
  after changing any agent's prompt; don't assume last round's fix still
  holds without checking.
- This system reviews diffs; it does not replace human review for design
  intent, product correctness, or anything the evidence bar can't establish
  from the code itself.
- `cli/`'s automated suite mostly tests orchestration logic (routing,
  caching, git diffing) against a fake, in-memory model client, not a
  live one. `cli/tests/test_cli_integration_live.py` narrows this with a
  cassette-backed run of the real orchestrator; its committed cassette
  (`cli/tests/cassettes/integration.json`) has been recorded for real
  against a live Foundry call (2026-09-18, `AGENT_REVIEW_RECORD_LIVE=1`)
  and both tests pass against it — this is no longer an open gap. Rerun
  the recording whenever `agents_client.py`, `routing.py`,
  `orchestrator.py`, or the bundled `default_rules/` prompts change in a
  way that could plausibly change what a real model call returns; a
  prompt-only PR that forgets to also touch `default_rules/` is now
  caught automatically by `cli/tests/test_default_rules_sync.py` in CI,
  but a stale *cassette* against an otherwise-in-sync prompt still needs a
  human to notice and re-record. The CLI itself has, independently, been
  run for real by an actual user against a real Microsoft Foundry
  resource and real repositories many times over (that's what produced
  the six-round fixture history above) — see `DESIGN.md` Section G's
  "Honest limitations" for the first such run.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing a new agent or
changing an existing one's scope — the 8-agent ceiling and zero-overlap
rule are load-bearing, not stylistic preferences. Issues and pull requests
are welcome at
[github.com/vaibhav-k/claude-code-review-agents](https://github.com/vaibhav-k/claude-code-review-agents).

## License

MIT — see [`LICENSE`](LICENSE).
