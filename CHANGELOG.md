# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-09-09

### Changed

- Split `testing-maintainability-review` into a dedicated
  `testing-coverage-review` agent, and folded its maintainability-flavored
  scope (duplicated business logic, dead/unreachable code) into
  `data-integrity-review` as a correctness-risk concern, capped at MEDIUM
  severity unless a concrete wrong-output scenario is also demonstrated.
  This keeps the total agent count at 8 while giving testing its own
  specialist.
- `testing-coverage-review` broadens the old testing scope beyond coverage
  gaps to test quality in general: flaky-prone patterns (fixed sleeps,
  unseeded randomness, order dependence) and test-isolation defects
  (shared/mutated state leaking between tests), still reasoned from the
  diff alone — no test runner or coverage tool execution.
- Updated the routing table, architecture table, and validation matrix in
  `DESIGN.md`, and the agent roster in `README.md`, to match.

### Added

- `.github/ISSUE_TEMPLATE/` and `.github/PULL_REQUEST_TEMPLATE.md`, mirroring
  the review checklist in `CONTRIBUTING.md`.
- `tests/fixtures/`: the validation matrix's True Positive / False Positive
  Trap / Boundary Case snippets as real source files with an expected-verdict
  manifest, so they can be checked by a human or a CI job instead of only
  living in prose.

## [0.1.0] — 2026-09-09

Initial release.

### Added

- `triage-router` agent: diff-metadata-only routing, emits `<review_routing>`
  XML naming which specialists to invoke and which files belong to each.
- Seven specialist review agents, organized by defect class rather than
  language: `security-review`, `data-integrity-review`,
  `concurrency-resource-review`, `reliability-availability-review`,
  `performance-review`, `api-type-contract-review`,
  `testing-maintainability-review`.
- `CLAUDE.md`: shared severity model, five-point evidence bar, risk
  priority order, output contract, and context-acquisition discipline
  applied to every agent.
- `.claude/commands/review-pr.md`: entry-point slash command wiring triage
  output to parallel specialist dispatch and severity-sorted aggregation.
- `DESIGN.md`: full architecture table, routing strategy matrix,
  validation matrix (true positive / false positive trap / boundary case
  per agent), and optimization rationale for the 8-agent ceiling.

### Coverage

Python, Java, C#, C/C++, JavaScript, TypeScript, SQL, Bash/Shell — via
embedded per-language idioms inside each specialist's own domain, not via
dedicated per-language agents.
