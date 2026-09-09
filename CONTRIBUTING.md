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
rationale in `docs/DESIGN.md`, Section F). Only propose a ninth agent if you
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
      snippet added to Section E for any new pattern the agent now covers.
- [ ] `triage-router.md`'s routing rule table updated if you changed what
      should route to the affected agent.
