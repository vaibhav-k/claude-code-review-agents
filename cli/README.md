# agent-review (CLI)

A standalone, globally installable command-line tool that runs the same
evidence-based, diff-scoped code review defined in this project's
`.claude/agents/` against **any** git repository on disk -- not just from
inside Claude Code, and not just this repository.

It talks to Claude exclusively via **Microsoft Foundry** (Azure AI
Foundry) -- there is no direct-to-api.anthropic.com code path, and no
Claude Code installation required to run it. It routes each changed file
to the right specialist review "agent" with zero extra model calls
(routing is a deterministic, local pattern match -- see
`src/agent_review/routing.py`), and caches per-file results locally
inside the target repo so a second run with no relevant changes costs
zero API calls.

## Install

```bash
cd cli
pip install -e .
```

Then configure Microsoft Foundry access -- a resource name plus one of
two auth modes, and optionally which model to use:

```bash
# bash / zsh / Linux & macOS terminals
export ANTHROPIC_FOUNDRY_RESOURCE=your-foundry-resource-name

# Auth option 1: API key (default)
export ANTHROPIC_FOUNDRY_API_KEY=...

# Auth option 2: Entra ID (Azure AD) instead of a static key -- requires
# the optional azure-identity dependency (pip install azure-identity, or
# pip install -e ".[azure-ad]"); whichever account `DefaultAzureCredential`
# resolves (Azure CLI login, managed identity, etc.) must have access to
# the Foundry resource.
export ANTHROPIC_FOUNDRY_USE_ENTRA_ID=1

# Optional: skip passing --model every time. Default: claude-sonnet-5.
export ANTHROPIC_FOUNDRY_MODEL=claude-sonnet-5
```

```powershell
# PowerShell (Windows) -- `export` above is bash syntax and will not work here
$env:ANTHROPIC_FOUNDRY_RESOURCE = "your-foundry-resource-name"

# Auth option 1: API key (default)
$env:ANTHROPIC_FOUNDRY_API_KEY = "..."

# Auth option 2: Entra ID (Azure AD) -- see the bash block above for the
# azure-identity dependency and DefaultAzureCredential note
$env:ANTHROPIC_FOUNDRY_USE_ENTRA_ID = "1"

# Optional: skip passing --model every time. Default: claude-sonnet-5.
$env:ANTHROPIC_FOUNDRY_MODEL = "claude-sonnet-5"
```

**The model -- whether set via `ANTHROPIC_FOUNDRY_MODEL` or `--model` --
must be a model actually *deployed* under that resource**, not just any
valid Claude model ID. Some Foundry models don't support "deploymentless
inference" (calling a bare model ID with nothing deployed under that name
in your resource); calling one that isn't deployed fails with a
`DeploymentError`, not a helpful "model not found." Check the Foundry
portal (**Discover → Models** → your deployment → **Details** tab) for
the exact deployment name if you're not sure what's actually deployed.

Either of these only lasts for the current shell session -- set it again
next time you open a new terminal, or persist it with your OS's usual
mechanism (`setx` on Windows, a shell profile file on bash/zsh) if you'd
rather not repeat it.

Any of these can also be set per-invocation with `--resource` /
`--use-entra-id` / `--model` instead of environment variables -- an
explicit flag always overrides the matching environment variable. This
installs two commands: `agent-review` and `agent-init`.

`.env.example` documents the same variables as a copy-paste template.
`agent-review`/`agent-init` load `.env` automatically (via
`python-dotenv`, searching upward from wherever you run the command --
never downward into subdirectories, and never overriding a variable
already set for real):

```bash
cp .env.example .env    # then fill in real values -- .env is gitignored
```

**Put `.env` in the directory you actually run `agent-review`/`agent-init`
from** (or any parent of it) -- commonly this repo's own root if you
followed the "Quick start" section below, since that's where you `cd`
and activate the virtualenv before invoking either command. A `.env`
left inside `cli/` (where this template file itself lives) is invisible
to a command run from the repo root, since the search only ever looks
upward from your current directory, never down into `cli/`.

`requirements.txt` / `requirements-dev.txt` are provided as an alternative
to `pip install -e .` for tooling that expects a requirements file (CI
systems, Docker base images, dependency scanners); they mirror
`pyproject.toml`'s `dependencies` / `optional-dependencies.dev` and don't
register the console scripts on their own:

```bash
pip install -r requirements.txt        # runtime only
pip install -r requirements-dev.txt    # + pytest, ruff, mypy, and the rest of pyproject.toml's dev extra
```

### If your editor shows "Import agent_review could not be resolved"

`cli/tests/*.py` import `agent_review` after a `sys.path.insert(...)` that
adds `cli/src` at runtime -- this works for `pytest` (which executes that
line before the import), but an editor's static analyzer (Pylance/Pyright
in VS Code) doesn't execute code, so it can't see it. Two independent
fixes, do both:

1. **Make the import real, not just visible to the type checker.** Run
   `pip install -e ".[dev]"` from `cli/` (see above) in the *same*
   interpreter/virtualenv your editor is using, then make sure that's the
   interpreter selected (in VS Code: "Python: Select Interpreter"). This
   is the fix that also makes `pytest`, `ruff`, and `mypy` actually work
   from a plain terminal.
2. **Tell the type checker where `cli/src` is, independent of step 1.**
   The repo root ships a `pyrightconfig.json` with
   `"extraPaths": ["cli/src"]` for exactly this reason -- Pylance/Pyright
   picks it up automatically once the *workspace root* is the repo root
   (not `cli/` itself). If VS Code has `cli/` open as its own workspace
   folder instead of the repo root, either open the repo root instead, or
   copy `extraPaths: ["src"]` into a `cli/pyrightconfig.json`.

If both are in place and the warning persists, reload the editor window
(stale language-server state after installing a package is common) rather
than assuming the code is wrong -- a clean `pip install -e ".[dev]"` in a
brand-new virtualenv plus `pyright .` from the repo root is the ground
truth for whether the imports actually resolve.

## Quick start

```bash
# One-time: scaffold a target repo with editable prompts, a gitignored
# cache directory, and a starter DESIGN.md.
agent-init --path /path/to/some/repo

# Review the diff between HEAD and the repo's default base ref.
agent-review --path /path/to/some/repo

# Same thing, explicit subcommand + explicit base ref + CI-friendly exit code.
agent-review review --path /path/to/some/repo --base origin/main --fail-on-findings

# Cap how many routed files get reviewed in one run (a pathologically wide
# diff -- a huge rename, generated content -- skips the rest rather than
# blowing the budget; skipped files are reported, not silently dropped,
# and get picked up on a later run once the file set fits under the cap).
agent-review review --path /path/to/some/repo --max-files 50

# Emit a SARIF 2.1.0 log instead of human-readable text -- pipe this to a
# file and upload it with github/codeql-action/upload-sarif (or your CI's
# equivalent) for inline PR annotations and a persistent alerts list.
# Mutually exclusive with --json (see cli.build_review_parser).
agent-review review --path /path/to/some/repo --sarif > results.sarif

# Persist today's findings as a baseline (creates .agent-review/baseline.json).
agent-review review --path /path/to/some/repo --update-baseline

# On later runs: classify against that baseline, show only unseen findings,
# and fail CI only when one of those NEW findings is high severity or above.
agent-review review --path /path/to/some/repo --baseline .agent-review/baseline.json --new-only --fail-on high

# Generate a commit message for what's currently staged (no co-author trailer).
agent-review commit --path /path/to/some/repo

# Run the repo's detected test suite; on failure, propose a fix (never
# applied automatically).
agent-review heal --path /path/to/some/repo
agent-review heal --path /path/to/some/repo --apply   # writes the patch, then reruns tests
```

## How it works

- **Routing** (`routing.py`) -- a direct, zero-cost port of
  `.claude/agents/triage-router.md`'s rule table: which specialist(s) a
  changed file needs is decided by local pattern matching, not a model
  call.
- **Caching** (`cache.py`) -- `.agent-cache/manifest.json` inside the
  target repo, keyed by each file's git blob hash plus the exact set of
  agents routed to it. Unchanged files are skipped entirely on the next
  run.
- **Prompts** (`prompts.py`) -- looks for `.agent-rules/` in the target
  repo first, then `.claude/agents/` (so a repo already using the
  Claude-Code-native agents works as-is), then falls back to the copies
  bundled with this package.
- **Suppressing false positives** (`suppressions.py`) -- an optional
  `.claude/ignore-findings.yml` in the target repo lets a team mark a
  specialist's finding as an accepted false positive or accepted risk,
  matched by agent name (or `"*"` for any agent) and a glob against the
  finding's `file:line` location, not an exact line number (those drift).
  Suppressed findings drop out of the report but are still logged to
  `.agent-cache/suppressions.log`, and a summary line always says how
  many were suppressed and why -- silent suppression would just trade one
  kind of blind spot for another. Suppression is applied AFTER parsing and
  is independent of the cache: the cache always stores the raw,
  unsuppressed finding, so editing `ignore-findings.yml` takes effect on
  the very next run with no cache invalidation needed.
- **Structured-output guards** (`findings.py`) -- a specialist's response
  is parsed against the exact `[SEVERITY] file:line` contract every agent
  is instructed to follow; `is_malformed_response()` catches a response
  that doesn't match it (a hedge, a code-fenced answer, stray prose) and
  the CLI surfaces it as an explicit warning naming the file and agent,
  rather than silently treating an unparseable response as "no finding" --
  a parsing failure and a clean verdict are different things and the
  output says which one happened.
- **Diff/file budget** (`orchestrator.py`) -- a single file's diff beyond
  `MAX_DIFF_LINES` (4000) is truncated before being sent to any specialist,
  with the response and CLI output both saying so rather than silently
  reviewing less than the file actually changed; a `--max-files` cap drops
  the tail of an oversized file list before any model call is made for
  those files, reported as `skipped_for_budget` rather than silently
  omitted. Both exist so a huge changeset degrades predictably instead of
  blowing past context limits or an unbounded API bill.
- **Self-healing** (`healing.py`) is intentionally *not* autonomous:
  it runs the detected test command, and on failure asks the model for a
  diagnosis and a unified diff -- but never writes anything to disk
  unless you pass `--apply`, and even then only after confirming the
  patch applies cleanly.
- **Commit messages** (`commit.py`) summarize the currently staged diff
  and, per design, never add any co-author or attribution trailer --
  these are the user's own commits in the user's own repository.

## Finding lifecycle and CI policy

Everything below is additive -- run `agent-review` with none of these
flags and you get exactly the pre-0.11.0 behavior.

- **Rule IDs** (`rules.py`) -- every finding gets a stable, deterministic
  ID like `SEC-INJECTION-001` or a domain-level fallback like
  `DATA-GENERAL-001`, derived from the diff text itself (never from the
  model's own wording, so the same defect always gets the same ID).
  Exposed in `--json` (`rule_id`) and as SARIF's `ruleId`. See
  `DESIGN.md`'s "Finding lifecycle and CI policy" section for the
  category table and its one documented limitation (one rule ID per
  file+agent, not per individual finding).
- **Fingerprints** (`fingerprint.py`) -- a sha256 of the rule ID, the
  repo-relative path, and a normalized title, deliberately excluding
  line number and the model's free-form `impact`/`fix` prose. This is
  what lets the same real-world defect be recognized as "the same
  finding" across two runs even after an unrelated edit moves its line
  number. It is **not** a semantic identity -- see DESIGN.md for the
  full, honest list of what it can and can't tell apart.
- **Baseline** (`--baseline PATH`, `--update-baseline`) -- a
  version-controlled JSON snapshot of previously-seen findings
  (`.agent-review/baseline.json` by default). `--update-baseline`
  explicitly writes/replaces it from this run's findings; a normal run
  (even with `--baseline` for reading) never writes it. Every finding
  is then classified `new` or `existing` against it.
- **`--new-only`** -- with a baseline, shows/evaluates only `new`
  findings. With no baseline, every finding is already `new`, so this
  is a harmless no-op.
- **`--fail-on SEVERITY[,SEVERITY...]`** -- exits non-zero if any
  finding in the (baseline/`--new-only`-filtered) display set is at or
  above the given severity. Independent of the older
  `--fail-on-findings`, which still means "fail if anything was found
  at all," unchanged.

```bash
# First run on a repo with an existing backlog: snapshot it once so CI
# only ever gates on what's introduced from here on.
agent-review review --path . --update-baseline

# Every PR after that: only fail if a genuinely NEW finding is HIGH+.
agent-review review --path . --baseline .agent-review/baseline.json --new-only --fail-on high

# Refresh the baseline once the backlog above has actually been fixed
# (or to intentionally accept today's findings as the new normal).
agent-review review --path . --update-baseline
```

`--baseline`/`--update-baseline`/`--new-only`/`--fail-on` all work with
`--json` and `--sarif` too: `--json` adds `rule_id`, `fingerprint`, and
(when a baseline was used) `status` per finding, plus a top-level
`baseline` object; `--sarif` adds a stable `ruleId`/`partialFingerprints`
and SARIF's own `baselineState` per result.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
python -m mypy .        # run from the repo root -- see root mypy.ini
python -m ruff check .  # run from the repo root -- see root ruff.toml
```

## Limitations

- Requires a Microsoft Foundry resource with a Claude deployment and
  valid credentials (API key or Entra ID); there is no offline/no-key
  mode for real reviews (tests use a fake in-memory `Reviewer` instead),
  and no direct-to-Anthropic-API fallback -- this project uses Azure only.
- Auto-discovered test runners (`discovery.py`) are best-effort,
  file-presence heuristics (e.g. `pyproject.toml` implies `pytest`); if a
  detected command isn't actually installed, `agent-review heal` reports
  that clearly rather than guessing further.
- Fingerprints are **not a semantic identity** -- they can't tell two
  different findings apart if they land on the same rule ID, path, and
  a title that normalizes the same way, and they treat any reworded
  title (beyond case/whitespace/punctuation) as a brand-new finding even
  if a person would call it identical. See DESIGN.md for the full list
  of documented tradeoffs behind this design.
- Rule IDs are derived once per file per agent (from that file's diff
  text), not once per individual finding -- two structurally different
  findings from the same specialist in the same file currently share a
  rule ID.
