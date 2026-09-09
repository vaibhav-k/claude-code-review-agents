## What changed and why

<!-- One or two sentences. If this changes an agent's scope, name the
     defect pattern this adds, removes, or moves to a different agent. -->

## Checklist (see CONTRIBUTING.md for details)

- [ ] Agent count is still 8, or this PR explicitly proposes which agent to
      merge/retire to stay at 8.
- [ ] No new language-specific agent was added.
- [ ] Frontmatter still uses only `name`, `description`, `tools`, `model`,
      `color` — no unverified fields.
- [ ] Tools remain read-only (`Read`, `Grep`, `Glob`, git-scoped `Bash`).
- [ ] If a scope boundary moved, both agents' "Explicit exclusions"
      sections were updated to match.
- [ ] `DESIGN.md` Section A (architecture table) and Section C (routing
      matrix) updated to match any changed trigger conditions.
- [ ] At least one True Positive / False Positive Trap / Boundary Case
      snippet added or updated in `DESIGN.md` Section E and mirrored under
      `tests/fixtures/` for any new pattern this PR adds.
- [ ] `CHANGELOG.md` updated.
