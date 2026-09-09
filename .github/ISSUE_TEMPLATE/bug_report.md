---
name: False positive / false negative report
about: An agent reported something it shouldn't have, missed something it should have caught, or fired outside its documented scope
title: "[agent-name] short description"
labels: bug
---

**Which agent**

e.g. `security-review`

**Diff or snippet that triggered (or should have triggered) a finding**

```
paste the relevant diff hunk or a minimal reproduction here
```

**What the agent reported (or didn't report)**

Paste the exact output, or "No high-impact issues found." if that's the
problem.

**What you expected**

State the expected `[SEVERITY] file:line — ...` finding, or confirm nothing
should have been reported and say why the actual output was wrong.

**Which rule this violates**

Check the agent's file in `.claude/agents/` — does this fall under a
documented trigger condition it missed, an exclusion it should have
respected, or a boundary with another agent (see that agent's "Explicit
exclusions" section)? Naming the specific rule makes this much faster to
fix.
