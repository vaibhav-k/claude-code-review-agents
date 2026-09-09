---
name: New defect pattern or agent proposal
about: Propose a defect pattern this system doesn't catch yet, or a change to an agent's scope
title: ""
labels: enhancement
---

**What defect pattern is missing**

Describe the pattern and give a concrete code example it should catch.

**Which existing agent should own it**

Check `DESIGN.md` Section A and the agent files in `.claude/agents/` first
— most new patterns belong inside an existing specialist's scope rather
than requiring a new agent. Name the agent, or explain why none of the
seven fit.

**If you're proposing a new agent**

Read `CONTRIBUTING.md`'s "8-agent ceiling" section first. A new agent
proposal needs to say which existing agent it merges with or retires to
stay at 8 total — see it for the full requirement.

**Evidence bar**

Can this pattern clear the five-point evidence bar in `CLAUDE.md` (concrete
path, plausible trigger, causal link to the diff, meaningful impact,
specific fix)? If it can only be described as "might be a problem," it
likely isn't ready to become an agent rule yet.
