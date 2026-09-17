# Contributing

This system's value comes from staying narrow. Before adding or changing
anything, check it against the four rules below — most proposed changes
that violate one of them should be rejected or redesigned, not merged with
a note.

## The 8-agent ceiling is not a soft target

There is one `triage-router` and seven specialists, and that's the whole
system. If you have a defect class that doesn't fit an existing specialist's
scope, the first move is to check whether it actually belongs inside an
existing agent's domain (most things do — see the risk-order grouping
rationale in `DESIGN.md`, Section F). Only propose a ninth agent if you
are also proposing which existing agent to retire or merge to make room, and
justify why the merge doesn't create overlap.

Do **not** add a language-specific agent under any circumstances. New
language support is a `CLAUDE.md` edit (extend the "Language Coverage"
section with that language's idioms for each existing domain), never a new
agent file.

See `DESIGN.md` Section F for the reasoning behind the current grouping
of eleven defect classes into seven specialists.

## Every change must preserve zero overlap

Each agent file has an "Explicit exclusions" section that names the
neighboring agent it defers to at every boundary. If you're changing an
agent's scope:

1. Check whether the change causes it to start reporting something a
   neighboring agent already owns. If so, add an explicit exclusion
   pointing to that agent instead of just hoping it won't come up.
2. Check the neighboring agent's exclusions for a matching cross-reference.
   A boundary is only real if both sides of it are written down.
3. Re-run (or add to) the validation matrix in `DESIGN.md` Section E —
   specifically the "False Positive Trap" case for the boundary you touched
   — and confirm the agent you changed does NOT fire on it.

## Every finding needs the same evidence bar

Don't add "nice to catch" patterns that can't clear the five-point evidence
bar in `CLAUDE.md` (path exists, trigger is plausible, causal link to the
diff, meaningful impact, specific fix). If a pattern can only be flagged
with a hedge ("this might be a problem if..."), it doesn't belong in an
agent's scope — false negatives are the acceptable failure mode here, not
speculative false positives.

## Every change should widen or narrow scope, never both at once

Keep pull requests to one agent's scope change (or one new cross-cutting
`CLAUDE.md` rule) at a time. A PR that touches three agents' scopes
simultaneously is very hard to review for overlap — split it.

## When a live `--live` run disagrees with `EXPECTED.md`

This has happened repeatedly (see `DESIGN.md`'s five "real `--live` run"
write-ups) and the same two lessons keep paying off:

- **A worked example beats more descriptive prose.** When a live model's
  response violates an existing exclusion, the fix that has reliably
  changed its verdict is a concrete `WRONG (do not report this)` /
  `RIGHT` pair anchored to the exact failing input — not a longer,
  more emphatic restatement of the same abstract rule. Several rounds in
  this project's history added increasingly specific prose to an
  exclusion and got zero change in the live model's verdict; switching
  that same exclusion to a worked example was often what actually worked.
  Watch for a backfire, though: an exclusion's own illustrative "here's
  what a BAD case would look like" vocabulary can get quoted back and
  misapplied to code that doesn't actually exhibit it — prefer stating a
  positive evidence requirement over naming bad-case examples.
- **After 2+ rounds of prompt-only fixes fail to change a verdict, check
  the fixture, not just the prompt.** More than one "stubborn" case in
  this project's history turned out to be a fixture that tripped an
  extremely strong, well-known trained prior (an `Email varchar(50)`
  column, a canonical schema anti-pattern) or that had a genuine,
  independent bug the model was correctly catching (an unclosed `Statement`
  the fixture's author hadn't noticed). In both cases, three-plus rounds
  of increasingly specific prompt text changed nothing, and the actual fix
  was to change the fixture. If a live run keeps disagreeing with
  `EXPECTED.md` on the same case after a couple of honest attempts to fix
  the prompt, read the fixture's own source again asking "is this asking
  the model to stay silent about something a careful reviewer really
  would flag" before assuming the model is wrong.

Also worth knowing: a bullet that grows across several rounds of
defensive edits doesn't just risk being ignored on its own case — in this
project's history, one heavily-edited bullet correlated with a previously
solid, *unrelated* case in the same agent starting to fail, on the theory
that the bullet's length/salience was distorting calibration elsewhere in
the same prompt. If a bullet has been edited three-plus times, consider
trimming it and moving detail into "Explicit exclusions" rather than
extending it in place again.

## Checklist for a PR that changes an agent

- [ ] Frontmatter still uses only the conservative field set (`name`,
      `description`, `tools`, `model`, `color`) — see the note at the top
      of `DESIGN.md` about why more exotic fields were deliberately
      avoided here.
- [ ] Tools remain read-only (`Read`, `Grep`, `Glob`, `Bash` scoped to
      specific `git` subcommands). No agent gets `Write`/`Edit` or
      unscoped `Bash` — this is a review system, not a code-modification
      system.
- [ ] "Explicit exclusions" updated on both sides of any boundary you moved.
- [ ] `DESIGN.md` Section A (architecture table) and Section C
      (routing matrix) updated to match, if the trigger conditions changed.
- [ ] At least one new True Positive / False Positive Trap / Boundary Case
      snippet added to Section E for any new pattern the agent now covers,
      mirrored as a real fixture under `tests/fixtures/<agent-name>/` (see
      `tests/fixtures/README.md`) with an entry in `tests/fixtures/manifest.json`.
- [ ] `triage-router.md`'s routing rule table updated if you changed what
      should route to the affected agent.
