# claude-code-review-agents

**Repository:** [github.com/vaibhav-k/claude-code-review-agents](https://github.com/vaibhav-k/claude-code-review-agents)

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

## Development

`tests/fixtures/` holds the validation matrix from `DESIGN.md` Section E as
real source files, one folder per true-positive / false-positive-trap /
boundary case, each with an `EXPECTED.md` describing the correct verdict —
useful for sanity-checking a prompt change before opening a PR.
`.github/workflows/validate-agents.yml` is a starting point for wiring
these into CI; it's scaffolding, not a finished pipeline (see the TODOs in
that file).

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

## Known limitations

- The frontmatter fields used here (`name`, `description`, `tools`,
  `model`, `color`, and argument-scoped `Bash(git diff *)`-style tool
  grants) are the conservative, well-established subset. If your Claude
  Code version supports additional fields, that's fine — nothing here
  depends on more than this subset.
- The validation matrix in `DESIGN.md` is a hand-written acceptance
  suite; it has not been executed against a live Claude Code install as
  part of this repository. Run it once in your own environment before
  trusting these agents in CI.
- This system reviews diffs; it does not replace human review for design
  intent, product correctness, or anything the evidence bar can't establish
  from the code itself.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing a new agent or
changing an existing one's scope — the 8-agent ceiling and zero-overlap
rule are load-bearing, not stylistic preferences. Issues and pull requests
are welcome at
[github.com/vaibhav-k/claude-code-review-agents](https://github.com/vaibhav-k/claude-code-review-agents).

## License

MIT — see [`LICENSE`](LICENSE).
