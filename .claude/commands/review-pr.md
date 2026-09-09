---
description: Run the full automated review pipeline (triage -> specialist agents -> aggregated findings) against the current diff.
argument-hint: "[base-branch-or-commit]"
---

Run the automated review pipeline against the diff between the working tree
(or `HEAD`, if the tree is clean) and `$1` (default `origin/main` if `$1` is
not given).

Follow these steps exactly, in order:

1. Invoke the `triage-router` agent. Pass it the base ref to diff against
   (`$1` or `origin/main`). Do not do any diff inspection yourself first —
   triage owns that.
2. Parse the `<review_routing>` XML the triage agent returns. Collect every
   `<route agent="...">` entry and, for each, the set of `<file>` entries
   whose `agents` attribute lists that agent.
3. For each routed agent, invoke it once, in parallel with the other routed
   agents (they are independent — invoke them together, not one after
   another). Pass each agent only its own file list from step 2's `<file>`
   entries and the base ref, so it scopes its own `git diff` to those files
   rather than re-deriving the full diff.
4. Collect every agent's returned findings (or its "No high-impact issues
   found." line).
5. Merge all findings into one list, sorted by severity (CRITICAL, HIGH,
   MEDIUM, LOW) and then by file path, with no reformatting of individual
   findings — pass each `[SEVERITY] file:line — ...` block through exactly
   as the specialist wrote it.
6. Print the merged list under a `## Review Findings` heading. If every
   agent returned "No high-impact issues found.", print exactly
   `No high-impact issues found.` and nothing else — do not print the
   per-agent skip reasons or routing rationale unless the user asks for it.
7. If any agent was skipped by triage (`<skip>`), do not mention skipped
   agents in the default output — only surface routing detail if the user
   explicitly asks "why wasn't X reviewed" or similar, in which case answer
   from the triage agent's `<skip reason="...">` entries.

Do not re-review anything yourself outside of orchestrating these agents —
your role here is dispatch and aggregation only, not a second opinion on the
code.
