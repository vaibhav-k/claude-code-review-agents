# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
