# Validation fixtures

Each subfolder is one case from `DESIGN.md` Section E, as real source files
instead of markdown snippets. `manifest.json` lists every case with its
agent and expected verdict:

- `must_fire` — the agent should produce a `[SEVERITY] file:line` finding
  matching `EXPECTED.md` in that folder.
- `must_not_fire` — the agent should produce exactly
  `No high-impact issues found.`
- `must_not_fire_standalone` — no finding from this file alone; the
  fixture's `EXPECTED.md` explains the condition under which it would flip.

## Running these by hand

Point the relevant agent at one case folder (e.g. via `@security-review`
in Claude Code, with the case folder as the working context) and compare
its output against that case's `EXPECTED.md`. This is the fastest way to
sanity-check a change to an agent's prompt before opening a PR.

## Wiring these into CI

`.github/workflows/validate-agents.yml` is a starting point for running
these automatically. It is not a finished, self-verifying pipeline — the
exact invocation for running a specific Claude Code subagent headlessly
against a fixture depends on your Claude Code version and how you've
configured API access in this repository's secrets, so treat the workflow
file as scaffolding to adapt rather than something guaranteed to pass as-is
on first use.
