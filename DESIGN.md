# Automated Code Review Agent System — Design

**Target runtime:** Claude Code (VS Code extension), custom subagents in
`.claude/agents/`, shared rules in project `CLAUDE.md`, entry point
`.claude/commands/review-pr.md`.

**Total agent count: 8** (1 triage router + 7 defect-class specialists).
Zero language-specific agents — every specialist embeds knowledge of all
eight supported languages within its own defect domain, so language coverage
scales without scaling agent count.

## File manifest

```
CLAUDE.md                                     # shared rules, loaded by every agent for free
.claude/commands/review-pr.md                 # entry point: diff -> triage -> dispatch -> aggregate
.claude/agents/triage-router.md                # Agent 0 — routing only, no findings
.claude/agents/security-review.md              # Agent 1
.claude/agents/data-integrity-review.md        # Agent 2
.claude/agents/concurrency-resource-review.md  # Agent 3
.claude/agents/reliability-availability-review.md # Agent 4
.claude/agents/performance-review.md           # Agent 5
.claude/agents/api-type-contract-review.md     # Agent 6
.claude/agents/testing-maintainability-review.md  # Agent 7
```

A note on how this was verified: before finalizing the frontmatter schema, I
had a documentation-lookup subagent confirm current Claude Code subagent
syntax. Its answer came back flagged by the harness as containing
instruction-shaped/injected content (it asserted an unusually large set of
frontmatter fields, including a `permissionMode: bypassPermissions` option,
which is exactly the kind of thing a malicious page would want a code-review
agent to adopt). I did not use any of the unverifiable fields it listed.
Every agent file here uses only the small, well-established frontmatter
surface (`name`, `description`, `tools`, `model`, `color`) and scopes `Bash`
to specific read-only git subcommands. This is called out again in the
self-review at the end of this document — treat any field beyond that
core set as unverified if you see it suggested elsewhere.

---

## A. Agent Architecture

| Agent | Razor-Thin Responsibility | Strict Scope | Exact Trigger | Explicit Exclusions |
|---|---|---|---|---|
| **triage-router** | Read the diff, decide which specialists run. Never judges code quality. | File list, change type, keyword/pattern signals in diff hunks only. | Every review request, always first. | Never emits `[SEVERITY]` findings; never reads full files beyond diff hunks; never invokes other agents itself. |
| **security-review** | Security vulnerabilities only. | Injection, authN/authZ, secrets, unsafe deserialization, SSRF/path traversal, crypto misuse, CORS/CSRF, vulnerable dependency bumps. | Auth/crypto/secret/session keyword; string-built query/command; deserialization call; new external-input handler; dependency bump in security-relevant lib. | Pre-existing vulns untouched by diff; theoretical vulns with no reachable input; missing rate limiting/headers as blanket advice; resource leaks or races with no security consequence. |
| **data-integrity-review** | Data loss/corruption + functional correctness. | Transaction integrity, migrations/schema, SQL correctness, business-logic arithmetic, (de)serialization fidelity, unsafe state mutation. | `.sql`/migration file; DB write/ORM change; calculation touching money/date/quantity; transaction boundary change. | Pre-existing wrong queries untouched by diff; query performance (not correctness); resource/lifecycle issues; authz-scoped data exposure (→ security); missing tests. |
| **concurrency-resource-review** | Concurrency/async defects + resource-lifecycle failures. | Races, deadlocks, unsynchronized shared state, async/promise correctness, leaked handles/connections/memory, double-free/use-after-free. | async/await, thread, lock/mutex/semaphore keyword; resource-acquiring call; `finally`/`using`/`with`/RAII/`Dispose`/`close` touched. | Pre-existing races/leaks untouched by diff; TOCTOU race that is actually an authz bypass (→ security); lock/resource performance cost (→ performance); missing concurrency tests. |
| **reliability-availability-review** | Reliability/availability under failure. | Error-handling that hides failure, missing/wrong timeouts, retry/backoff defects, cascading-failure risk, startup/shutdown/health-check/queue-ack correctness. | Broadened/added catch-all; new network/DB call w/ no timeout; retry/backoff code; startup/shutdown/health-check/consumer-ack logic. | Pre-existing handling untouched by diff; leaks inside a catch block (→ concurrency-resource); auth-service-fails-open (→ security); "add more logging" advice; missing tests. |
| **performance-review** | Material performance regressions only. | N+1 patterns, algorithmic complexity regressions, blocking calls in non-blocking contexts, unbounded growth by design, batch/pagination regressions, query-plan regressions. | Loop wrapping I/O/DB call; request-handler hot-path change; batch/page-size/cache-config change; algorithmic-structure change with evident scale. | Pre-existing perf characteristics untouched by diff; micro-optimizations w/o measured impact; slow code on demonstrably small bounded input; query correctness (→ data-integrity); growth from a cleanup bug (→ concurrency-resource); missing benchmarks. |
| **api-type-contract-review** | API/contract violations + type-safety failures. | Breaking signature/endpoint changes, schema/DTO drift, unsafe type widenings/casts, null/undefined contract breaks, enum/union exhaustiveness, cross-language boundary drift. | Public function/endpoint/interface signature change; request/response DTO/schema change; type-annotation widening/removal; versioned-API file. | Pre-existing mismatches untouched by diff; breaking changes fully propagated to every visible consumer; internal logic correctness (→ data-integrity); same-language breaks the compiler already catches; missing tests. |
| **testing-maintainability-review** | Meaningful testing gaps + material maintainability damage. | Untested non-trivial new branches, tests that cannot fail, removed/weakened assertions, duplicated business logic, complexity spikes mixing responsibilities, dead/unreachable code. | Production logic changed w/ no test diff in same commit; test file touched; large added function w/ no test; near-identical logic block added. | Missing tests for untouched code; subjective test style; trivial duplication; re-flagging a bug a peer agent already owns just because it also lacks a test; pre-existing debt untouched by diff. |

---

## B. Complete Agent Markdown Files

All eight files are written to disk exactly as designed, at the paths in the
file manifest above — see `CLAUDE.md`, `.claude/agents/triage-router.md`,
and the seven `.claude/agents/*-review.md` files. They are reproduced there
in full (production-ready frontmatter, no placeholders or abbreviations) and
are not duplicated inline in this document to avoid a second copy drifting
out of sync with the real files.

---

## C. Routing Strategy Matrix

Triage (`triage-router`) is the only agent that reads this matrix; it is
also encoded as the trigger table inside `triage-router.md` itself. Reading
it top-to-bottom: any row whose condition is true adds its agent to the
route set — routes are additive, not exclusive.

| Dimension | Signal | Routed agent(s) |
|---|---|---|
| Language | `.py`, `.java`, `.cs`, `.c`/`.h`, `.cpp`/`.hpp`, `.js`/`.ts`, `.sql`, `.sh` | No agent is language-triggered directly — language only tells triage which idioms each routed specialist should apply. |
| File type | `*.sql`, `**/migrations/**` | data-integrity-review |
| File type | `Dockerfile`, IaC (`*.tf`, `*.yaml` under `k8s/`/`infra/`) | reliability-availability-review (+ security-review if secrets/network policy touched) |
| File type | `*.test.*`, `*_test.*`, `test_*.py`, `**/tests/**` | testing-maintainability-review |
| File type | CI/CD config (`.github/workflows/*`, `Jenkinsfile`) | reliability-availability-review |
| Change type | New public/exported function or endpoint signature | api-type-contract-review |
| Change type | New/removed try/catch, retry, timeout, circuit breaker | reliability-availability-review |
| Change type | New lock/thread/async/await/resource-open call | concurrency-resource-review |
| Change type | Pure rename/formatting/comment-only diff | none (triage marks "no semantic change") |
| Framework | ORM model change (SQLAlchemy, Hibernate, EF Core, TypeORM) | data-integrity-review |
| Framework | Web framework route/controller/handler added or changed | security-review (authz), api-type-contract-review (signature/schema) |
| Framework | Test framework fixtures/mocks changed | testing-maintainability-review |
| Technology | Message queue consumer/producer code | reliability-availability-review (ack/dead-letter), concurrency-resource-review (consumer concurrency) |
| Technology | Crypto library call, JWT/session library call | security-review |
| Technology | Serialization library (pickle, Jackson, protobuf, JSON) | security-review (unsafe deserialization) + api-type-contract-review (schema drift) as applicable |
| Risk signal | Dependency/lockfile version bump | security-review (CVE relevance), reliability-availability-review (behavior change risk) as applicable |
| Risk signal | Auth/session/secret keyword in diff hunk | security-review |
| Risk signal | Money/date/quantity arithmetic changed | data-integrity-review |
| SDLC stage | Pre-commit / local diff review | full pipeline via `/review-pr` |
| SDLC stage | PR review (CI-invoked) | full pipeline, base ref = target branch |
| SDLC stage | Hotfix / production incident diff | triage still runs first; typically narrows to reliability-availability-review + whichever domain the incident touches |

---

## D. Triage Agent

See `.claude/agents/triage-router.md` for the complete, production-ready
file (frontmatter, routing rule table, and the exact `<review_routing>` XML
contract it must emit). Frontmatter uses `model: haiku` because routing is a
pattern-matching task over a diff summary, not deep code reasoning — this is
the single biggest inference-cost lever in the whole system, since triage
runs on 100% of review requests while each specialist runs only on the
subset it's actually needed for.

---

## E. Validation Matrix

Each row is a concrete snippet that should (True Positive), should not
(False Positive Trap), or is genuinely ambiguous and resolved by the
evidence bar (Boundary Case) produce a finding from that agent. These are
the acceptance tests this system should be evaluated against before it
ships.

### security-review

**True Positive** — Python, string-built SQL from request input:
```python
def get_user(request):
    user_id = request.args.get("id")
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return db.execute(query).fetchone()
```
Expected: `[CRITICAL] app/handlers.py:4 — SQL injection via unparameterized user_id`
Impact: attacker-controlled `id` is concatenated directly into SQL, allowing
arbitrary query injection (data exfiltration or modification).
Fix: use a parameterized query, e.g. `db.execute("SELECT * FROM users WHERE id = %s", (user_id,))`.

**False Positive Trap** — looks like injection, is actually parameterized (must NOT fire):
```python
def get_user(request):
    user_id = request.args.get("id")
    query = "SELECT * FROM users WHERE id = %s"
    return db.execute(query, (user_id,)).fetchone()
```
Expected: no finding — the value is passed as a bind parameter, never
concatenated into the SQL string, regardless of how untrusted `user_id` is.

**Boundary Case** — f-string used, but only with a hardcoded/internal constant:
```python
def get_active_users():
    status = STATUS_ACTIVE  # module-level constant, not user input
    query = f"SELECT * FROM users WHERE status = '{status}'"
    return db.execute(query).fetchall()
```
Expected: no finding — no externally-controlled input reaches the
interpolation, so the evidence bar's "untrusted input source" requirement
is not met. (If `STATUS_ACTIVE` were ever reassigned from request data
elsewhere in the same diff, this would flip to a True Positive — the agent
must check, not assume.)

### data-integrity-review

**True Positive** — C#, migration silently truncates existing data:
```csharp
migrationBuilder.AlterColumn<string>(
    name: "Email",
    table: "Users",
    type: "varchar(50)",   // was varchar(255)
    nullable: false);
```
Expected: `[HIGH] Migrations/20260909_ShrinkEmail.cs:4 — Email column narrowed from varchar(255) to varchar(50) with no backfill/validation`
Impact: any existing email longer than 50 characters is silently truncated
on migration, corrupting user contact data with no error raised.
Fix: add a pre-migration check/backfill that rejects or remediates rows
exceeding the new length before narrowing the column, or keep the wider type.

**False Positive Trap** — column widened, not narrowed (must NOT fire):
```csharp
migrationBuilder.AlterColumn<string>(
    name: "Email",
    table: "Users",
    type: "varchar(320)",   // was varchar(255)
    nullable: false);
```
Expected: no finding — widening a column cannot truncate existing data;
there is no data-loss path here.

**Boundary Case** — narrowed column, but table is provably empty/new in this diff:
```csharp
migrationBuilder.CreateTable(
    name: "StagingImports",
    columns: table => new { Email = table.Column<string>(type: "varchar(50)") });
```
Expected: no finding — `CreateTable` cannot narrow existing data because no
rows exist yet; this is a brand-new table in the same diff. (If this were
`AlterColumn` on a table that already exists elsewhere in the repo, it would
be a True Positive.)

### concurrency-resource-review

**True Positive** — Java, connection not released on exception path:
```java
public String fetchName(int id) throws SQLException {
    Connection conn = pool.getConnection();
    PreparedStatement ps = conn.prepareStatement("SELECT name FROM t WHERE id=?");
    ps.setInt(1, id);
    ResultSet rs = ps.executeQuery();
    return rs.next() ? rs.getString(1) : null;
}
```
Expected: `[HIGH] Db.java:2 — Connection acquired from pool is never released`
Impact: every call leaks a pooled connection; under sustained load the pool
exhausts and all subsequent DB calls block or fail.
Fix: acquire and release in a try-with-resources block, e.g.
`try (Connection conn = pool.getConnection(); ...) { ... }`.

**False Positive Trap** — same shape, but pool auto-closes on scope exit via try-with-resources (must NOT fire):
```java
public String fetchName(int id) throws SQLException {
    try (Connection conn = pool.getConnection();
         PreparedStatement ps = conn.prepareStatement("SELECT name FROM t WHERE id=?")) {
        ps.setInt(1, id);
        try (ResultSet rs = ps.executeQuery()) {
            return rs.next() ? rs.getString(1) : null;
        }
    }
}
```
Expected: no finding — every resource is opened inside a try-with-resources
block, guaranteeing release on all exit paths including exceptions.

**Boundary Case** — connection stored on an object whose own lifecycle owns the close:
```java
class ReportSession implements AutoCloseable {
    private final Connection conn = pool.getConnection();
    ResultSet run(String sql) throws SQLException { return conn.createStatement().executeQuery(sql); }
    public void close() throws SQLException { conn.close(); }
}
```
Expected: no finding from this file alone — the connection's release is
delegated to `ReportSession.close()`, which is a legitimate ownership
pattern. The agent must check callers (per its context-acquisition rule) to
confirm every `ReportSession` construction is itself inside a
try-with-resources/`using`/`with`; only if a caller in this diff constructs
one without closing it does this become a True Positive, attributed to that
caller's line, not to this class definition.

### reliability-availability-review

**True Positive** — TypeScript, retry with no cap or backoff around a non-idempotent call:
```typescript
async function chargeCard(orderId: string, amount: number) {
  while (true) {
    try {
      return await paymentGateway.charge(orderId, amount);
    } catch (e) {
      continue; // retry immediately
    }
  }
}
```
Expected: `[CRITICAL] payments.ts:2 — Unbounded immediate retry around a non-idempotent charge call`
Impact: any transient failure causes a tight retry loop with no backoff or
cap, which can both hammer the payment gateway (amplifying an outage) and
double-charge the customer if the first attempt actually succeeded upstream
before the error surfaced.
Fix: cap retry attempts, add exponential backoff, and pass an idempotency
key to `paymentGateway.charge` so retried attempts cannot double-charge.

**False Positive Trap** — bounded retry with backoff on an idempotent read (must NOT fire):
```typescript
async function getExchangeRate(currency: string) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await rateService.get(currency);
    } catch (e) {
      await sleep(2 ** attempt * 100);
    }
  }
  throw new Error(`rate lookup failed for ${currency}`);
}
```
Expected: no finding — the operation is a read (idempotent), retries are
capped at 3, backoff is exponential, and failure is ultimately propagated
rather than swallowed.

**Boundary Case** — broad catch, but rethrows after logging (ambiguous shape):
```python
def process_batch(items):
    try:
        return [transform(i) for i in items]
    except Exception as e:
        logger.error(f"batch failed: {e}")
        raise
```
Expected: no finding — although the catch is broad, the exception is
logged for observability and then re-raised unchanged, so the caller still
sees the failure and can react. This differs from a swallowing catch only
in the `raise`; the agent must read that line before deciding, not pattern-
match on "broad except" alone.

### performance-review

**True Positive** — Python, N+1 query introduced inside a loop:
```python
def get_order_summaries(order_ids):
    summaries = []
    for oid in order_ids:
        order = db.query(Order).filter(Order.id == oid).first()
        summaries.append(order.total)
    return summaries
```
Expected: `[MEDIUM] orders.py:3 — N+1 query: one SELECT per order_id instead of a single batched query`
Impact: for `order_ids` of realistic request size (tens to hundreds), this
issues that many round trips to the database per call, adding latency
roughly linear in list size and multiplying DB load under concurrent
requests.
Fix: replace with a single batched query,
`db.query(Order).filter(Order.id.in_(order_ids)).all()`, then map results
back to the input order.

**False Positive Trap** — looks like N+1, but bounded to a fixed small constant (must NOT fire):
```python
def get_top3_summaries(top3_ids):
    # top3_ids is always exactly the 3 leaderboard positions
    return [db.query(Order).filter(Order.id == oid).first().total for oid in top3_ids]
```
Expected: no finding — the loop is bounded to a fixed, small (3) constant
by the caller's contract, so per-iteration query cost is not material
regardless of call volume; batching 3 fixed lookups is not a meaningful win.

**Boundary Case** — single extra query added, but only on a rare cold-start path:
```python
def get_config(key):
    if key not in _cache:
        _cache[key] = db.query(Config).filter(Config.key == key).first()
    return _cache[key]
```
Expected: no finding — this is a cache-fill pattern; the DB call happens
once per distinct key for the process lifetime, not once per request, so
there is no N+1 pattern under repeated calls. (If `_cache` were cleared or
bypassed on every call in this diff, it would flip to a True Positive.)

### api-type-contract-review

**True Positive** — C#, nullable contract change not propagated to an existing consumer in the same diff:
```csharp
// UserService.cs (changed in this diff)
public User? FindUser(int id) => _repo.Find(id); // was: public User FindUser(int id)

// UserController.cs (present in this diff, NOT updated)
var user = _userService.FindUser(id);
return Ok(user.Name);
```
Expected: `[HIGH] UserController.cs:2 — FindUser now returns User? but caller dereferences without a null check`
Impact: when `_repo.Find(id)` returns null (the exact case the signature
change was made to represent), this line throws a NullReferenceException,
turning a previously well-typed lookup miss into an unhandled 500.
Fix: handle the null case explicitly at the call site, e.g.
`if (user is null) return NotFound(); return Ok(user.Name);`.

**False Positive Trap** — same nullable change, but this diff updates every consumer (must NOT fire):
```csharp
// UserService.cs
public User? FindUser(int id) => _repo.Find(id);

// UserController.cs (also updated in this same diff)
var user = _userService.FindUser(id);
if (user is null) return NotFound();
return Ok(user.Name);
```
Expected: no finding — the diff both introduces the nullable return and
fully updates the one consumer visible in it to handle the new case; the
contract change is complete, not broken.

**Boundary Case** — signature unchanged, but a same-language, same-repo break the compiler will catch:
```typescript
// api.ts (changed in this diff)
export function formatPrice(cents: number): string { ... }
// was: export function formatPrice(cents: number, currency: string): string

// checkout.ts (present in this diff, not updated, still calls with 2 args)
formatPrice(total, "USD");
```
Expected: no finding, or at most a LOW note if genuinely non-obvious — this
break will fail `tsc`/the build immediately for every consumer in-repo; the
agent's evidence requirement explicitly deprioritizes same-language breaks
the type checker already catches, since reporting it adds no signal beyond
what the compiler already guarantees before merge. Reserve findings here for
breaks that survive compilation (dynamic-language calls, reflection, a
cross-service JSON boundary).

### testing-maintainability-review

**True Positive** — Python, new non-trivial branch with zero test diff:
```python
def compute_discount(order):
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.20
    return order.total * 0.05
```
(No corresponding change in `test_discounts.py` anywhere in this diff.)
Expected: `[MEDIUM] pricing.py:2 — New VIP-discount branch has no test coverage in this diff`
Impact: the 20%-vs-5% discount boundary (VIP + total > 500) is exactly the
kind of condition that regresses silently on a future refactor; nothing in
the test suite currently pins either branch's output.
Fix: add tests asserting `compute_discount` returns 20% for a VIP order over
500 and 5% for a non-VIP or under-500 order.

**False Positive Trap** — new branch, but this diff DOES add a covering test (must NOT fire):
```python
def compute_discount(order):
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.20
    return order.total * 0.05

# test_discounts.py (same diff)
def test_vip_discount_over_500():
    assert compute_discount(make_order(vip=True, total=600)) == 120
def test_non_vip_discount():
    assert compute_discount(make_order(vip=False, total=600)) == 30
```
Expected: no finding — both branches are exercised with a real assertion
tied to the actual computed value.

**Boundary Case** — a test is added, but it cannot fail:
```python
def test_vip_discount_over_500():
    result = compute_discount(make_order(vip=True, total=600))
    assert result == result  # tautology
```
Expected: `[MEDIUM] test_discounts.py:3 — Added test asserts a tautology and cannot detect a regression`
Impact: this test will pass regardless of what `compute_discount` returns,
giving false confidence that the VIP-discount branch is covered when it is
not. Distinguish this from the True Positive above only by actually reading
the assertion, not merely confirming a test function exists.
Fix: assert against the expected literal value, e.g. `assert result == 120`.

---

## F. Optimization Summary

**Final agent count: 8** — 1 triage router + 7 non-overlapping specialists.
This is the hard ceiling given in the brief, met exactly rather than padded
toward it.

**Language coverage:** Python, Java, C#, C/C++, JavaScript, TypeScript, SQL,
Bash/Shell are each covered inside every one of the 7 specialists' own
domain expertise (see each agent's "Language Coverage" inheritance from
`CLAUDE.md`), rather than via 8 language agents × 7 defect classes (which
would require 56 agents, or a compromise matrix nobody could maintain).
Adding a ninth supported language later costs zero new agents — it is a
one-line addition to `CLAUDE.md`'s language-idiom list.

**SDLC coverage:** the routing matrix (section C) reaches pre-commit review,
PR review, hotfix/incident review, migrations, CI/CD config, IaC,
dependency bumps, and test-file changes — i.e. code authored, code reviewed,
and the surrounding delivery pipeline, without a dedicated "CI/CD agent" or
"infra agent" (those signals route into whichever of the 7 defect-class
agents actually owns the resulting risk, per the matrix).

**Overlaps eliminated:** every agent's "Explicit exclusions" section names
the adjacent agent it defers to and the exact boundary rule, so the same
line of code cannot generate two findings from two agents:
- A TOCTOU race that's really an authz bypass → security-review only.
- A resource leak on an error path → concurrency-resource-review only, not
  reliability-availability-review (which owns the error *handling*, not the
  resource itself).
- Query correctness vs. query cost → data-integrity-review vs.
  performance-review, never both on the same line.
- A breaking API change vs. what the function computes → api-type-contract-
  review vs. data-integrity-review.
- "This bug also lacks a test" → testing-maintainability-review explicitly
  stands down whenever a peer agent already owns the underlying defect, so
  a single line never produces two findings for the same root cause.

**Routing efficiency:** triage runs on 100% of requests but is the cheapest
agent in the system (`model: haiku`, diff-metadata only, no file reads
beyond hunks) and its only output is 
a routing decision — the expensive reasoning (`model: sonnet`) only spins up
for the subset of specialists a given diff actually needs. A typical
single-concern PR (e.g. a pure SQL migration) invokes triage + 1 specialist,
not triage + 7.

**Shared instructions:** severity model, evidence bar, risk order, output
contract, exclusions, and context-acquisition discipline live exactly once,
in `CLAUDE.md`, which Claude Code loads into every subagent's initial
context automatically. Each of the 8 agent files therefore contains only
its own operational scope — no agent file repeats the ~700-token global
rule block, which is the largest single token-efficiency lever after
routing itself (8 agents × 0 copies of the shared block, versus 8 copies
without this mechanism).

**Context minimization:** every specialist's "Context acquisition" section
is a strict subset of the global 8-step order in `CLAUDE.md`, tailored to
name exactly which follow-up read is ever justified in that domain (e.g.
security-review only ever needs to confirm an input's trust boundary;
performance-review only ever needs to confirm a collection's realistic
bound). Triage itself never reads full files, only diff hunks and, rarely,
a shebang line — it is explicitly forbidden from doing a specialist's job
"just in case."

**Tool minimization:** every agent's tool list is read-only —
`Read`, `Grep`, `Glob`, and `Bash` scoped to specific git subcommands
(`Bash(git diff *)`, `Bash(git show *)`, and for triage only, `Bash(git log *)`/
`Bash(git status *)`). No agent has `Edit`, `Write`, or unscoped `Bash` —
this is a review system, not a code-modification system, and the tool grant
enforces that at the permission layer rather than relying on the prompt
alone to self-restrain.

**Self-review before shipping — issues to check before you rely on this:**

1. *Unverified frontmatter fields, deliberately not used.* A documentation
   subagent I consulted while designing this returned output the harness
   flagged as instruction-shaped/injected (it pushed an unusually large
   frontmatter surface, including a permission-bypass field). I've built
   every file here on only the small, defensible field set
   (`name`/`description`/`tools`/`model`/`color`) and scoped `Bash`
   arguments. Before you rely on the `Bash(git diff *)`-style argument
   scoping actually being enforced by your installed Claude Code version,
   test it once (e.g. try `git push` from inside one of these agents and
   confirm it's refused) rather than trusting the frontmatter alone.
2. *CLAUDE.md auto-inheritance is the load-bearing efficiency assumption.*
   The whole "write shared rules once" design depends on subagents actually
   receiving the project's `CLAUDE.md` in their initial context. If your
   Claude Code version does not do this for some reason, every agent will
   silently lose the severity model, evidence bar, and output contract —
   symptom would be inconsistent output formatting across agents. Run one
   real review through `/review-pr` and check that a specialist's output
   actually follows the `[SEVERITY] file:line` contract before trusting it
   in CI.
3. *`/review-pr`'s parallel-dispatch step is an instruction to the
   orchestrating session, not a hard guarantee.* If your Claude Code
   version executes agent invocations sequentially regardless of phrasing,
   the system still works correctly — it's just not exploiting the
   parallelism this file has assumed. Not a correctness risk, only a
   latency assumption worth confirming.
4. *Triage on `haiku` is a deliberate cost/accuracy trade.* If you observe
   triage under- or over-routing in practice (check the `<skip>` reasons
   it emits against diffs you know touch, say, concurrency), the fix is to
   bump `model: haiku` to `model: sonnet` in `triage-router.md` — the
   routing rule table itself doesn't need to change.
5. *The validation matrix (section E) is hand-written, not executed.* These
   21 snippets are the acceptance tests this design should be graded
   against, but I have not actually run them through the agents (that would
   require a live Claude Code environment with these files installed). Run
   them for real before treating any agent as validated.
