# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.11.0] — 2026-09-21

Milestone 1 of the finding-lifecycle work: turns a one-shot review into
something a CI pipeline can actually gate on across multiple runs,
without changing anything about the tool for someone who doesn't opt in.

### Added

- **Stable rule IDs** (`rules.py`, new module) -- every finding now
  carries a deterministic ID (e.g. `SEC-INJECTION-001`, or a
  domain-level fallback like `DATA-GENERAL-001`) derived from the diff
  text being reviewed, never from the model's own generated wording.
  Exposed in `--json` as `rule_id` and as SARIF's `ruleId` (see "Changed"
  below for the SARIF behavior change this implies). Deliberately a
  fresh, standalone pattern table, not a refactor of `routing.py`'s
  existing routing patterns -- see DESIGN.md for why sharing that state
  would have been risky. Known, documented limitation: one rule ID per
  file+agent, not per individual finding.
- **Stable finding fingerprints** (`fingerprint.py`, new module) -- a
  versioned sha256 (`FINGERPRINT_VERSION = "v1"`) of the rule ID, a
  normalized repo-relative path, and a normalized title. Deliberately
  excludes line number entirely (no unreliable "close enough" line-drift
  heuristic -- the project's own preference for a false NEW over an
  incorrectly-merged pair of different defects) and the model's
  free-form `impact`/`fix` prose. Explicitly **not** claimed to be a
  semantic identity -- DESIGN.md documents exactly what it can and can't
  tell apart.
- **Baseline file** (`baseline.py`, new module; default
  `.agent-review/baseline.json`) -- `--baseline PATH` reads and
  classifies this run's findings as `new`/`existing` against it and
  never writes anything; `--update-baseline` explicitly (re)writes it,
  atomically, from this run's full finding set (including currently
  suppressed findings, so a later `ignore-findings.yml` change can't
  make something look "new" again out of nowhere). A malformed or
  unsupported-version baseline always errors loudly, including under
  `--update-baseline` -- it is never silently treated as empty.
- **`--new-only`** -- with a baseline, narrows the displayed/evaluated
  finding set to just the `new`-classified ones. With no baseline, every
  finding is already `new`, so this is a defined no-op.
- **`--fail-on SEVERITY[,SEVERITY...]`** -- exits non-zero if any
  finding in the (baseline/`--new-only`-filtered) display set is at or
  above the given severity, reusing the existing four-tier severity
  model. Independent of, and additive to, the pre-existing
  `--fail-on-findings` flag, which keeps its exact original "fail if
  anything was found at all" meaning.
- `cli/tests/test_rules.py`, `test_fingerprint.py`, `test_baseline.py`,
  and `test_cli_baseline_integration.py` -- unit and end-to-end coverage
  for all of the above, including the spec's own worked example
  (`--baseline ... --new-only --fail-on high` fails only on a genuinely
  new HIGH+ finding, not a pre-existing one).

### Changed

- **SARIF `ruleId` now comes from the new rule-ID system, not
  `finding.agent`.** Previously each SARIF rule was one specialist agent;
  now it's the finding's rule ID (e.g. `SEC-INJECTION-001`), with the
  specialist's identity preserved as `properties.agent` exactly as
  before. This is the one user-visible behavior change in this release,
  and it's spec-mandated -- SARIF's `ruleId` is meant to be a specific,
  stable identifier for the kind of defect, which the new rule IDs
  finally provide.
- `to_sarif()` gained two new optional, default-off parameters
  (`baseline`, `new_only`) rather than a second SARIF writer; each result
  gains SARIF's native `baselineState` (`new`/`unchanged`) only when a
  baseline was supplied.
- `--json` output is purely additive: every finding gains `rule_id` and
  `fingerprint`; `status` appears only when `--baseline` was used.
- `Finding` gained a `rule_id: str = ""` field, defaulted so every
  existing direct-construction call site (test fixtures included) keeps
  working unchanged.

### Fixed

- **A real, previously latent cache-attribution bug.** `.agent-cache/`
  stored every routed agent's response text for a file merged into one
  string, replayed on a cache hit under the placeholder agent name
  `"cached"` -- harmless while nothing depended on the real agent name,
  but once rule IDs (and therefore fingerprints) started depending on
  it, a cache-hit replay of the identical finding got a *different* rule
  ID than the original live call did, for the single most common
  repeat-run scenario. Caught by a real failing test written for this
  milestone, not by inspection. Fixed by storing `dict[agent, raw_text]`
  per file instead of one merged blob (`CACHE_SCHEMA_VERSION` 1 → 2 -- an
  old version-1 manifest is simply treated as empty and repopulated, the
  same no-migration mechanism this cache has always used for a schema
  bump).
- **Two super-linear regexes, caught by review of this milestone's own
  new/touched code.** `findings._HEADER_RE`'s unbounded `\S+`
  (`location`) and three unbounded `\s*` runs could be forced into O(n)
  backtracking by a single malformed line with a long non-whitespace run
  and no `-`/`—` anywhere in it. `fingerprint._TRAILING_PUNCT_RE`
  (`r"[.\s]+$"`, used by `normalize_title`) was worse: run via `.sub()`
  against a string that doesn't end in a match, the engine retries the
  same greedy-then-backtrack dance from every dot/whitespace position,
  not just the true suffix -- O(n²) overall for an adversarial input like
  a long run of dots followed by one non-dot character. Fixed by bounding
  every quantifier in `_HEADER_RE` except `title` (which needs none --
  it's the last group before the end anchor, so it always resolves in
  one pass) to a small constant (`\s{0,4}`, `\S{1,300}`) with zero
  behavior change for any realistic input, and by replacing
  `_TRAILING_PUNCT_RE` with a plain right-to-left character scan
  (`fingerprint._rstrip_dots_and_space`) that inspects each character at
  most once, with no backtracking possible even in principle. Both
  verified directly: a 200,000-character adversarial input for each now
  resolves in well under a second (regression tests in `test_findings.py`
  and `test_fingerprint.py` pin the boundary and the fast-fail time).
- **`orchestrator.run_review`'s Cognitive Complexity (18) exceeded this
  project's linter threshold (15).** Split into four single-purpose
  helpers (`_route_changed_files`, `_apply_file_budget`,
  `_run_routed_reviews`, `_apply_suppressions`), each taking over one
  loop/branch that was previously nested inside `run_review` itself,
  which is now a flat, linear sequence of calls with no branching of its
  own. Pure refactor -- behavior is unchanged and fully covered by the
  existing test suite (verified: no test needed to change).
- **A real cache-vs-live ordering mismatch, caught by a genuine CI
  failure (GitHub Actions, Python 3.10/3.11/3.12), not by inspection.**
  `test_second_run_is_a_pure_cache_hit_with_no_further_reviewer_calls`
  failed because a cache-hit replay's two findings (same severity, same
  location, from two different specialists) came back in the OPPOSITE
  order from the live call that originally produced them, even though
  both runs found exactly the same two findings. Root cause:
  `findings.sort_findings`'s key was only `(severity, location)` --
  two findings that tie on both fall back to Python's stable-sort
  behavior of preserving whatever order they arrived in, which is not
  itself guaranteed consistent across this project's own call sites. The
  live path builds its list in `agents_for_file`'s routing order; a
  cache-hit replay iterates `cache.get_if_fresh()`'s `per_agent` dict,
  whose key order survives a save/load round trip through
  `json.dumps(..., sort_keys=True)` alphabetically (`cache.py`'s
  `save()`) -- a real, reproducible reordering (confirmed directly: a
  dict inserted as `{"security-review": ..., "performance-review":
  ...}` comes back as `{"performance-review": ..., "security-review":
  ...}` after one save/load cycle), not a flake. This was only ever
  latent in schema version 1 too -- it just never had a `per_agent` dict
  to reorder, since v1 stored one merged string per file instead. Fixed
  at the actual source: `sort_findings`'s key is now `(severity,
  location, title, agent)`, fully specified so two findings sort
  identically regardless of what order they arrived in -- verified with
  a test that sorts the same two tied findings in both possible input
  orders and asserts identical output either way, and confirmed against
  the exact cache round trip that surfaced this.

### Notes

- `python scripts/validate_fixtures.py` was run as part of this
  milestone's own validation and reported 0/23 in the working tree at
  the time; `cli/tests/test_cli_integration_live.py`'s two replay tests
  also failed. Both confirmed, via a disposable clean checkout of the
  prior commit, to be entirely caused by already-uncommitted, unrelated
  edits (most notably an in-progress `CLAUDE.md` / `default_rules/`
  change) that predate this milestone and are outside its scope to fix
  -- both pass cleanly (23/23, and 2/2) at that clean checkout. See
  DESIGN.md's "Finding lifecycle and CI policy" section for the full
  root-cause writeup. Not a regression introduced by this release.

## [0.10.3] — 2026-09-21

GitHub Actions ran `cli-ci.yml` on the pushed 0.10.0–0.10.2 batch across
Python 3.10/3.11/3.12 and reported 3 failures, all real (not flaky, not
environment-specific -- reproduced locally too).

### Fixed

- **`default_rules/agents/security-review.md` had drifted from
  `.claude/agents/security-review.md`.** The root file had picked up an
  "Always include" / "Do not include" output-formatting block (an
  independent edit to that agent's prompt, not part of this session's own
  changes) that was never mirrored into the CLI's bundled snapshot --
  exactly the silent-drift failure mode `test_default_rules_sync.py`
  exists to catch, and it caught it. Fixed by copying the root file over
  the bundled one, per that test's own instruction.
- **`test_requirements_sync.py`'s comparison was too strict and broke on
  a legitimate practice.** The actual committed `requirements.txt` /
  `requirements-dev.txt` pin exact versions (`anthropic==1.4.0`,
  `pytest==9.1.1`, etc.) rather than repeating `pyproject.toml`'s own
  looser `>=` bounds verbatim -- a normal, reasonable pattern (a
  requirements file as a reproducible-install lockfile, distinct from
  the installable package's own compatible-range dependencies) that this
  test's first draft never accounted for, having been written and
  verified against a version of these files that happened to still use
  loose bounds. Rewrote the comparison to check what actually matters:
  the same package names appear in both files, environment markers
  match, and any version constraint in the requirements file is
  compatible with `pyproject.toml`'s (an exact pin must satisfy
  `pyproject.toml`'s specifier, checked with the `packaging` library --
  already a guaranteed transitive dependency of `pytest`, so no new
  dependency was added). Still catches the original bug this test was
  built for -- a forgotten addition or removal shows up as a package-name
  mismatch -- without breaking every time a lockfile gets re-pinned to a
  newer patch release.

### Known follow-up this fix causes (not yet done -- needs real Foundry credentials this session doesn't have)

Syncing `security-review.md`'s bundled copy changes that agent's system
prompt hash, which invalidates every cassette entry recorded against the
old prompt -- by design (see `tests/fixtures/README.md` and this
project's whole cassette-testing philosophy: a stale recording must never
silently satisfy a changed prompt). Confirmed directly, not assumed:
`python scripts/validate_fixtures.py --agent security-review` now reports
all 3 cases (`true_positive`, `false_positive_trap`, `boundary_case`) as
cassette misses against `tests/fixtures/cassettes.json`, and
`cli/tests/test_cli_integration_live.py`'s two tests fail the same way
against `cli/tests/cassettes/integration.json` (its `handlers.py` case
routes to `security-review` + `performance-review`). Both need a real
`--live` re-record before `.github/workflows/validate-agents.yml` and
`cli-ci.yml` are fully green again:

```bash
python scripts/validate_fixtures.py --live
AGENT_REVIEW_RECORD_LIVE=1 pytest cli/tests/test_cli_integration_live.py
```

then commit both refreshed cassette files. This can't be done from this
session (no real `ANTHROPIC_FOUNDRY_*` credentials here) -- it needs to
happen on a machine with real Foundry access, per this project's
established process for exactly this situation.

## [0.10.2] — 2026-09-21

### Changed

- **Refactored `cli._print_review()`** to bring its Cognitive Complexity
  (SonarQube/SonarLint) down from 20 to the project's allowed 15 -- pure
  refactor, no behavior change. The function was seven independent
  "if there's something to report, print a header, loop over it" blocks
  in a row; each `if x: ... for ... in x: ...` pairing nests a loop
  inside a branch, which Cognitive Complexity penalizes hardest (a nested
  loop costs its base score plus one per enclosing branch/loop), even
  though the seven blocks never interact. Split each block into its own
  helper (`_print_failed_files`, `_print_missing_agents`,
  `_print_malformed_agents`, `_print_truncated_files`,
  `_print_skipped_for_budget`, `_print_suppressed`, `_print_findings`)
  using a guard-clause early return (`if not x: return`) instead of a
  positive `if x:` wrapping the rest of the body -- that turns "loop
  nested inside a branch" into "loop at the same level as a guard
  clause," collapsing each helper to a complexity of 2 and
  `_print_review` itself to 0 (a plain sequence of calls, no branching of
  its own left). Verified two ways: the existing capsys-based CLI
  integration tests (which assert on `_print_review`'s exact stdout)
  pass unchanged, confirming byte-for-byte identical output; and the
  `cognitive_complexity` package's `get_cognitive_complexity()` was run
  directly against both the original AST (reproduced the reported score
  of 20 exactly, confirming the tool models the same metric SonarQube
  reported) and the refactored one (0 / 2 across the board) -- not just
  eyeballing the shape of the fix and assuming it worked.

## [0.10.1] — 2026-09-21

0.10.0 itself introduced a small instance of the exact class of drift
this project has repeatedly had to catch elsewhere (bundled prompt
copies, stale doc claims): adding `jsonschema` to
`[project.optional-dependencies].dev` in `pyproject.toml` (for the new
SARIF schema-validation test) without also adding it to
`requirements-dev.txt`, which both files' own header comments say to
keep in sync with `pyproject.toml` -- and nothing was actually enforcing
that. Caught while responding to a direct request to reconcile the two
files, not through any automated check that already existed.

### Fixed

- `requirements-dev.txt` was missing `jsonschema>=4.0` (present in
  `pyproject.toml`'s `dev` extra since 0.10.0). Added, with the same
  explanatory comment as the `pyproject.toml` copy.
- `requirements.txt` was already correct — no change needed there.

### Added

- **`test_requirements_sync.py`**, so this can't silently drift again.
  Parses `pyproject.toml` (via `tomllib` on 3.11+, the `tomli` backport
  on 3.10 -- a new conditional dev dependency,
  `tomli>=2.0; python_version < "3.11"`) and asserts `requirements.txt`'s
  and `requirements-dev.txt`'s active (non-comment) lines are exactly the
  set of strings in `[project.dependencies]` and
  `[project.optional-dependencies].dev` respectively. This is the same
  fix shape `test_default_rules_sync.py` already applies to prompt
  content drifting out of sync with its bundled copy: replace a comment
  asking a future contributor to remember with a test that fails loudly
  the moment they forget. Verified the test actually catches drift (not
  just passes vacuously) by deliberately deleting the `jsonschema` line
  from `requirements-dev.txt` and confirming the test fails with a clear
  diff, then restoring it — and verified the `tomli` fallback path for
  real, not just by code inspection, by installing this package under an
  actual Python 3.10 interpreter and running the full suite there (176
  passed; 4 unrelated pre-existing failures in that one interpreter's
  `test_agents_client.py` Entra ID tests trace to a missing
  `_cffi_backend` native module for that specific ad-hoc install, not to
  anything this change touches).

## [0.10.0] — 2026-09-21

With 0.9.16's flakiness-reduction path confirmed a dead end at the API
level (no code-level lever exists for it in the installed SDK), building
on top of the current, already-disciplined prompt/evidence-bar baseline
was the agreed next step rather than waiting on a fix that isn't coming.
This is that feature: SARIF output, so a CI pipeline gets inline PR
annotations and a persistent alerts list (GitHub code scanning, Azure
DevOps, and most CI security dashboards all consume SARIF) instead of
only a build-log-only report.

### Added

- **`--sarif` flag** (`review` subcommand), mutually exclusive with
  `--json`. Renders the same `ReviewRun` as a SARIF 2.1.0 log via the new
  `sarif.py` module — a peer to the existing `_review_run_to_dict()`
  (`--json`)'s rendering, not a replacement for it: same input, different
  consumer-facing shape. Each specialist agent becomes a SARIF rule
  (`security-review`, `data-integrity-review`, etc., with a
  `shortDescription` kept in sync with the README's agent-roster table by
  hand); each finding becomes a result with `level` derived from severity
  (`CRITICAL`/`HIGH` → `error`, `MEDIUM` → `warning`, `LOW` → `note`,
  with the original four-way severity preserved unchanged in
  `properties.severity` since SARIF's four levels can't otherwise tell
  CRITICAL and HIGH apart); and `location`'s `file:line` is split into a
  SARIF `physicalLocation` (a location with no `:line` suffix — e.g. a
  documentation-only finding — degrades to an artifact-only location with
  no `region`, rather than crashing on a shape that isn't supposed to
  occur but is defensively handled anyway).
- **Stable finding fingerprints.** Each result carries a
  `partialFingerprints.agentReview/v1` hash of `agent:path:title`
  (deliberately excluding the model's free-form impact/fix prose,
  which can be worded slightly differently between runs for the same
  underlying issue) — this is what lets GitHub code scanning and similar
  SARIF consumers recognize a finding as already-seen across runs even
  after an unrelated earlier edit shifts its line number, instead of
  re-flagging it as new every time.
- **Non-finding signals travel too.** Everything `_print_review()` /
  `--json` already surface outside the findings list — a failed file, a
  specialist skipped for a missing/malformed prompt response, a
  truncated diff, a file dropped by `--max-files`, a suppressed finding —
  is rendered as a SARIF `toolExecutionNotification` (`error` for a
  failed file, which also flips `invocations[0].executionSuccessful` to
  `false`; `warning` for missing/malformed agents; `note` for the rest),
  so a SARIF-only consumer that never sees this tool's stdout doesn't
  silently lose visibility into any of it.
- New dev-only dependency: `jsonschema` (only imported by
  `test_sarif_schema_validation.py`, via `pytest.importorskip` so its
  absence skips one test rather than failing collection). Backs a new
  test that validates a generated SARIF log against the real, official
  SARIF 2.1.0 schema — bundled locally at
  `cli/tests/support/sarif-schema-2.1.0.json` (same record/replay
  reasoning as `tests/support/cassette.py` one directory over: this must
  stay a deterministic, network-free check on every PR, not a live fetch
  that can flake or silently start validating against a moved/absent
  URL).

### Fixed (caught before shipping, not after, this time)

- The SARIF schema URL this feature's own code originally hardcoded
  (`.../sarif-spec/master/Schemata/sarif-schema-2.1.0.json`, the path
  most third-party examples use) 404s — OASIS moved the file under
  `sarif-2.1/schema/` at some point after that path was first written up
  elsewhere. Caught by the same discipline 0.9.16's postmortem named as
  the fix going forward: actually validate against the real thing before
  shipping, not just write code that looks right. A hand-built SARIF log
  covering every rendering branch (multiple severities, a
  no-line-number finding, a failed file, missing/malformed agents, a
  truncated file, a budget-skipped file, a suppressed finding, and a
  cache-replayed finding) was run through `jsonschema.validate()` against
  the corrected URL's schema before this shipped, and that check is now
  a permanent, offline regression test rather than a one-time manual
  step.

## [0.9.16] — 2026-09-21

Reverted 0.9.15 within the hour: the very first `--live` run after it
shipped crashed every single case with
`TypeError: Messages.create() got an unexpected keyword argument
'temperature'`. The fix's whole premise — pin `temperature` to cut
sampling variance — was never checked against the actually-installed
`anthropic` SDK before shipping. It should have been.

### Fixed

- Confirmed directly (`inspect.signature()` on the installed SDK's
  `Messages.create`, then grepping the entire installed `anthropic`
  package for the string `"temperature"` and finding zero references
  anywhere) that this API generation has no `temperature`, `top_p`, or
  `top_k` parameter at all — not a version mismatch on this project's
  side, a genuine capability absent from the SDK/API surface that serves
  `claude-sonnet-5`-generation models. The closest thing,
  `output_config={"effort": ...}` (`low`/`medium`/`high`/`xhigh`/`max`),
  is a reasoning-effort dial, not a sampling-determinism control, and
  isn't a substitute. Removed `TEMPERATURE` and the `temperature=` kwarg
  entirely rather than trying a different value or parameter name — there
  is currently no lever in this codebase for the thing 0.9.15 was trying
  to control, and pretending otherwise with a differently-named guess
  would repeat the same mistake once more.

### Lesson

0.9.15 was reasoned from the project's own documented flakiness history
and from reading the calling code — genuinely well-motivated — but never
executed against a real call before being presented as a fix, which is
exactly the verification step this project's own established discipline
(going back to the sixth `--live` run's CRLF correction) exists to catch.
A one-line smoke test — construct the real client, or even just
`inspect.signature()` the method being called — would have caught this
before it shipped instead of after. The flakiness problem this was meant
to address is still real and still open; there's currently no known fix
for it beyond continuing the prompt/evidence-bar discipline already in
use throughout this project's `--live` tuning history.

## [0.9.15] — 2026-09-21

Pinned `temperature=0.0` on every live Foundry call
(`agents_client.py`'s `AnthropicFoundryReviewer.complete()`), which had
never set it at all — every review call ran at the API's default
(full-sampling) temperature. This is a code fix, not a prompt fix, made
after reviewing this project's own multi-round `--live` tuning history:
several of the hardest-to-diagnose "is this a real prompt gap or just
noise" investigations (the four distinct reframings of
`testing-coverage-review/false_positive_trap`, `data-integrity-review/
true_positive`'s n=2 miss against an unchanged prompt) are exactly the
symptom an unpinned temperature produces — the same request producing a
different verdict, or a different *framing* of the same verdict, across
otherwise-identical calls. This doesn't replace the evidence-bar/prompt
discipline used throughout this project, but it should lower how often
that discipline is needed for pure sampling noise rather than a real gap.

### Changed

- `agents_client.py`: added a `TEMPERATURE = 0.0` module constant
  (alongside the existing `MAX_TOKENS`) and passed it to every
  `messages.create()` call. Not exposed as a CLI flag or env var — there's
  no legitimate reason a code-review verdict should want more sampling
  variety, so this isn't a setting a user could accidentally loosen.

### Important caveat for what happens next

`request_key()` hashes only `system_prompt + user_message` — it has no
way to know a non-prompt parameter like temperature changed, so every
existing cassette entry in `tests/fixtures/cassettes.json` and
`cli/tests/cassettes/integration.json` still matches its cache key and
will keep replaying as "valid" even though it was recorded at the old,
unpinned temperature. Unlike a prompt edit, this change does NOT force a
re-recording automatically. A full fresh `--live` run (ideally two or
three consecutive full runs, not just one) is required to actually
generate temperature=0 data and see whether it measurably reduces the
run-to-run flakiness this session spent so much effort diagnosing —
watch specifically whether `data-integrity-review/true_positive` and
`testing-coverage-review/false_positive_trap`, the two cases with the
most flip-flopping history, now hold steady across repeated runs.

## [0.9.14] — 2026-09-18

`data-integrity-review --live` came back 4/4 clean, confirming 0.9.13's
fix live for real. Attempting the still-outstanding
`cli/tests/test_cli_integration_live.py` recording surfaced a real, if
smaller, DX gap of its own: running it plain (no env vars) failed against
the stale placeholder cassette as expected, but running it with
`AGENT_REVIEW_RECORD_LIVE=1` and `ANTHROPIC_FOUNDRY_MODEL` set still
failed with `RuntimeError: No Microsoft Foundry resource configured`,
even though a working `cli/.env` already exists — `cli/tests/conftest.py`'s
`_no_real_dotenv_lookup` autouse fixture disables `.env` loading for
every test in the suite, including this file's opt-in recording path,
which is the one place that actually wants real credentials loaded.

### Fixed

- `cli/tests/test_cli_integration_live.py` now captures a direct
  reference to the real, unpatched `_load_dotenv_if_present` at module
  import time (before `conftest.py`'s autouse fixture ever runs, same
  technique `test_cli_dotenv.py` already uses) and calls it explicitly at
  the top of `reviewer_and_cassette`'s recording branch, before
  constructing `AnthropicFoundryReviewer()`. Ordinary replay-mode tests in
  this file and the rest of the suite are unaffected — the autouse
  fixture still blocks `.env` loading everywhere else, correctly. This
  doesn't override anything already exported by hand
  (`_load_dotenv_if_present` itself never overrides a real environment
  variable) — it just means a working `cli/.env` is enough on its own,
  the same as it already is for `scripts/validate_fixtures.py --live`.
  **Confirmed live** — `cli/tests/cassettes/integration.json` was
  re-recorded for real against the current bundled prompts (both tests
  pass, `_meta`'s placeholder note is cleared, and a spot-check for the
  known "wrapped in a markdown code fence" failure mode found none in the
  currently-matched entries — only in older, no-longer-reachable hash keys
  left over from a previous round, harmless dead weight rather than a live
  bug). This closes out the entire 0.9.10 → 0.9.14 arc — the bundled
  defaults sync, the discriminating-power Evidence Bar lift, the three
  exclusion trims/cross-references, the billing.py/handlers.py
  canonicalization, the narrowing-vs-brand-new-table fix, and this
  dotenv fix — as one fully live-verified state.

## [0.9.13] — 2026-09-18

`data-integrity-review/true_positive` (the `Email varchar(255)`→`varchar(50)`
narrowing migration, zero prior failure history across every earlier
`--live` round) missed twice in a row against the exact prompt shipped in
0.9.12, confirmed via request-key hash comparison against a real second
opinion from the live model rather than assumed from the terminal summary
alone. **Two of the three "failures" seen while diagnosing this were a red
herring**: the `.claude/agents/*.md` protected-path delivery mechanism (a
file download the user places by hand, since it can't be written
remotely) hadn't been applied yet, so the first two live runs were
actually testing 0.9.12's `CLAUDE.md` paired with the PRE-0.9.12
`data-integrity-review.md` — a combination that never shipped and was
never meant to be tested. Verified this by computing the exact
`request_key()` hash for all four new/old×CLAUDE.md/agent-file
combinations and matching each against the cassette's recorded miss
before concluding anything, rather than trusting the coincidence.

### Fixed

- Once the correct file was actually in place, the miss recurred (real
  n=2 against the genuine 0.9.12 prompt). Root cause: `data-integrity-
  review.md`'s brand-new-table exclusion ("only a write statement inside
  THIS diff proves the width/constraint is actually violated") sits
  right next to `CLAUDE.md`'s new, more cautious Evidence Bar, and the
  model appears to have generalized that brand-new-table-only evidentiary
  bar to the narrowing-an-EXISTING-table case too — where it's backwards:
  an existing table is presumed to already hold data of unknown
  length/shape, so "some existing row may not fit" is the risk being
  reported, not a hypothetical needing its own proof. Added an explicit
  contrast directly after the brand-new-table WRONG/RIGHT example: a
  second RIGHT example, using this exact fixture's diff, showing the
  finding IS reportable with no write statement present, because the
  table already exists. Synced the bundled `default_rules/` copy in the
  same commit.
  **Confirmed live** — `--agent data-integrity-review --live` came back
  4/4, including the true_positive case firing correctly. This closes out
  0.9.12's full change: the discriminating-power Evidence Bar lift, the
  three exclusion trims/cross-references, the billing.py/handlers.py
  canonicalization, and this fix are all now live-verified as one
  confirmed state.

## [0.9.12] — 2026-09-18

Lifted a recurring cross-agent pattern into `CLAUDE.md` as a new, sixth
Evidence Bar requirement, and permanently ended the recurring
`billing.py`/`handlers.py` blank-line drift instead of continuing to fight
a local formatter every session. **This touches every agent's request-key
hash (CLAUDE.md is inherited by all 8) — a full `--live` validation matrix
run is required before treating any of this as confirmed, including
re-recording `cli/tests/cassettes/integration.json` a second time (the
run that just passed was against the pre-this-change prompt).**

### Added

- `CLAUDE.md`'s Evidence Bar gained a sixth requirement, **discriminating
  power**: a finding claiming a gap, omission, or a second instance of an
  already-covered pattern is only reportable if the additional case can
  actually reach a different code path or produce a different outcome than
  a case already covered. This generalizes 0.9.11's testing-coverage-review
  fix (a redundant boundary-adjacent test demanded under a different
  customer attribute) into a shared, project-wide principle, since the same
  underlying shape already existed independently in
  `concurrency-resource-review.md`'s "no third code path for a one-field
  class" exclusion. See `DESIGN.md`'s write-up for the full reasoning.

### Changed

- `data-integrity-review.md`'s "already wrong before this diff" exclusion
  was a near-verbatim restatement of `CLAUDE.md`'s existing Evidence Bar
  causal-link requirement — trimmed to a one-line cross-reference.
  `performance-review.md` and `concurrency-resource-review.md` had the same
  near-duplicate wording but with real domain-specific elaboration worth
  keeping, so those kept their text and gained a cross-reference instead of
  a trim. `testing-coverage-review.md`'s boundary-adjacent exclusion and
  `concurrency-resource-review.md`'s one-field-class exclusion each gained
  a closing sentence naming the new Evidence Bar item they're now the
  canonical worked examples of. All bundled `default_rules/` copies synced
  in the same commit.
- `tests/fixtures/data-integrity-review/true_positive_structural/billing.py`
  and `tests/fixtures/security-review/boundary_case/handlers.py` adopted
  the 2-blank-line PEP 8 form as their canonical committed content, instead
  of the 1-blank-line form some local formatter kept overwriting it to on
  every save (see 0.9.9's investigation). Fighting the formatter every
  session was costing more than just matching its output once. Updated
  `billing.py`'s `EXPECTED.md` line reference (`:8` → `:9`, one line added
  before the flagged `if`) and the matching worked examples in
  `data-integrity-review.md` and `DESIGN.md`'s validation matrix.
  `handlers.py`'s `EXPECTED.md` has no line reference, so it needed no
  change.

## [0.9.11] — 2026-09-18

A fresh, unprompted full `--live` run (22/23) caught `testing-coverage-review/
false_positive_trap` failing for the first time since round 5, with a fourth
distinct complaint angle against the same fixture: `[MEDIUM] test_discounts.py:1
— Untested non-VIP discount branch at boundary condition`, demanding a test
for a non-VIP customer specifically at `total=500`.

### Fixed

- `.claude/agents/testing-coverage-review.md`'s boundary-adjacent-test
  exclusion (added round 4, extended round 5) was written narrowly in terms
  of a second boundary-adjacent *value* (e.g. `499`) and a "wrong branch"
  recharacterization — it didn't cover a third reframing: demanding a second
  boundary-adjacent *attribute combination* (a non-VIP customer) at the same
  already-tested boundary value. Verified this has zero discriminating power
  before treating it as a real gap: once `total > 500` is `False`, `is_vip`
  cannot change which branch is taken, so `(is_vip=False, total=500)` hits
  the identical `else` branch and asserts the identical `25` that
  `(is_vip=True, total=500)` already proves — the same underlying "redundant
  boundary-adjacent test demand" as rounds 3–5, just walked sideways from the
  value axis to an unrelated-attribute axis the existing wording didn't
  cover. Generalized the exclusion's wording to "any input dimension that
  doesn't change the branch taken" and added a fourth explicit WRONG example
  naming this exact reframing. Synced the bundled
  `cli/src/agent_review/default_rules/agents/testing-coverage-review.md`
  copy in the same commit (see `test_default_rules_sync.py`, 0.9.10).
  **Confirmed live** — an `--agent testing-coverage-review --live` rerun
  passed against real model output, not just the hand-reasoned theory
  above. See `DESIGN.md`'s "Fourth reframing of the false_positive_trap
  boundary complaint, closed" write-up for the full investigation.

## [0.9.10] — 2026-09-18

Recording `cli/tests/cassettes/integration.json` live for the first time
(closing the last explicitly-documented "still-open gap" from 0.9.8's
README update) surfaced something much bigger than that gap itself: the
CLI's bundled default prompts had silently drifted from the real,
live-tuned ones.

### Fixed

- **`cli/src/agent_review/default_rules/`** (the prompts a target repo
  gets when it has neither `.agent-rules/` nor `.claude/` of its own) had
  not been updated since some point before this project's six-round
  `--live` tuning history — all 7 bundled specialist prompts and the
  bundled `CLAUDE.md` had drifted from the real ones at the repo root.
  Concretely: the bundled `CLAUDE.md` was missing the entire "never wrap
  your response in a markdown code fence" output-contract rule, which is
  exactly why the first live-recording attempt at `cli/tests/cassettes/
  integration.json` came back with a response wrapped in ` ``` ` — not a
  fresh model quirk, a real, previously-shipped gap between what this
  project's own validation actually tests and what a real CLI user with
  no `.claude/` of their own actually gets. Synced every bundled file to
  its root counterpart and added `cli/tests/test_default_rules_sync.py`,
  which fails loudly and names the exact file the moment a root prompt
  changes without its bundled copy following — the missing piece that let
  this drift accumulate silently across six rounds. `.claude/agents/**`
  and root `CLAUDE.md` are now also trigger paths for `cli-ci.yml`, so a
  prompt-only edit (touching nothing else under `cli/`) still runs this
  check instead of silently skipping it.
- `cli/tests/test_cli_integration_live.py`'s recording fixture now clears
  the cassette's stale `_meta` "not live-recorded" note after a recording
  run, same bug and same fix as `scripts/validate_fixtures.py`'s `run()`
  from 0.9.9.

### Changed

- `cli/tests/cassettes/integration.json` re-seeded with hand-authored
  placeholders (clearly labeled in `_meta`) for the 4 request keys the
  corrected bundled prompts now produce — the previous entries recorded
  against the stale prompt are no longer reachable. **Not yet
  live-verified against the corrected prompt** — a further
  `AGENT_REVIEW_RECORD_LIVE=1 pytest cli/tests/test_cli_integration_live.py`
  run is what actually confirms these two tests pass against genuine model
  output rather than a hand-authored guess, and is the natural next step.

No agent prompt content changed in this release beyond the sync itself —
this closes a distribution gap, not a review-quality one.

## [0.9.9] — 2026-09-17/18

The sixth real `--live` run reported the first fully clean 23/23 — but
independently replaying that cassette in a second checkout caught a real
bug the celebration would otherwise have papered over, plus a couple of
smaller ones found while verifying the fix before committing it. No agent
prompt changed in this release; the worked examples added through 0.9.7
are untouched, per the standing instruction to hold off on any prompt
trimming until a live run is confirmed to actually replay cleanly
everywhere, not just where it was recorded.

### Fixed

- Two fixtures (`data-integrity-review/true_positive_structural/billing.py`,
  `security-review/boundary_case/handlers.py`) had each independently
  picked up one extra blank line before a top-level `def` — real content
  drift, not line-ending noise, almost certainly an editor's on-save
  formatter applying PEP 8's two-blank-line convention the moment either
  file was opened locally (`git log --follow` shows neither touched since
  its original commit). That drift is what actually broke cassette replay
  across checkouts — restored both to their originally committed,
  single-blank-line content. It recurred once more while re-verifying
  before handing off a commit-message list, confirming this is a
  recurring local-formatter effect, not a one-off; restored again. See
  `DESIGN.md`'s sixth real `--live` run write-up for the full
  investigation, including a CRLF theory that looked plausible, got a
  code fix shipped on its strength, and was then directly disproved by
  testing before being written up as confirmed — `git diff --no-index`
  turns out to already respect `core.autocrlf` for its blob-hash line, and
  `subprocess.run(..., text=True)` already normalizes any raw `\r\n` in a
  diff's body independent of git. That fix was reverted as a no-op with
  the corrected explanation left in the code so it isn't silently
  re-attempted later.
- `ruff.toml`'s `extend-exclude` for `tests/fixtures/` (deliberately
  incomplete snippets like `db.execute(...)` with no `db` defined
  anywhere, not meant to pass static analysis) only applies when ruff
  discovers files by walking a directory — it's silently bypassed when a
  path is passed explicitly, which is exactly what an editor's ruff
  extension does when linting "the current file." Reported as a
  false-positive `db is not defined` on `security-review/boundary_case/
  handlers.py`, which a repo-wide `ruff check .` already correctly
  skipped. Added `force-exclude = true` so the exclusion applies either
  way; verified against both the explicit-path and repo-wide cases.
- A synced copy of `scripts/validate_fixtures.py` was found missing every
  `# noqa: E402` comment on its post-`sys.path.insert` imports — would
  have failed `ruff check` with 6 real errors. Almost certainly an
  import-sorting tool (isort, or an editor's "organize imports" on save)
  hoisting those imports and dropping their trailing comments in the
  process. Added `# isort:skip_file` as a guard.
- `run()`'s `--live` branch now clears a stale "seeded from EXPECTED.md,
  not live-recorded" cassette `_meta` note on a full (unfiltered) run —
  previously it was set once by the first `--seed-placeholders-from-expected`
  bootstrap and never cleared afterward, so it kept claiming
  placeholder-only data even after this round's fully-live 82-entry
  recording. A `--agent`-scoped run still leaves it in place, since that
  run doesn't cover the whole cassette.

### Changed

- `cli/pyproject.toml`: adopted the author email added independently on
  the maintainer's machine, so neither copy overwrites the other going
  forward.

### Confirmed

A follow-up `--live` run (same Windows checkout, with both fixtures
restored) reported 23/23, and the resulting 84-entry cassette replays
23/23 independently on a second (Linux, LF) checkout with zero changes on
that side — the first cross-checkout-reproducible clean pass in this
project's history. Committed as `tests/fixtures/cassettes.json`. All of
`pytest`, `ruff` (both configs), `mypy`, and `pyright` are clean on both
checkouts as of this entry.

## [0.9.8] — 2026-09-17

Docs-only update — no prompt, fixture, or code changes. The docs had
fallen behind five rounds of real `--live` runs and the 0.9.0 feature set.

### Changed

- `README.md`'s "Known limitations" section still said the validation
  matrix "has not been executed against a live Claude Code install" —
  false since 0.9.1; replaced with an accurate summary of the five-round
  live-run history and a pointer to `DESIGN.md`'s write-ups, plus a
  correction that `cli/tests/cassettes/integration.json` (a different,
  still-placeholder cassette from a different test file) remains a
  separate, still-open gap from the fixture matrix's live-run history.
- `README.md`'s CLI section and `cli/README.md`'s "How it works" never
  mentioned three real 0.9.0 features: the diff/file budget
  (`MAX_DIFF_LINES` truncation, `--max-files`), structured-output guards
  (`is_malformed_response`), and suppression's cache-independence. Added
  all three, plus a `--max-files` usage example in `cli/README.md`'s
  Quick start.
- `README.md`'s Development section and `tests/fixtures/README.md` didn't
  mention `scripts/validate_fixtures.py`'s `[i/N]` progress markers
  (0.9.4), the `--seed-placeholders-from-expected` bootstrap flag, the
  `--agent` filter, or `pytest.ini`'s exclusion of `tests/fixtures/` from
  a bare `pytest` run. Added all four, plus an explanation of why editing
  any agent's prompt invalidates every one of that agent's cassette
  entries at once (the request-key hashing design), pointing to
  `DESIGN.md`'s live-run write-ups for what happens when a live run and
  `EXPECTED.md` disagree.
- `CONTRIBUTING.md` had no record of the two most load-bearing lessons
  from this project's actual prompt-tuning history: worked examples
  reliably beat descriptive prose for changing a live model's verdict
  (with the illustrative-bad-example backfire risk noted), and after
  2+ rounds of prompt-only fixes fail, check whether the FIXTURE is the
  problem before writing a third round of prose. Added both, plus a note
  that a bullet edited three-plus times is worth trimming rather than
  extending again.

## [0.9.7] — 2026-09-17

A fifth real `--live` run confirmed round 4's two fixture fixes held on
their first live test (`data-integrity-review/boundary_case` and
`concurrency-resource-review/boundary_case` both passed) — the strongest
evidence yet that those two really were fixture problems, not prompt
problems. Four different failures surfaced instead. See DESIGN.md's "Fifth
real `--live` run" for the full analysis; summary below.

### Fixed

- `data-integrity-review.md`: `true_positive_structural` (billing.py)
  missed for a third time (having passed in between, on byte-identical
  prompt text) — the clearest evidence yet in this project of pure
  run-to-run model variance rather than a prompt-caused effect. Added a
  worked example to the Structural correctness risk bullet, framed
  positively ("report this"), plus an instruction to actively compare
  function bodies when a diff adds multiple same-area functions.
- `performance-review.md`: `leaderboard.py`'s exclusion — unchanged and
  reliable since round 1 — failed for a second consecutive round. This
  round's response explicitly acknowledged the count is fixed at 3 and
  flagged it anyway. Converted the prose-only exclusion into a WRONG/RIGHT
  worked example (this exclusion had never gotten one, unlike most others
  by this point), explicitly addressing that recognizing the N+1 shape and
  still not reporting it are compatible.
- `testing-coverage-review.md`: `false_positive_trap`'s test file drew a
  third distinct complaint in three rounds ("boundary untested" →
  "no value below it" → "exercises the wrong branch", the last of which is
  simply incorrect — the non-VIP rate at exactly the boundary is the only
  mathematically possible outcome for a strict `>` comparison, not a
  defect). Added a worked example showing the current, complete three-test
  file as the definitive reference, closing off the "wrong branch"
  reframing directly.
- `testing-coverage-review.md`: `true_positive` (pricing.py, zero
  accompanying test file) missed for the first time — the plainest
  possible case this agent exists to catch. No structural cause fits (the
  Strict scope bullet is unchanged since 0.8.0), treated as a likely
  single-sample stochastic miss; added a matching worked example as
  low-risk reinforcement rather than a larger prompt change.
- `tests/fixtures/cassettes.json`: re-seeded 11 placeholder entries for the
  two touched agents.

## [0.9.6] — 2026-09-17

A fourth real `--live` run repeated round 3's four failures a fourth time,
plus one brand-new regression on a case that had passed cleanly every
round before. Unlike rounds 2–3, the fix this round is mostly fixtures,
not prompts — see DESIGN.md's "Fourth real `--live` run" for the full
analysis; summary below.

### Fixed

- `tests/fixtures/data-integrity-review/boundary_case`: four straight
  rounds of increasingly explicit, framing-independent prompt instructions
  didn't stop the model reporting a `varchar(50)` email column as
  insufficient — a canonical, deeply-trained "this is wrong" pattern no
  amount of in-context instruction overrode. Changed the fixture's column
  from `Email varchar(50)` to `BatchLabel varchar(20)` (same underlying
  principle, no famous "correct minimum length" for the model to recognize
  and override instructions for). Also added a WRONG/RIGHT worked example
  to `data-integrity-review.md` as defense in depth.
- `tests/fixtures/concurrency-resource-review/boundary_case`: this round's
  finding — `ReportSession.run()` creates a `Statement` per call and never
  closes it — was actually correct, not a model error, and exposed that
  round 2's prompt claim ("closing a Connection reliably closes its
  Statements") was an overstated, driver/pool-dependent claim, not a JDBC
  guarantee. Fixed the fixture (`Statement.closeOnCompletion()`, keeping
  the class to one field so it doesn't reopen round 3's "partial
  construction" argument) and corrected the prompt's inaccurate claim in
  `concurrency-resource-review.md`, plus a matching worked example.
- `performance-review.md`: the "Unbounded resource growth" bullet had grown
  into the longest bullet in the agent set across three rounds of edits,
  and its own false-positive-trap case (`leaderboard.py`, previously
  rock-solid) failed for the first time this round — evidence the bullet's
  length was distorting calibration on unrelated bullets in the same
  prompt. Trimmed it back to one sentence, moved the detail to Explicit
  exclusions (nothing substantive dropped).
- `tests/fixtures/performance-review/boundary_case/config.py`: anchored
  with a comment stating `key` is one of ~20 fixed, developer-controlled
  setting names, mirroring the exact technique (`leaderboard.py`'s "always
  exactly the 3 leaderboard positions" comment) that has reliably worked
  since round 1 — giving the model textual evidence to hang a "no finding"
  verdict on instead of inferring boundedness from an unanchored 4-line
  snippet.
- `testing-coverage-review.md`: round 3's fixture fix for the `total==500`
  boundary held, but the model found a narrower, genuinely new prompt gap
  — demanding a *second* test just below the boundary, which exercises the
  identical branch as the boundary value itself and adds no discriminating
  power. Added an exclusion for redundant boundary-adjacent test demands.
- `tests/fixtures/cassettes.json`: re-seeded 14 placeholder entries for the
  fixtures and prompts touched this round.

### Note

Two of this round's fixes (data-integrity-review, concurrency-resource-review)
are fixture changes, not prompt changes — after three rounds of textual
counter-examples failed to move either verdict, the more honest read was
that the fixtures were asking the model to stay silent about exactly the
kind of finding it has the strongest trained priors toward reporting. A
fifth `--live` run is what confirms whether removing that specific trigger
worked, independent of whether the prompt wording was ever the problem.

## [0.9.5] — 2026-09-17

A third real `--live` run scored the same 16/23 as the second, and — case
for case — the identical 7 failures. Unlike round 2, this is enough
evidence to act on: every one of round 2's four targeted fixes failed to
change the model's verdict a second time (though the model's specific
argument shifted each time, meaning the fixes closed the door they aimed
at and the model found a different one), and both cases round 2
deliberately left alone for lack of evidence reproduced identically,
confirming they're real rather than noise. See DESIGN.md's "Third real
`--live` run" for the full analysis; summary below.

### Fixed

- `data-integrity-review.md`: moved the widening/brand-new-table carve-outs
  out of the Migrations/schema Strict-scope bullet (which had grown into
  the longest bullet in the list across two rounds of edits) and into
  Explicit exclusions as a single categorical rule: a brand-new table's
  column width/constraint choice is out of scope "no matter how you frame
  the argument" — including comparing it to an external standard (RFC
  5321, typical name lengths), which the live model was using to reframe
  the same "future insert" argument round 2 had already excluded.
- `concurrency-resource-review.md`: closed a "partial construction" framing
  the model shifted to after round 2 excluded "the acquisition call itself
  throws" — explicit instruction not to invent additional initializer/
  constructor code the diff doesn't show just to create a window for the
  argument; for a one-field class there is no such window.
- `performance-review.md`: made the cache-key evidence requirement literal
  — if the diff shows no caller of the cache-reading function at all
  (true for this fixture), the Evidence Bar's "path exists"/"trigger is
  plausible" clauses cannot be met by definition, and the agent may not
  reach outside the diff for a plausible-sounding caller to complete its
  own argument. Two prior wordings of "don't assume unbounded" hadn't
  stopped the model asserting the key was attacker-controlled anyway.
- `api-type-contract-review.md`: replaced descriptive prose (which hadn't
  changed the verdict across two rounds, though it did drop the reported
  severity) with a concrete WRONG/RIGHT worked example matching the exact
  `formatPrice`/`checkout.ts` fixture, mirroring CLAUDE.md's Output
  Contract section — the one technique in this project with a confirmed
  track record of changing live-model behavior (it fixed the round-1
  code-fence violation on the first attempt).
- `reliability-availability-review.md`: first edit to this agent. Added an
  exclusion for retry loops that don't classify errors as retryable before
  retrying, as long as the loop already has the three properties its own
  Retry/backoff defects bullet requires (capped, backed off, propagates on
  exhaustion) — closes a real, reproduced-twice gap, not a hedge against
  a single sample.
- `tests/fixtures/testing-coverage-review/false_positive_trap/test_discounts.py`:
  not a prompt bug. This fixture's diff adds a `total > 500` boundary and
  tests only `total=600` (never the boundary itself) — a textbook match
  for testing-coverage-review's own documented "untested new boundary
  conditions" scope. Added a test at `total=500` to close the actual gap
  rather than teaching the agent to ignore its own scope.
- `tests/fixtures/cassettes.json`: re-seeded 17 placeholder entries for the
  five agents whose prompts changed plus the one fixture content change.

### Note

Two rounds of prompt edits produced zero verdict changes on the four
repeat boundary cases (data-integrity-review, concurrency-resource-review,
performance-review, api-type-contract-review). This round's fixes are more
structural (categorical rules, an evidence-bar gate, a worked example)
than rounds 1–2's incremental counter-examples. If a fourth `--live` run
shows the same four cases still failing, the more honest conclusion may be
that these specific boundary cases are at or past the practical ceiling of
single-shot prompt engineering against this model.

## [0.9.4] — 2026-09-17

### Added

- `scripts/validate_fixtures.py` now prints a `[i/N] agent/case ...`
  progress marker before each case runs, followed immediately by its
  PASS/FAIL result, instead of running the whole manifest silently and
  printing everything only once it's all done. Matters most for `--live`
  runs: each case is a real network call to a Foundry resource that can
  take anywhere from a couple of seconds to tens of seconds, and a 23-case
  run with no output in between gave no way to tell "still working" from
  "hung," or which specific case a slow or stuck run was on. `_run_all_cases`
  replaces the old batch-then-print `_print_case_results`; the final
  `N/M cases passed` summary line is unchanged.

## [0.9.3] — 2026-09-17

A second real `python scripts/validate_fixtures.py --live` run (43
cassette entries recorded) scored 16/23, up from 13/23 in 0.9.1. Both
round-1 architectural fixes (the output-contract code-fence violation,
testing-coverage-review's `make_order()` false negative) fully held. See
DESIGN.md's "Second real `--live` run" for the full failure-by-failure
analysis; summary below.

### Fixed

- `concurrency-resource-review.md`: added a rebuttal for constructing a
  leak scenario premised on the resource's own acquisition call failing
  (nothing acquired means nothing to leak), and for double-counting a
  child resource (e.g. a JDBC `Statement`) whose parent's `close()`
  already covers it.
- `data-integrity-review.md`: extended the brand-new-table exclusion to
  cover forward-looking ("a future insert might overflow this column")
  and downstream-assumption ("some consumer might expect a constraint
  that isn't there") arguments, not just the backfill/narrowing angle
  0.9.1 addressed.
- `api-type-contract-review.md`: cross-referenced the compile-time-
  checked-break exclusion directly from the Strict-scope bullet it
  overrides, since the two were in tension with no signal for which one
  controls a same-repo, same-language, compiler-catchable break.
- `performance-review.md`: reworded the unbounded-cache-key exclusion to
  drop illustrative bad-case vocabulary ("a user ID, a tenant ID") that
  the live model was observed quoting back as if it applied to code that
  didn't actually exhibit it, inverting the instruction's own conclusion.
  Replaced with a plain positive-evidence requirement and an explicit
  "no indication either way → do not report" resolution.
- `scripts/validate_fixtures.py`: restored the `# noqa: E402` suppression
  comments on the post-`sys.path.insert` imports that a prior cleanup
  (0.9.1) had removed on the mistaken belief that ruff's default rule set
  didn't include E402 — it does (E402 is part of the default `E4` import
  group), so those imports were failing `ruff check` outright rather than
  carrying an "unused" suppression.
- `tests/fixtures/cassettes.json`: re-seeded 13 placeholder entries for
  the four agents whose prompts changed this round (data-integrity-review,
  concurrency-resource-review, performance-review, api-type-contract-review).

### Not fixed (no prompt change made — insufficient evidence)

- `data-integrity-review/true_positive_structural` (billing.py) missed
  for the first time; `reliability-availability-review/false_positive_trap`
  (rates.ts, an agent untouched in any round) newly over-triggered.
  Neither is explained by anything edited this round or in 0.9.1. Left
  alone pending a further `--live` run to distinguish real regression from
  single-sample model variance.

## [0.9.2] — 2026-09-17

Real-world friction found by running `pytest` bare from the repo root for
the first time (previous instructions only ever said `cd cli && pytest
tests/` or `pytest cli/tests`).

### Fixed

- A bare `pytest` invocation at the repo root failed collection entirely
  ("import file mismatch") on the first basename collision under
  `tests/fixtures/` (`test_discounts.py` legitimately exists in both
  `testing-coverage-review/boundary_case/` and `.../false_positive_trap/`,
  read in isolation by `scripts/validate_fixtures.py`, never meant to be
  collected as an ad hoc pytest suite -- there are no `__init__.py` files
  there by design). `ruff.toml` and `mypy.ini` already excluded
  `tests/fixtures/` from their own tools for this exact reason; pytest had
  no equivalent. Added a root `pytest.ini` with `norecursedirs =
  tests/fixtures` to close the gap.
- `scripts/validate_fixtures.py`'s `build_case_diff()` walked a fixture
  case directory with `rglob("*")` excluding only `EXPECTED.md` by name --
  a `__pycache__/*.pyc` left behind by importing one of the (real,
  executable) testing-coverage-review test fixtures got folded straight
  into that case's "diff" as binary noise. Now also excludes any path
  under a `__pycache__` directory.

## [0.9.1] — 2026-09-17

The first real `python scripts/validate_fixtures.py --live` run against
`tests/fixtures/` (run by the CLI's own user against a real Foundry
resource) scored 13/23 -- the first genuine, live-model signal on whether
the 8 specialist agents actually hold up to their own `EXPECTED.md`
verdicts, as opposed to the placeholder cassette (seeded FROM those same
verdicts) merely confirming the harness's wiring. See DESIGN.md's "First
real `--live` run against `tests/fixtures/`" for the full failure-by-
failure analysis; summary below.

### Fixed

- `CLAUDE.md`'s Output Contract now explicitly forbids wrapping a response
  in a markdown code fence or adding any explanation before/after it, with
  a concrete right/wrong example -- the existing wording didn't survive a
  real model, which repeatedly fenced a correct "No high-impact issues
  found." verdict and sometimes appended a justifying sentence after it,
  both exact-match contract violations regardless of the verdict's
  correctness.
- Five specialist agents each had an existing, correctly-worded exclusion
  rule that a live model still misapplied on a hard case; each gained a
  concrete counter-example anchored to the actual failing input rather
  than a restatement of the same abstract rule:
  `security-review.md` (an f-string SQL query built from a value already
  identified as a module constant, flagged over hypothetical future
  misuse), `data-integrity-review.md` (a column-widening migration and a
  brand-new `CREATE TABLE` flagged as data-loss risks -- neither can lose
  an existing row), `concurrency-resource-review.md` (a class's own
  resource field flagged as a leak despite the class implementing
  `AutoCloseable`/delegating release to its own `close()`),
  `performance-review.md` (a loop explicitly commented as bounded to a
  fixed 3 items flagged as N+1; a memoizing cache over a plausibly small
  fixed key space flagged as unbounded growth), `api-type-contract-
  review.md` (a same-file TypeScript signature break the compiler already
  catches, flagged as CRITICAL despite the agent's own Evidence
  requirements already saying to prefer findings the type checker will
  NOT catch).
- `orchestrator.build_review_user_message()` (new, shared by
  `orchestrator.py` and `scripts/validate_fixtures.py` so they can never
  silently diverge on this) appends a short note after the diff stating
  plainly that the specialist has no `Read`/`Grep`/`Bash` tool access in
  this environment and the diff shown is its complete evidence -- fixing a
  real false negative where testing-coverage-review, whose own prompt
  (written for a live Claude Code session with real tools) says to
  "confirm by reading the test diff... do not assume absence without
  checking," took that literally and withheld an otherwise-unambiguous
  missing-test-coverage finding because it had no way to actually check.
- `testing-coverage-review.md` gained an exclusion for flagging a test as
  broken merely for referencing a helper/fixture not defined in the
  visible diff (assume it exists elsewhere unless the diff proves
  otherwise) -- import/syntax validity isn't this agent's job. Also fixed
  the fixture that exposed this:
  `tests/fixtures/testing-coverage-review/false_positive_trap/test_discounts.py`
  now actually imports `compute_discount` and defines `make_order()`,
  removing a genuine ambiguity a strict reading of the old fixture had.

### Changed

- `tests/fixtures/cassettes.json` -- the real cassette the user's `--live`
  run produced -- is now entirely stale (every prompt above changed, and
  so did the message shape), so it's been re-seeded with fresh
  `EXPECTED.md`-derived placeholders via
  `--seed-placeholders-from-expected`, same bootstrap convention as before
  the first live run. `cli/tests/cassettes/integration.json` was
  re-seeded the same way. Both need a fresh `--live` run to confirm
  whether the fixes above actually hold against a real model -- unlike a
  code change, this can't be verified with certainty by this sandbox
  alone.

## [0.9.0] — 2026-09-17

The three production-readiness gaps left as a deliberately unimplemented
roadmap in 0.8.0's DESIGN.md section ("Roadmap: structured output, budget
management, feedback loop") are now built, each with its own TDD-first
test coverage rather than being rushed in alongside the CI/cassette work
that originally identified them.

### Added

- **Structured output & parsing guards.** `findings.is_malformed_response()`
  distinguishes a genuine clean review (`raw_text == NO_FINDINGS_TEXT`)
  from a response that drifted off the `[SEVERITY] file:line — Title`
  contract entirely — previously both parsed to the same empty finding
  list, silently reporting a broken specialist call as "no issues found."
  A malformed response is now surfaced via a new
  `FileReviewResult.malformed_agents` (reported by `_print_review()` and
  `--json` the same way as `missing_agents` already was) and, critically,
  is never cached as a completed review — it's retried on the next run
  instead of permanently masking whatever that specialist would have
  said.
- **Dynamic token and context budget management.**
  `routing.is_excluded_from_review()` drops lockfiles, vendored/third-party
  trees, and common minified/generated-code paths *before* routing, so
  they cost zero inference budget rather than merely being absent from
  the report. `orchestrator._truncate_diff()` caps any single file's diff
  at `MAX_DIFF_LINES` (4000) with an explicit `[... diff truncated ...]`
  marker at the cut point, sent to the model with that caveat visible
  rather than silently dropped (`FileReviewResult.truncated`). A new
  `--max-files` flag caps how many routed files one run reviews at all,
  prioritizing files matched by the most specialists (`routing.py`'s
  already-computed, zero-cost decision, as a proxy for risk surface) and
  reporting the rest as `skipped_for_budget` — for a pathologically wide
  diff (a huge rename, a generated-content commit), not everyday use. A
  new `--json` flag prints findings and every warning `_print_review()`
  shows as structured JSON, for a CI/CD pipeline to consume without
  regex-parsing this tool's human-readable stdout.
- **Feedback loop & false-positive suppression.** An optional
  `.claude/ignore-findings.yml` at the target repo root (see
  `suppressions.py`) lets a team mark a specialist's finding as an
  accepted false positive or accepted risk, matched by agent name (or
  `"*"` for any agent) and a glob against `Finding.location` — a pattern
  rather than an exact line number, since line numbers drift with every
  unrelated edit and an exact-line suppression would silently stop
  matching on the very next commit. Applied in `run_review()` *after*
  parsing but independent of caching — the cache still stores the raw,
  unsuppressed finding, so editing the suppression file takes effect
  immediately with no cache invalidation and no fresh model call.
  Suppressed findings are dropped from the report, surfaced as a summary
  line (and in `--json`), and logged to a gitignored
  `.agent-cache/suppressions.log` (`agent`, `location`, `reason`,
  timestamp per occurrence, every run) — a concrete, evidence-based
  record of what a given specialist tends to false-positive on, for
  anyone maintaining its prompt over time.
- New runtime dependency: `pyyaml` (parses `.claude/ignore-findings.yml`).
  Unlike `prompts.py`'s own deliberately tiny frontmatter reader — which
  only ever reads a fixed, tool-generated set of flat scalar fields — this
  file is meant to be hand-edited by a team with ordinary YAML
  expectations (quoting, comments), which is exactly the case a real,
  well-tested parser is worth a small dependency for.

## [0.8.1] — 2026-09-17

Fixes found by actually running 0.8.0's new tests and `--live` harness for
the first time on a real Windows machine, not just in this project's
Linux CI/build environment.

### Fixed

- `init_repo()`'s `created_files`/`skipped_files` used `str(Path)`
  (backslash-separated on Windows), inconsistent with every other path
  this CLI surfaces (git output, routing decisions, findings'
  `file:line` locations) and breaking exact-match consumers on Windows.
  Now `.as_posix()`.
- `agents_client.py`'s `DeploymentError` handling (a Foundry resource
  with a model configured but not *deployed* for deploymentless
  inference) previously only re-raised the SDK's bare message. Now
  enriched with a hint pointing at `ANTHROPIC_FOUNDRY_MODEL`/`--model`,
  matching the existing connection-error enrichment.
- `scripts/validate_fixtures.py --live` didn't load `.env` the way
  `agent-review`/`agent-init` do, so real Foundry credentials sitting in
  `.env` were invisible to it unless exported into the shell by hand.
  Now auto-loads `.env` the same way, via the same helper.
- `cli/tests/test_init.py`'s own idempotency test wrote CLAUDE.md's real
  content (which contains em dashes) back to disk via `write_text()`
  with no explicit encoding, silently falling back to the platform's
  locale default -- UTF-8 on Linux, commonly cp1252 on Windows, which
  mojibake's the em dash into an invalid UTF-8 byte and broke the very
  next read. Fixed the test; added `encoding="utf-8"` to one remaining
  implicit-encoding write in `cache.py` for the same reason (currently
  safe due to `json.dumps`'s `ensure_ascii=True` default, no longer
  relying on that); added `test_default_rules_encoding.py` as a standing
  guard against this class of corruption in the shipped prompt files.

### Docs

- `.env.example`'s `ANTHROPIC_FOUNDRY_MODEL` comment now names this as
  the single most common first-run failure reported so far (not a
  hypothetical), and points at `claude-haiku-4-5` as the one model
  confirmed working end-to-end so far, for anyone who doesn't already
  know what's deployed in their resource.

## [0.8.0] — 2026-09-17

### Added

- `.github/workflows/cli-ci.yml` — `cli/`'s own pytest/ruff/mypy/pyright
  suite now runs in CI (Python 3.10/3.11/3.12 matrix) on every push/PR
  touching `cli/`. This project had zero CI coverage for the standalone
  CLI package before this.
- `cli/tests/support/cassette.py` — a small record/replay module shared by
  the two testing gaps below: a `Reviewer` that replays a recorded
  response by default (deterministic, free) and switches to a real
  Foundry call only when explicitly asked to re-record. Entries are keyed
  by a content hash of `(system_prompt, user_message)`, not a
  human-assigned name, so a prompt or fixture change can never be
  silently served a stale recording — a miss raises `CassetteMissError`
  instead.
- `scripts/validate_fixtures.py` — runs every case in
  `tests/fixtures/manifest.json` through its target `.claude/agents/*.md`
  specialist and checks the response against that case's expected verdict
  (`must_fire` / `must_not_fire` / `must_not_fire_standalone`), reusing
  the standalone CLI's own prompt-loading/model-calling machinery.
  Replaces the TODO-only scaffolding that used to live inline in
  `validate-agents.yml`. `tests/fixtures/cassettes.json` (23 entries, one
  per fixture case) is seeded with placeholder responses derived from
  each case's own `EXPECTED.md` — this build environment has no Foundry
  credentials — clearly labeled as placeholder data pending a `--live`
  refresh, not yet a real trip through the model.
- `.github/workflows/validate-agents.yml` rewritten from scaffolding to a
  working two-job pipeline: `validate` runs the harness above in replay
  mode on every relevant PR (a cassette miss is a hard failure); the
  manual `validate-live` job (`workflow_dispatch`) calls a real Foundry
  resource, refreshes the cassette, and uploads it as a build artifact
  for a maintainer to review and commit.
- `cli/tests/test_cli_integration_live.py` — an opt-in live/replay
  integration test for `cli/`'s own orchestrator, alongside the existing
  fully-scripted `test_cli_integration.py`. Runs the real
  `orchestrator.run_review()` (routing, caching, git diffing, parsing)
  against a cassette-backed reviewer by default
  (`cli/tests/cassettes/integration.json`, also placeholder-seeded for
  now), or a real `AnthropicFoundryReviewer` when
  `AGENT_REVIEW_RECORD_LIVE=1` is set with real credentials.

### Fixed

- `git_utils.diff_for_file`'s untracked-file branch passed the file's
  *absolute* path to `git diff --no-index`, which git wrote verbatim into
  the diff header — leaking the local checkout's filesystem path into the
  text sent to the model (and, unlike every other diff this module
  produces, not relative to the repo). Found while building the cassette
  tests above, since it also made that diff shape impossible to hash
  reproducibly across machines. Now passes the relative path.

## [0.7.1] — 2026-09-17

### Fixed

- `ANTHROPIC_FOUNDRY_BASE_URL` -- documented in `cli/.env.example` since
  0.4.4 as a real, supported (if advanced/rare) alternative to a Foundry
  resource name -- was actually unusable: `AnthropicFoundryReviewer`
  required a non-empty resource unconditionally, so the base-url-only
  path the docs described could never be reached. Now a resource name OR
  `ANTHROPIC_FOUNDRY_BASE_URL` satisfies construction (mutually exclusive,
  matching `anthropic.AnthropicFoundry`'s own constructor contract, with a
  clearer error than the SDK's if both are set), for both API-key and
  Entra ID auth. Found during a documentation-accuracy pass, not a user
  report -- caught by checking that every `.env.example`/`DESIGN.md`
  claim about supported configuration actually has a working code path,
  not just by reading the code in isolation.

### Docs

Stale references found and corrected during the same pass (none changed
behavior, only what the docs claimed about it):

- `DESIGN.md`'s file manifest and architecture prose still described
  `discovery.py` as detecting the target repo's language mix; that
  capability was removed as dead code in 0.7.0 (nothing ever consumed
  it -- routing has always worked directly off diff-content patterns).
  Also missing from the file manifest: `layout.py` (added in 0.7.0) and
  the actual current test count (had drifted to 80; now 114).
- `CONTRIBUTING.md` pointed at `docs/DESIGN.md`; the file has always
  lived at the repo root as `DESIGN.md` -- no `docs/` directory exists.

## [0.7.0] — 2026-09-10

### Changed

Consolidated scattered constants and removed dead code, found via a
manual audit of every module-level constant plus `vulture --min-confidence
60` (each flagged symbol cross-verified by hand against `src/`, `tests/`,
and `pyproject.toml` before being touched):

- The four `ANTHROPIC_FOUNDRY_*` environment variable names were
  hardcoded as bare string literals at each lookup site in
  `agents_client.py`, and separately repeated inside that same file's
  error messages and inside `cli.py`'s `--help` text. Extracted into
  named, public constants (`ENV_RESOURCE`, `ENV_API_KEY`,
  `ENV_USE_ENTRA_ID`, `ENV_MODEL`) in `agents_client.py`, imported by
  `cli.py`, so the name a user is told to set can never drift from the
  name actually read.
- `init.py` (the writer) and `prompts.py` (the reader) each hardcoded
  their own copies of `.agent-rules`, `.claude`, `agents`, `CLAUDE.md`,
  `DESIGN.md`, and an identical private `_bundled_default_dir()`
  function -- two independent copies of the same target-repo layout that
  could silently drift (e.g. `init.py` scaffolding into a directory
  `prompts.py` no longer looks for). Both now import a single shared
  `layout.py` module for these constants and the bundled-defaults lookup.
- `cli.py`'s `_KNOWN_COMMANDS` set duplicated the three subcommand name
  literals (`"review"`, `"commit"`, `"heal"`) already spelled out at each
  `add_parser()` call and at the `argv = ["review", *argv]` default-command
  line. Named `_REVIEW_CMD`/`_COMMIT_CMD`/`_HEAL_CMD` constants are now
  declared once and reused at every one of those call sites, with
  `_KNOWN_COMMANDS` derived from them.
- `agent_review/__init__.py`'s `__version__` was a hand-maintained literal
  (`"0.1.0"`) that had already silently drifted from `pyproject.toml`'s
  real version and had zero consumers to catch the mismatch. Now derived
  dynamically via `importlib.metadata.version("agent-review")`, with a
  `PackageNotFoundError` fallback for an uninstalled source checkout.

### Removed

Dead code with zero callers anywhere in `src/` or `tests/`, confirmed by
hand (not just by `vulture`, which cannot see `pyproject.toml`'s
`[project.scripts]` entry points and so also flags `main_init` -- a real
console-script target that was correctly left in place):

- `cache.py`'s `AgentCache.stats()` method.
- `git_utils.py`'s `read_file()` function.
- `routing.py`'s `ALL_AGENTS` constant and `RoutingDecision.reason` field
  (plus the now-unnecessary reason-string bookkeeping inside
  `route_file()` that only existed to populate it).
- `discovery.py`'s `EXTENSION_LANGUAGE` mapping, `language_for_path()`,
  and `detect_languages()`, along with their tests in `test_discovery.py`
  (`test_language_for_path`, `test_detect_languages_scans_tree`). The
  module's docstring claimed this fed routing decisions; `routing.py`
  never actually consulted it, so the docstring was corrected as part of
  the removal.

Deliberately kept, despite being unread or test-only, because each has a
real reason to exist: `cache.py`'s `reviewed_at` field (human-inspectable
audit timestamp, per the module's own docstring), `git_utils.py`'s
`current_commit()` (used by `test_git_utils.py`), and `healing.py`'s
`HealingProposal.raw_response` field (kept for diagnosing a bad parse of
the model's response). `git_utils.DEFAULT_GIT_TIMEOUT_SECONDS` and
`healing.DEFAULT_TIMEOUT_SECONDS` were considered for merging but kept
separate: they bound semantically distinct operations (git plumbing vs.
a target repo's own test suite) that have no reason to share one knob.

## [0.6.0] — 2026-09-09

### Fixed

Findings from a full evidence-based review of the CLI (all 7 specialist
lenses, each finding hand-verified against the actual source before being
acted on):

- `routing.py`'s `is_semantic_noise()` compared removed/added diff lines
  as a *sorted multiset* rather than an ordered sequence, so a diff that
  only *reordered* lines with real semantic effect (e.g. swapping a
  `COMMIT` to run before the write it's meant to follow) was silently
  classified as a no-op and skipped before any specialist -- including
  data-integrity-review's own `COMMIT`/`ROLLBACK` pattern -- ever saw it.
  Now compared as an ordered sequence; only an exact, order-preserving
  match (pure reformatting) is treated as noise.
- `orchestrator.py`'s `_review_one_file()` had no error handling around
  `reviewer.complete()`, so a single file's failed model call (a
  transient Foundry timeout, a rate limit, the enriched connection-error
  `RuntimeError`) propagated out of `run_review()`'s `ThreadPoolExecutor`
  fan-out entirely, discarding every other file's already-computed
  findings and cache entries in the same batch before `cache.save()` ever
  ran. Per-file failures are now caught, reported back on
  `FileReviewResult.error` (deliberately not cached, so a transient
  failure is retried next run), and surfaced in `agent-review`'s output;
  the rest of the batch's successful results and cache writes are
  unaffected.
- The same function also cached a routed agent with no loaded prompt
  (e.g. a customized `.agent-rules/agents/` file renamed or removed) as
  if it had cleanly reviewed the file, permanently and silently hiding it
  from every future run against that content. The cache is now keyed off
  only the agents that actually ran; a missing agent is tracked on
  `FileReviewResult.missing_agents` and printed as a warning instead.
- `git_utils.py`'s subprocess calls had no timeout and inherited stdin --
  a blocked `git` process (a hook, credential prompt, or LFS/textconv
  filter waiting on input) could hang a review indefinitely with no
  recovery. Every call now passes `timeout=` (raising `GitError` on
  expiry) and `stdin=subprocess.DEVNULL`. The empty-tree fallback sentinel
  is now a named `EMPTY_TREE_SENTINEL` constant.
- `run_review()`'s routing loop and `_review_one_file()` each
  independently fetched the same per-file diff, and `blob_hash()` was
  called once per routed file -- doubling git subprocess overhead on
  every run, which scales with the whole repo (not just a PR's file
  count) on a fresh-repo/`agent-init` scan that diffs against the
  empty-tree sentinel. The diff is now fetched once and threaded through;
  a new batched `git_utils.blob_hashes()` replaces the per-file
  `hash-object` calls with one call per chunk of routed files.
- `healing.py`'s `check_patch_applies()`/`apply_patch()` (`heal --apply`'s
  `git apply` calls) had no timeout, unlike `run_tests()`. Both now share
  the same `DEFAULT_TIMEOUT_SECONDS` and `TimeoutExpired` handling.
- `cli.py`'s `cmd_commit()` didn't wrap its `git diff --staged`
  precondition check in a try/except, so a `GitError` (e.g. a stale
  `.git/index.lock`) surfaced as a raw traceback instead of the clean
  `error: ...` message every other command here guarantees. Now wrapped
  to match.

### Testing

- `cmd_heal`'s `--apply` safety gate (the only thing stopping a plain
  `agent-review heal` from writing a model-proposed patch) previously had
  no test coverage at all; 2 new tests confirm `apply_patch` is never
  called without `--apply` and is called with it.
- `AnthropicFoundryReviewer.complete()`'s successful response path (the
  `block.type == "text"` join/filter) previously had zero coverage --
  every existing test only exercised its exception paths. 1 new test
  covers a multi-block response including a non-text block.
- `resolve_base_ref()`'s empty-tree-fallback test previously only
  asserted a truthy return value, which the host's default branch name
  could satisfy by accident without ever exercising the real fallback.
  Replaced with 3 tests pinned to `EMPTY_TREE_SENTINEL`, explicit-ref
  precedence, and `origin/main`-over-local-`main` precedence.
- `run_tests()`'s `TimeoutExpired` handling (and `_decode_partial_output`,
  added specifically for its `bytes | str | None` hazard) previously had
  no test. 1 new test.
- `_ALWAYS_IGNORED_PREFIXES`'s exclusion of `.agent-cache/`/`.agent-rules/`
  from routing previously had no test. 1 new test.
- 2 new orchestrator tests pin the two behavior fixes above directly: one
  file's failed model call no longer discards another file's cached
  result in the same run; a missing-prompt agent is no longer cached as a
  clean review and is correctly re-attempted once the prompt is restored.
- 1 existing test (`test_reordered_identical_lines_is_noise`) encoded the
  old, incorrect behavior and was updated to assert the fix; 1 new test
  pins the concrete `COMMIT`/`UPDATE` reordering scenario from the fix
  description. 116 tests total, up from 106.

## [0.5.0] — 2026-09-09

### Added

- `ANTHROPIC_FOUNDRY_MODEL` environment variable, so `--model` doesn't
  have to be passed on every invocation (directly requested after the
  first live run needed `--model claude-haiku-4-5` to match what was
  actually deployed). Precedence matches `--resource`/`--use-entra-id`:
  explicit `--model` flag > `ANTHROPIC_FOUNDRY_MODEL` > `DEFAULT_MODEL`
  (`claude-sonnet-5`). `--model`'s argparse default changed from
  `DEFAULT_MODEL` to `None` so "flag not given" can be told apart from
  "flag given, happens to equal the default" -- otherwise the env var
  could never win. Same whitespace-stripping treatment as
  `ANTHROPIC_FOUNDRY_RESOURCE`/`API_KEY` (a deployment name copied from
  the Foundry portal is just as likely to pick up a stray space). Caught
  a real bug in its own first implementation this way: stripping only
  the *final* resolved value let a whitespace-only
  `ANTHROPIC_FOUNDRY_MODEL="   "` win over `DEFAULT_MODEL` instead of
  being treated as unset, since a whitespace string is truthy to `or` --
  fixed by stripping each candidate before the fallback chain, not after.
  `.env.example`, `cli/README.md`, and `cli.py`'s help text updated. 8 new
  tests; 106 tests total, up from 98.

## [0.4.8] — 2026-09-09

### Verified

- **First confirmed live, end-to-end run**: `agent-review --path <repo>
  --model claude-haiku-4-5` against a real Microsoft Foundry resource and
  a real target repository, returning a correct clean result ("No
  high-impact issues found.", 2 files analyzed, 0 cache hits / 2 cache
  misses on a first run). Everything from 0.4.4 through 0.4.7 in this
  changelog was fixed in direct response to friction hit on the way to
  this first real run: `.env` not being loaded (0.4.4/0.4.5), unstripped
  whitespace in `ANTHROPIC_FOUNDRY_RESOURCE`/`API_KEY` (0.4.6), an opaque
  "Connection error." when the resource env var held a model *deployment*
  name instead of the actual *resource* name (0.4.7), and finally a
  `DeploymentError` ("does not support deploymentless inference") when
  `--model` didn't match what was actually deployed in that Foundry
  resource -- resolved by passing `--model claude-haiku-4-5` to match the
  deployment that actually exists there. `DESIGN.md` and both READMEs'
  "honest limitations" sections updated -- this was previously documented
  as an untested gap; it no longer is.

## [0.4.7] — 2026-09-09

### Changed

- `AnthropicFoundryReviewer.complete()` now enriches a connection-level
  failure (no HTTP response at all -- bad host, DNS failure, network
  unreachable) with the exact URL it tried to reach and a hint that
  `ANTHROPIC_FOUNDRY_RESOURCE` may hold a model *deployment* name (e.g.
  `gpt-5.2-1`, `claude-sonnet-4-6`) instead of the actual Foundry
  *resource* name -- confirmed via Microsoft's own docs that this exact
  mix-up is common enough that they call it out by name. The SDK's own
  message ("Connection error.") gave no hint which host it tried or why;
  a real report showed exactly this failure mode. An error that already
  carries an HTTP response (e.g. a 401 from a resource that DOES
  resolve) is left untouched -- only the no-response case is rewrapped,
  so `cli.py`'s existing `error: review failed: {exc}` still surfaces
  the SDK's own, already-informative message for every other failure
  kind. 2 new tests (enrichment fires only for a connection-shaped
  error; a status-shaped error propagates unchanged) using lightweight
  duck-typed fakes rather than constructing real SDK exceptions, since
  the anthropic package's underlying httpx variant has already changed
  once across the versions this project supports (`anthropic>=0.74.0`)
  and a test shouldn't couple to that internal detail. 98 tests total,
  up from 96.

## [0.4.6] — 2026-09-09

### Fixed

- `ANTHROPIC_FOUNDRY_RESOURCE` and `ANTHROPIC_FOUNDRY_API_KEY` (from
  either the environment/`.env` or explicit `resource=`/`api_key=`
  kwargs) are now `.strip()`-ped before being handed to
  `AnthropicFoundry`. A value copied from a browser, a `.env` file, or a
  PowerShell here-string very commonly picks up a trailing newline or
  stray space; confirmed directly against the `anthropic` SDK that this
  previously flowed straight through -- a trailing space in `resource`
  gets URL-encoded into the hostname itself
  (`https://my-resource%20%20.services.ai.azure.com/...`), and an
  unstripped key is sent byte-for-byte in the Authorization header. Both
  produce a 401 ("Access denied due to invalid subscription key or wrong
  API endpoint") indistinguishable from a genuinely wrong credential. A
  whitespace-only value is now correctly treated as absent (the existing
  "No Microsoft Foundry resource/API key" errors), not as present-but-
  broken. 4 new regression tests; 96 tests total, up from 92.

## [0.4.5] — 2026-09-09

### Changed

- `agent-review`/`agent-init` now load `.env` automatically at startup
  (new `_load_dotenv_if_present()` in `cli.py`, via the `python-dotenv`
  dependency added in 0.4.4's release cycle -- now a real runtime
  dependency, not just documentation). Searches upward from the current
  directory (never downward, never overriding a variable already set for
  real), matching most `.env`-aware tools. Fixes the exact failure mode
  reported after 0.4.4 shipped `.env.example`: filling in `.env` had no
  effect on its own, because nothing loaded it -- `ANTHROPIC_FOUNDRY_*`
  still had to be exported by hand every session. `.env.example` and
  `cli/README.md` rewritten to explain the upward-search directory rule
  (put `.env` where you run the command from, commonly this repo's root
  -- not inside `cli/`, which is a subdirectory the search can't see from
  there).
- Added `cli/tests/conftest.py` with an autouse fixture neutralizing the
  new dotenv lookup for the rest of the suite (otherwise a real `cli/.env`
  on a contributor's machine could leak Foundry credentials into every
  test process and make test behavior depend on that machine's disk); a
  new `test_cli_dotenv.py` exercises the real function directly (cwd
  lookup, parent-directory lookup, real-env-wins-over-dotenv precedence,
  no-file no-op, missing-dependency no-op). Test count: 92, up from 87.

## [0.4.4] — 2026-09-09

### Added

- `cli/.env.example`: a copy-paste template documenting
  `ANTHROPIC_FOUNDRY_RESOURCE`, `ANTHROPIC_FOUNDRY_API_KEY`,
  `ANTHROPIC_FOUNDRY_USE_ENTRA_ID`, and `ANTHROPIC_FOUNDRY_BASE_URL`
  (the last two mutually exclusive with the first, noted inline), plus a
  reminder that model selection is a `--model` CLI flag, not an env var.
  `agent-review` doesn't auto-load `.env` files (no `python-dotenv`
  dependency added, to keep the runtime dependency list to just
  `anthropic`), so the file's header comment gives the exact command to
  export it manually in both bash/zsh and PowerShell. `.gitignore` now
  ignores `.env`/`.env.*` while explicitly un-ignoring `.env.example`, so
  a filled-in copy can never get committed by accident.

## [0.4.3] — 2026-09-09

### Fixed

- Added root-level `pyrightconfig.json` (`extraPaths: ["cli/src"]`) so
  Pylance/Pyright in VS Code resolves `agent_review` in
  `cli/tests/*.py` without depending on whether `pip install -e cli[dev]`
  has been run in the active interpreter -- every test file adds
  `cli/src` to `sys.path` at runtime (which `pytest` executes fine, but a
  static analyzer never does). Verified with a completely clean install
  (fresh virtualenv, `agent_review` not even installed) that `pyright .`
  resolves every import in the repo with 0 errors; separately verified
  that `pip install -e ".[dev]"` into a fresh virtualenv from scratch
  still passes the full suite (87 tests), `ruff check .`, and
  `python -m mypy .` with no changes needed -- the imports themselves
  were never broken, only unresolvable to an editor that hadn't been
  pointed at an interpreter with the package installed. `cli/README.md`
  documents both fixes (the config file, and selecting the right
  interpreter) since either alone can look like it "didn't work" if the
  other is also missing.

## [0.4.2] — 2026-09-09

### Fixed

- Real `mypy` findings from a run against the repo root:
  - `healing.py`'s `TimeoutExpired` handler passed `exc.stdout`/`exc.stderr`
    (typed `bytes | str | None`, since `TimeoutExpired`'s own attributes
    aren't generic over the subprocess call's `text=True`) straight into
    `TestRunResult`'s `str`-only fields, and concatenated one of them with
    an f-string, which mypy correctly flagged as a potential
    `bytes + str` `TypeError`. In practice `text=True` makes this always
    a `str` at runtime, but nothing enforced that statically. Fixed with a
    small `_decode_partial_output()` helper that decodes bytes
    defensively instead of assuming.
  - `test_healing.py` and `test_commit.py`'s `ScriptedReviewer.calls = []`
    needed an explicit `list[tuple[str, str]]` annotation.
- Added root-level `mypy.ini`, mirroring `ruff.toml`: excludes
  `tests/fixtures/` (the intentionally broken/incomplete validation-matrix
  snippets, which also collide on duplicate module names across their
  true-positive/false-positive/boundary-case folders — none of that is
  meant to type-check or import as one package). `mypy` added to
  `cli/`'s dev dependencies (`pyproject.toml` and `requirements-dev.txt`).
  `python -m mypy .` now passes cleanly from the repo root.

## [0.4.1] — 2026-09-09

### Fixed

- `AnthropicFoundryReviewer` no longer mis-parses
  `ANTHROPIC_FOUNDRY_USE_ENTRA_ID` as a boolean: `bool(os.environ.get(...))`
  treated any non-empty string as `True`, so `ANTHROPIC_FOUNDRY_USE_ENTRA_ID=0`
  (an explicit attempt to disable Entra ID) silently enabled it instead and
  bypassed the configured API key. Fixed with a proper `_env_flag()` helper
  (`""`/`"0"`/`"false"`/`"no"`/`"off"`, case-insensitive, are false;
  everything else is true) and a parametrized regression test. Found during
  a self-review requested right after the 0.4.0 Foundry migration, before
  shipping it.
- Verified the `anthropic>=0.74.0` version floor directly: installed 0.74.0
  in an isolated venv and confirmed `AnthropicFoundry.__init__`'s keyword
  surface (`resource=`, `api_key=`, `azure_ad_token_provider=`) matches what
  this project calls it with, rather than relying only on it being the
  first release that added the class (previously verified only against the
  newer 1.4.0 installed in the build sandbox).
- Test count: 87, up from 80.

## [0.4.0] — 2026-09-09

### Changed

- `cli/` now talks to Claude **exclusively via Microsoft Foundry** (Azure
  AI Foundry) — there is no direct-to-`api.anthropic.com` code path.
  `AnthropicReviewer` is replaced by `AnthropicFoundryReviewer`, using the
  official `anthropic` package's `AnthropicFoundry` client class
  (requires `anthropic>=0.74.0`). Configuration: `ANTHROPIC_FOUNDRY_RESOURCE`
  (required) plus either `ANTHROPIC_FOUNDRY_API_KEY` (default) or
  `ANTHROPIC_FOUNDRY_USE_ENTRA_ID=1` for Entra ID (Azure AD) auth via the
  optional `azure-identity` dependency; each has a matching `--resource`/
  `--use-entra-id` CLI flag. `DEFAULT_MODEL` updated to `claude-sonnet-5`
  to match what's actually GA in Foundry's model catalog. All prompts
  (`CLAUDE.md`, the 7 specialist agents) are unchanged — Foundry's
  Messages API is Anthropic-compatible, so this was a client/auth change
  only, not a prompt-engineering one. 8 new tests
  (`cli/tests/test_agents_client.py`) cover the resource/API-key/Entra-ID
  construction-time validation; 80 tests total, up from 72.

## [0.3.0] — 2026-09-09

### Added

- `cli/`: a standalone, globally installable Python CLI (`agent-review`,
  `agent-init`) that runs this project's review philosophy against *any*
  git repository, independent of Claude Code. See `DESIGN.md` Section G
  for the full architecture and rationale. Highlights:
  - `agent-review [--path REPO] [--base REF] [--jobs N] [--fail-on-findings]`
    — reviews the diff between a base ref and the working tree. Routing is
    a zero-cost, deterministic local port of `triage-router.md`'s rule
    table (no model call spent on triage). Results are cached inside the
    target repo at `.agent-cache/manifest.json`, keyed by git blob hash
    plus the exact routed agent set, so an unchanged file is never
    re-reviewed.
  - `agent-review commit [--path REPO]` — generates a semantic commit
    message for the currently staged diff. Never adds a co-author or
    attribution trailer, by design — these are the user's own commits.
  - `agent-review heal [--path REPO] [--apply]` — runs the target repo's
    auto-detected test command (pytest, npm test, Maven, Gradle, cargo,
    ctest, or dotnet) and, on failure, asks the model for a root-cause
    explanation and a unified diff. Nothing is written to disk unless
    `--apply` is passed explicitly; the patch is always dry-run-checked
    (`git apply --check`) first and the tests are re-run afterward.
  - `agent-init [--path REPO]` — idempotently scaffolds a target repo with
    an editable `.agent-rules/` copy of the shared rules and specialist
    prompts, a gitignored `.agent-cache/`, and a starter `DESIGN.md`.
  - Prompt/config lookup checks `<repo>/.agent-rules/`, then
    `<repo>/.claude/` (so a repo already using the Claude-Code-native
    agents works unmodified), then falls back to a bundled snapshot.
  - 72 pytest tests, including full end-to-end tests through the actual
    `agent-review`/`agent-init` entry points against synthetic git repos
    with a fake in-memory model client (no network access needed to run
    the suite). Several genuine bugs were found and fixed this way — see
    `DESIGN.md` Section G, "What TDD actually caught."

### Known limitations of this release

- No live Anthropic API call has been made as part of building `cli/`
  (no key configured in the build environment) — its own real API client
  is exercised only up through construction-time key validation.
- `cli/` has not been installed or run on any machine outside the build
  environment as part of this change.

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
