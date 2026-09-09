# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
