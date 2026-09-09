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
pip install -r requirements-dev.txt    # + pytest, ruff, mypy
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
- **Self-healing** (`healing.py`) is intentionally *not* autonomous:
  it runs the detected test command, and on failure asks the model for a
  diagnosis and a unified diff -- but never writes anything to disk
  unless you pass `--apply`, and even then only after confirming the
  patch applies cleanly.
- **Commit messages** (`commit.py`) summarize the currently staged diff
  and, per design, never add any co-author or attribution trailer --
  these are the user's own commits in the user's own repository.

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
