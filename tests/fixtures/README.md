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

`.github/workflows/validate-agents.yml` runs `scripts/validate_fixtures.py`
against every case in this manifest on every PR that touches an agent
file, `CLAUDE.md`, or a fixture. It does this by reusing the standalone
CLI's own prompt-loading and model-calling machinery
(`agent_review.prompts`/`agent_review.agents_client`) rather than a real
Claude Code session, so an agent's body is what actually gets exercised as
a system prompt against each case's synthetic diff.

By default this replays a committed cassette
(`tests/fixtures/cassettes.json`) instead of calling a real model —
deterministic and free, and a cassette miss (an unrecorded case, or a
prompt/fixture that changed since the cassette was last refreshed) is a
hard CI failure, never a silent skip. It prints a `[i/N] agent/case ...`
progress marker before each case runs and that case's PASS/FAIL result the
moment it finishes, rather than staying silent until the whole manifest is
done — this matters more in `--live` mode below, where each case is a real
network call that can take a while, but applies in replay mode too. To
actually validate against a live model and refresh the cassette, run:

```bash
python scripts/validate_fixtures.py --live
```

with `ANTHROPIC_FOUNDRY_RESOURCE`/`ANTHROPIC_FOUNDRY_API_KEY` set, then
commit the updated `cassettes.json`. The same `--live` flag is available
as a manual `workflow_dispatch` job in CI (`validate-live`), which uploads
the refreshed cassette as a build artifact for a maintainer to review and
commit rather than auto-committing model output. Add `--agent
<agent-name>` to either mode to run just that agent's cases while
iterating on its prompt.

Editing any agent's `.md` body (or `CLAUDE.md`, which every agent
inherits) changes its system prompt, which changes the request-key hash
every one of that agent's cassette entries is stored under — so a prompt
edit invalidates ALL of that agent's cases in `cassettes.json` at once,
by design: a stale recording can never silently satisfy a changed prompt.
After editing a prompt, either run `--live` to re-record real responses,
or bootstrap placeholders derived from each case's own `EXPECTED.md` (NOT
live-verified, clearly labeled as such in the cassette's `_meta`, and
never overwriting an existing entry) so replay mode and CI stay green in
the interim:

```bash
python scripts/validate_fixtures.py --seed-placeholders-from-expected
```

This only proves the harness's wiring is correct end-to-end — that a real
`--live` run is still the only thing that tests whether the model's actual
judgment matches `EXPECTED.md`, not just that the harness can compute and
compare a hash correctly. `DESIGN.md`'s six "real `--live` run" write-ups
(search for "First real `--live` run" onward) are a detailed record of what
happens when the two diverge: several rounds where a live model's response
disagreed with `EXPECTED.md`, and, for a few of those, it turned out the
*fixture* needed changing rather than the agent's prompt — worth reading
before assuming every placeholder-seeded "PASS" reflects real model
behavior.
