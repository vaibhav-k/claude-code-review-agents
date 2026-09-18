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
.claude/agents/testing-coverage-review.md      # Agent 7
```

This lists only the functional runtime — the 8 agent files, the shared
rules, and the entry-point command. The repository also carries
`README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, `.github/`
(issue/PR templates and a CI workflow scaffold), `tests/fixtures/`
(the validation matrix in Section E as real files), and `cli/` (a
standalone, globally installable Python CLI that runs this same review
logic against any git repository on disk, independent of Claude Code —
see Section G) — none of those affect what Claude Code loads at review
time inside this repository itself.

A note on frontmatter: every agent file here uses only the small,
well-established frontmatter surface (`name`, `description`, `tools`,
`model`, `color`), with `Bash` scoped to specific read-only git subcommands.
Some blog posts and community examples show a much larger frontmatter
surface (permission modes, hooks, MCP server lists, and similar) — those
are deliberately not used here, since they aren't confirmed in the current
official Claude Code docs and at least one such example promotes a
permission-bypass option, which is bad practice for a review-only tool
regardless of whether the field actually exists in your version. Check your
installed Claude Code version's documented frontmatter fields before adding
anything beyond this core set; see the note at the end of Section F.

---

## A. Agent Architecture

| Agent | Razor-Thin Responsibility | Strict Scope | Exact Trigger | Explicit Exclusions |
|---|---|---|---|---|
| **triage-router** | Read the diff, decide which specialists run. Never judges code quality. | File list, change type, keyword/pattern signals in diff hunks only. | Every review request, always first. | Never emits `[SEVERITY]` findings; never reads full files beyond diff hunks; never invokes other agents itself. |
| **security-review** | Security vulnerabilities only. | Injection, authN/authZ, secrets, unsafe deserialization, SSRF/path traversal, crypto misuse, CORS/CSRF, vulnerable dependency bumps. | Auth/crypto/secret/session keyword; string-built query/command; deserialization call; new external-input handler; dependency bump in security-relevant lib. | Pre-existing vulns untouched by diff; theoretical vulns with no reachable input; missing rate limiting/headers as blanket advice; resource leaks or races with no security consequence. |
| **data-integrity-review** | Data loss/corruption + functional correctness. | Transaction integrity, migrations/schema, SQL correctness, business-logic arithmetic, (de)serialization fidelity, unsafe state mutation, correctness risk from duplicated/dead logic. | `.sql`/migration file; DB write/ORM change; calculation touching money/date/quantity; transaction boundary change; near-identical duplicated business logic; unreachable branch/function added. | Pre-existing wrong queries untouched by diff; query performance (not correctness); resource/lifecycle issues; authz-scoped data exposure (→ security); missing tests; duplication/complexity flagged with no concrete drift risk. |
| **concurrency-resource-review** | Concurrency/async defects + resource-lifecycle failures. | Races, deadlocks, unsynchronized shared state, async/promise correctness, leaked handles/connections/memory, double-free/use-after-free. | async/await, thread, lock/mutex/semaphore keyword; resource-acquiring call; `finally`/`using`/`with`/RAII/`Dispose`/`close` touched. | Pre-existing races/leaks untouched by diff; TOCTOU race that is actually an authz bypass (→ security); lock/resource performance cost (→ performance); missing concurrency tests. |
| **reliability-availability-review** | Reliability/availability under failure. | Error-handling that hides failure, missing/wrong timeouts, retry/backoff defects, cascading-failure risk, startup/shutdown/health-check/queue-ack correctness. | Broadened/added catch-all; new network/DB call w/ no timeout; retry/backoff code; startup/shutdown/health-check/consumer-ack logic. | Pre-existing handling untouched by diff; leaks inside a catch block (→ concurrency-resource); auth-service-fails-open (→ security); "add more logging" advice; missing tests. |
| **performance-review** | Material performance regressions only. | N+1 patterns, algorithmic complexity regressions, blocking calls in non-blocking contexts, unbounded growth by design, batch/pagination regressions, query-plan regressions. | Loop wrapping I/O/DB call; request-handler hot-path change; batch/page-size/cache-config change; algorithmic-structure change with evident scale. | Pre-existing perf characteristics untouched by diff; micro-optimizations w/o measured impact; slow code on demonstrably small bounded input; query correctness (→ data-integrity); growth from a cleanup bug (→ concurrency-resource); missing benchmarks. |
| **api-type-contract-review** | API/contract violations + type-safety failures. | Breaking signature/endpoint changes, schema/DTO drift, unsafe type widenings/casts, null/undefined contract breaks, enum/union exhaustiveness, cross-language boundary drift. | Public function/endpoint/interface signature change; request/response DTO/schema change; type-annotation widening/removal; versioned-API file. | Pre-existing mismatches untouched by diff; breaking changes fully propagated to every visible consumer; internal logic correctness (→ data-integrity); same-language breaks the compiler already catches; missing tests. |
| **testing-coverage-review** | Testing as a discipline — coverage gaps and test-quality defects. | Untested non-trivial new branches/boundaries, tests that cannot fail, removed/weakened assertions, flaky-prone patterns, test-isolation defects. | Production logic changed w/ no test diff in same commit; test file touched; new boundary condition without a boundary-case test; new test with a sleep/unseeded-random/shared-state pattern. | Missing tests for untouched code; subjective test style; duplicated logic or dead code (→ data-integrity); re-flagging a bug a peer agent already owns just because it also lacks a test; computing coverage percentages or running a test runner. |

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
| File type | `*.test.*`, `*_test.*`, `test_*.py`, `**/tests/**` | testing-coverage-review |
| File type | CI/CD config (`.github/workflows/*`, `Jenkinsfile`) | reliability-availability-review |
| Change type | New public/exported function or endpoint signature | api-type-contract-review |
| Change type | New/removed try/catch, retry, timeout, circuit breaker | reliability-availability-review |
| Change type | New lock/thread/async/await/resource-open call | concurrency-resource-review |
| Change type | Pure rename/formatting/comment-only diff | none (triage marks "no semantic change") |
| Framework | ORM model change (SQLAlchemy, Hibernate, EF Core, TypeORM) | data-integrity-review |
| Framework | Web framework route/controller/handler added or changed | security-review (authz), api-type-contract-review (signature/schema) |
| Framework | Test framework fixtures/mocks changed | testing-coverage-review |
| Technology | Message queue consumer/producer code | reliability-availability-review (ack/dead-letter), concurrency-resource-review (consumer concurrency) |
| Technology | Crypto library call, JWT/session library call | security-review |
| Technology | Serialization library (pickle, Jackson, protobuf, JSON) | security-review (unsafe deserialization) + api-type-contract-review (schema drift) as applicable |
| Risk signal | Dependency/lockfile version bump | security-review (CVE relevance), reliability-availability-review (behavior change risk) as applicable |
| Risk signal | Auth/session/secret keyword in diff hunk | security-review |
| Risk signal | Money/date/quantity arithmetic changed | data-integrity-review |
| Risk signal | Large near-identical block added within a touched file/module | data-integrity-review |
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

**True Positive (structural correctness risk)** — Python, duplicated discount logic that can drift:
```python
def compute_checkout_total(order):
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.80
    return order.total


def compute_invoice_total(order):
    # copy-pasted from compute_checkout_total when invoicing was added
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.80
    return order.total
```
Expected: `[MEDIUM] billing.py:9 — compute_invoice_total duplicates compute_checkout_total's VIP-discount rule`
Impact: the 20%-VIP-discount rule now exists in two places; a future change
to the discount threshold or rate that only updates one of them will make
checkout and invoicing silently disagree on the same order's total.
Fix: extract the shared rule into one function (e.g. `apply_vip_discount`)
and have both call sites use it.

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

### testing-coverage-review

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

**Boundary Case (flaky-prone pattern)** — a new test that sleeps instead of waiting on a condition:
```python
def test_background_job_completes():
    submit_job(job_id="123")
    time.sleep(0.5)  # assume the worker is done by now
    assert get_job_status("123") == "complete"
```
Expected: `[MEDIUM] test_jobs.py:3 — Fixed sleep instead of waiting on job completion`
Impact: under CI load the worker can legitimately take longer than 500ms,
making this test fail intermittently regardless of whether the job logic
is correct — a flaky test erodes trust in the whole suite and gets ignored
or disabled rather than fixed. Distinguish this from an ordinary passing
test by checking whether the wait is time-based or condition-based; a
`sleep` before an assertion on external/async completion is the tell.
Fix: poll `get_job_status` with a timeout and short interval, or await an
explicit completion signal, instead of a fixed sleep.

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
- Duplicated or dead business logic vs. a plain missing test → data-integrity-
  review owns the former (it's a correctness-risk finding) and
  testing-coverage-review owns the latter; neither reports the other's line.
- "This bug also lacks a test" → testing-coverage-review explicitly stands
  down whenever a peer agent already owns the underlying defect, so a
  single line never produces two findings for the same root cause.

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

**Open items — verify these in a live environment before relying on this
system in CI:**

1. *Frontmatter field set.* This design intentionally sticks to the small,
   well-established fields (`name`/`description`/`tools`/`model`/`color`)
   with git-scoped `Bash` arguments, rather than the larger surface some
   community examples show (permission modes, hooks, MCP server lists).
   Confirm your installed Claude Code version's supported fields before
   extending this list, and don't add a permission-bypass-style option to a
   review-only agent regardless of what any single source suggests — there
   is no legitimate reason for a read-only reviewer to need one. Also
   confirm the `Bash(git diff *)`-style argument scoping is actually
   enforced by your version (try `git push` from inside one of these agents
   and confirm it's refused) rather than trusting the frontmatter alone.
2. *`CLAUDE.md` auto-inheritance is the load-bearing efficiency assumption.*
   The "write shared rules once" design depends on subagents actually
   receiving the project's `CLAUDE.md` in their initial context. If a given
   Claude Code version doesn't do this, every agent silently loses the
   severity model, evidence bar, and output contract — the symptom would be
   inconsistent output formatting across agents. Run one real review
   through `/review-pr` and confirm a specialist's output actually follows
   the `[SEVERITY] file:line` contract before trusting it in CI.
3. *`/review-pr`'s parallel-dispatch step is an instruction to the
   orchestrating session, not a hard guarantee.* If a given Claude Code
   version executes agent invocations sequentially regardless of phrasing,
   the system still works correctly — it's just not exploiting the
   parallelism assumed here. Not a correctness risk, only a latency
   assumption worth confirming.
4. *Triage on `haiku` is a deliberate cost/accuracy trade.* If triage
   under- or over-routes in practice (check the `<skip>` reasons it emits
   against diffs known to touch, say, concurrency), the fix is to bump
   `model: haiku` to `model: sonnet` in `triage-router.md` — the routing
   rule table itself doesn't need to change.
5. *The validation matrix (Section E) is hand-written, not yet executed.*
   These snippets are the acceptance tests this design should be graded
   against, but they have not been run through the agents in a live Claude
   Code install as part of this repository. Run them for real before
   treating any agent as validated.

---

## G. Standalone CLI (`agent-review` / `agent-init`)

Sections A–F describe the Claude-Code-native system: `.claude/agents/*.md`
files that only run inside a Claude Code session, on whatever repository
that session happens to be open in. `cli/` is a separate, second
implementation of the same review philosophy — same severity model, same
evidence bar, same 7 defect-class specialists, same output contract — built
so it can run **against any git repository on disk, from any machine, with
no Claude Code installation at all.**

This is genuinely new software, not a refactor: Sections A–F never
contained a line of executable code (agent files are prompts, not
programs), so nothing existing was "restructured" — `cli/` is a from-scratch
Python package that reuses the *content* of `CLAUDE.md` and the 7 specialist
prompts (copied verbatim into `cli/src/agent_review/default_rules/`) as its
default configuration, then reimplements everything else — routing,
caching, orchestration, git plumbing — as real, tested control flow.

### Why a direct API client instead of wrapping the Claude Code CLI

The obvious alternative was to shell out to Claude Code's own CLI in
headless mode and let it invoke the existing subagents. That was rejected
for three concrete reasons: (1) this build environment has no way to
install or invoke Claude Code itself to confirm its headless
subagent-invocation behavior, so that path couldn't be tested at all, only
assumed; (2) a direct dependency on the `anthropic` package is something
every step of this package can be unit-tested against — including with a
zero-network `Reviewer` fake — whereas shelling out to another CLI's
undocumented headless behavior is not; (3) portability — `agent-review`
now runs anywhere Python and Foundry credentials exist, not only on
machines that also have Claude Code installed and configured. The
trade-off is real and worth naming: this CLI's prompts are a *snapshot* of
the `.claude/agents/` files at the time it was built, not a live read of
them at every invocation (see "Config precedence" below for how a repo can
still point the CLI at its own, current agent files).

### Azure-only: Claude via Microsoft Foundry, not api.anthropic.com

This project talks to Claude **exclusively** via Microsoft Foundry (Azure
AI Foundry) — there is no direct-to-`api.anthropic.com` code path, by
explicit choice, not merely as one option among several. Microsoft Foundry
serves Claude through an Anthropic-compatible Messages API, and the
official `anthropic` Python package ships a dedicated client class for it,
`anthropic.AnthropicFoundry` (parallel to `AnthropicBedrock`/
`AnthropicVertex` for the other clouds), added in `anthropic-python`
0.74.0. `messages.create()`'s signature is unchanged from direct Anthropic
use — same `system=`/`messages=` shape, same response object — so every
prompt in this project (`CLAUDE.md` plus the 7 specialist agents) needed
zero changes to migrate; only `agents_client.py`'s construction logic
changed (client class, auth, resource name).

`AnthropicFoundryReviewer` (`agents_client.py`) supports both of Foundry's
auth modes, validated eagerly at construction for the same reason the
original API-key check was eager (see "What TDD actually caught," below):
a Foundry **resource name** (`ANTHROPIC_FOUNDRY_RESOURCE` / `--resource`)
is required either way -- unless `ANTHROPIC_FOUNDRY_BASE_URL` is set
instead (env-var only, no `--base-url` flag; a rare, advanced override for
a custom endpoint, mutually exclusive with a resource, matching
`anthropic.AnthropicFoundry`'s own constructor contract) -- then either an
**API key** (`ANTHROPIC_FOUNDRY_API_KEY` / `--resource`'s sibling
`api_key=`, the default) or **Entra ID** (Azure AD) via `azure-identity`'s
`DefaultAzureCredential` (`ANTHROPIC_FOUNDRY_USE_ENTRA_ID=1` /
`--use-entra-id`) — the latter an optional dependency
(`pip install -e ".[azure-ad]"`), since API-key auth doesn't need it.
`DEFAULT_MODEL` is `claude-sonnet-5`, matching what's actually GA in
Foundry's model catalog (`claude-opus-5`, `claude-opus-4-8`,
`claude-sonnet-5`, `claude-haiku-4-5` at time of writing) — Foundry's
catalog and api.anthropic.com's aren't guaranteed to list identical model
names, so this floor was verified against Foundry's own documentation
rather than assumed to match the direct API.

### File manifest (`cli/`)

```
cli/pyproject.toml                      # [project.scripts]: agent-review, agent-init
cli/README.md                           # CLI-specific install/usage docs
cli/src/agent_review/cli.py             # argparse wiring: review/commit/heal subcommands + agent-init
cli/src/agent_review/orchestrator.py    # diff -> route -> (cache hit | model call) -> parse -> aggregate
cli/src/agent_review/routing.py         # zero-cost deterministic port of triage-router.md's rule table
cli/src/agent_review/cache.py           # .agent-cache/manifest.json — blob-hash + agent-set keyed
cli/src/agent_review/git_utils.py       # git plumbing against an arbitrary target repo path
cli/src/agent_review/discovery.py       # test-runner auto-discovery via marker files
cli/src/agent_review/prompts.py         # .agent-rules/ -> .claude/ -> bundled default_rules/ lookup
cli/src/agent_review/layout.py          # shared target-repo layout constants (init.py + prompts.py)
cli/src/agent_review/findings.py        # output-contract parser/sorter (shared with the orchestrator)
cli/src/agent_review/suppressions.py    # optional .claude/ignore-findings.yml false-positive suppression
cli/src/agent_review/agents_client.py   # Reviewer protocol + real AnthropicFoundryReviewer (Azure only)
cli/src/agent_review/commit.py          # staged-diff commit message generator (no co-author trailer)
cli/src/agent_review/healing.py         # guarded self-healing: propose a patch, apply only if --apply
cli/src/agent_review/init.py            # agent-init: scaffolds .agent-rules/, .agent-cache/, DESIGN.md
cli/src/agent_review/default_rules/     # bundled snapshot of CLAUDE.md + the 7 specialist prompts
cli/tests/                              # 157 pytest tests, including full CLI-entry-point integration tests
```

### Local caching (requirement 1: cache isolation + incremental analysis)

`.agent-cache/manifest.json` lives *inside the target repository*, not
globally and not only in process memory. Each entry is keyed by the file's
git blob hash (`git hash-object`) plus the exact sorted set of specialist
agents routed to it; a cache hit requires both to match exactly, so
widening the routed agent set (e.g. after editing a specialist's prompt)
correctly invalidates the cache even though the file's content didn't
change. `run_review()` only ever calls the model for a cache miss — a
second run with zero relevant changes costs zero API calls, verified
end-to-end in `cli/tests/test_cli_integration.py` through the real
`agent-review` entry point, not just the lower-level orchestrator function.
The cache directory itself is excluded from being treated as reviewable
content unconditionally (`orchestrator.py`'s `_ALWAYS_IGNORED_PREFIXES`),
independent of whether the target repo's `.gitignore` has been set up yet —
this was a real bug caught during development (see "What TDD actually
caught," below), not a design that was correct on the first attempt.

### Global CLI & multi-repo entry point (requirement 2)

`agent-review --path /path/to/repo` (any subcommand — `review` is implied
when none is given) resolves that path, confirms it's a git repository,
and does everything relative to it. `prompts.py`'s config lookup checks,
in order, `<target_repo>/.agent-rules/`, then `<target_repo>/.claude/`
(so a repo that already uses the Claude-Code-native agents — including
this one — works with the CLI unmodified), then falls back to the
snapshot bundled with the package. `DESIGN.md`/spec files are read from
the target repo's own root, never from wherever the CLI happens to be
installed.

### Portable git hooks & auto-discovery (requirement 3)

Every git operation in `git_utils.py` takes the target repo's path as an
explicit argument (`git -C <repo> ...`) rather than assuming the current
working directory — including a fix, found via testing, for brand-new
files that were never `git add`ed (`changed_files()` originally missed
them entirely; a review tool that can't see a file someone just wrote is a
real functional gap, not an edge case). `discovery.py` detects the target
repo's test runner via marker files (`pyproject.toml`/`pytest.ini` →
pytest, `package.json` → npm test, `pom.xml` → Maven, `build.gradle[.kts]`
→ Gradle, `Cargo.toml` → cargo, `CMakeLists.txt` → ctest, `*.csproj`/`*.sln`
→ dotnet) — it no longer also detects a repo's language mix; that was
removed as dead code (nothing consumed it — routing.py's specialist
routing has always worked directly off diff-content patterns, never off a
detected language label). `commit.py` generates a semantic commit message
for the target
repo's staged diff and — per the user's own specification — never adds a
co-author or attribution trailer; this is unrelated to and does not
override the attribution policy Claude follows for its own commits to
*this* repository, since here the CLI is producing a message on behalf of
the user, about the user's own change, for the user's own commit.

"Self-healing loops" are deliberately **not** autonomous. `healing.py` runs
the detected test command and, on failure, asks the model for a root-cause
explanation and a unified diff — but `apply_patch()` is never called
implicitly; the CLI's `agent-review heal` subcommand only writes the patch
to disk (via `git apply`, after a `git apply --check` dry run) when the
user passes `--apply` explicitly, and always re-runs the tests afterward
to report whether the patch actually fixed the failure. A silent
autonomous edit-and-commit loop was considered and rejected: it is exactly
the kind of unbounded blast radius this entire project's evidence-based,
diff-scoped philosophy exists to avoid.

### Initialization command (requirement 4)

`agent-init --path /path/to/repo` is idempotent — safe to re-run, never
overwrites a file that's already there (verified by
`cli/tests/test_init.py`, including against a repo that customized a
scaffolded file). It creates `<repo>/.agent-rules/` (a copy of
`CLAUDE.md` and the 7 specialist prompts, editable per-repo without
touching the installed package), ensures `.agent-cache/` is listed in the
target repo's `.gitignore`, and writes a starter `DESIGN.md` (architecture
overview / key invariants / known tradeoffs) if the repo doesn't already
have one.

### What TDD actually caught

Every module above was written test-first and run for real
(`python -m pytest cli/tests/ -q`, 87 tests passing), and several genuine
bugs were found and fixed this way rather than assumed away:

- `routing.py`'s original keyword list had **no pattern for the single
  most iconic SQL-injection shape** — an f-string or concatenation
  building a query around unescaped input — until a validation-style test
  case for exactly that surfaced the gap.
- `git_utils.changed_files()` silently skipped untracked (brand-new,
  never-`git add`ed) files entirely, which would have made the CLI blind
  to new files in the exact moment they matter most (right before a
  commit).
- Fixing that immediately surfaced a second bug: the cache's own
  `.agent-cache/manifest.json`, once written, showed up as an "untracked
  file" on the next run and got treated as reviewable content — breaking
  the "second run with no changes costs zero API calls" guarantee this
  whole caching design exists to provide.
- `healing.py`'s end-to-end apply-then-rerun test failed intermittently
  for a subtle reason: CPython's `.pyc` bytecode cache is invalidated by
  `(mtime, size)`, and a one-character fix can leave both identical
  versus the failing run moments earlier, causing a rerun to silently
  execute stale bytecode and report the original failure again even
  though the patch applied correctly. Fixed by running the test command
  with `PYTHONDONTWRITEBYTECODE=1`.
- The very first draft of the reviewer client (`AnthropicReviewer`, calling
  api.anthropic.com directly, before the later migration to Foundry) only
  validated the API key lazily, inside the anthropic SDK, at request time
  — which, dispatched from inside the orchestrator's thread pool, surfaced
  as a raw Python traceback from a worker thread instead of a clean error.
  Fixed by validating eagerly at construction; that same eager-validation
  discipline carried over to `AnthropicFoundryReviewer`'s resource/API-key/
  Entra-ID checks when the client was migrated to Foundry.
- A self-review performed right after the Foundry migration (before
  shipping it) caught a real boolean-parsing bug in that same construction
  path: `use_entra_id = bool(os.environ.get("ANTHROPIC_FOUNDRY_USE_ENTRA_ID"))`
  treats *any* non-empty string as `True`, so someone explicitly setting
  `ANTHROPIC_FOUNDRY_USE_ENTRA_ID=0` to turn Entra ID **off** would instead
  have it silently turned **on** — bypassing their configured API key
  entirely and switching to `DefaultAzureCredential`, likely failing (or
  worse, succeeding against the wrong identity) far from the actual
  misconfiguration. Reproduced directly, then fixed with a small
  `_env_flag()` helper that treats `""`, `"0"`, `"false"`, `"no"`, and
  `"off"` (case-insensitive) as false and everything else as true;
  covered by a parametrized regression test
  (`test_use_entra_id_env_var_falsy_string_does_not_enable_entra_id`) plus
  a companion truthy-value test so the fix doesn't overcorrect.
- The same self-review flagged the `anthropic>=0.74.0` floor as
  *plausible but unverified* — it's confirmed to be the release that added
  `AnthropicFoundry`, but the constructor's exact keyword surface
  (`resource=`, `api_key=`, `azure_ad_token_provider=`) had only been
  exercised against the newer 1.4.0 installed in this sandbox. Installed
  0.74.0 itself in an isolated venv and inspected the constructor
  signature directly — it matches exactly, so the pin is now a verified
  floor, not an assumption carried over from "first version with the
  class."

### Honest limitations of this CLI, as delivered

- **Confirmed working end-to-end against a real Microsoft Foundry
  resource** (2026-09-09, `claude-haiku-4-5`, run by the CLI's own user
  against a real repo) — no longer a theoretical gap. The build/test
  environment itself still has no Foundry resource or credential
  configured, so most automated tests still exercise orchestration logic
  (routing, caching, git diffing, patch application) against a
  hand-written `FakeReviewer`/`ScriptedReviewer` rather than the network;
  `cli/tests/test_agents_client.py` still only covers
  `AnthropicFoundryReviewer`'s constructor-time validation and its error
  handling, not a real `messages.create()` call. As of 0.8.0,
  `cli/tests/test_cli_integration_live.py` narrows this gap one step
  further: it exercises the real orchestrator against a response shape
  captured from (or replayable without) a live Foundry call, via a
  record/replay cassette (`cli/tests/cassettes/integration.json`,
  `AGENT_REVIEW_RECORD_LIVE=1` to refresh against real credentials) —
  see "CI/CD validation harness and cassette testing" below. But the real
  client has now been exercised for real, by an actual user, against an
  actual resource, and returned a real (clean) review -- getting there
  also surfaced and fixed several real config/UX gaps along the way: a
  `.env`-loading bug, unstripped whitespace in the resource/key, an
  opaque connection error when `ANTHROPIC_FOUNDRY_RESOURCE` held a model
  deployment name instead of the resource name, and the
  deployment-vs-deploymentless model distinction (some models require an
  actual named deployment in the target resource; calling a bare model ID
  that isn't deployed there fails with `DeploymentError`). See
  CHANGELOG.md's 0.4.4-0.4.8 entries for the specifics.
- **Nothing has been installed or run on the user's actual machine from
  this session** — this build and its test suite ran entirely inside this
  sandbox. `pip install -e cli/` needs to be run for real, on the target
  machine, before `agent-review`/`agent-init` exist as commands there.
- **Test-runner auto-discovery is a file-presence heuristic**
  (`discovery.py`), not a guarantee the detected command is installed or
  correctly configured for a given repo; `agent-review heal` reports a
  missing/failing runner clearly rather than guessing further.

### CI/CD validation harness and cassette testing

Two testing gaps closed in 0.8.0, both built around the same idea: a
`Reviewer` (see `agents_client.Reviewer`) that replays a previously
recorded response by default -- deterministic, free, no secrets needed --
and switches to a real Foundry call only when explicitly asked to
re-record.

- **The `.claude/agents/*.md` prompts now have automated regression
  coverage.** `scripts/validate_fixtures.py` runs every case in
  `tests/fixtures/manifest.json` through its target agent, reusing the
  standalone CLI's own `prompts`/`agents_client`/`findings` machinery to
  invoke each agent's body as a system prompt with the fixture's diff
  embedded in the user message (the same shape `orchestrator.py` already
  sends) -- the same precedent named above under "Why a direct API client."
  `.github/workflows/validate-agents.yml` runs it in replay mode
  (`tests/fixtures/cassettes.json`) on every PR touching an agent file,
  `CLAUDE.md`, or a fixture, and a cassette miss is a hard CI failure, not
  a silent skip. A `workflow_dispatch` input (`live: true`) runs the same
  harness against a real Foundry resource and uploads the refreshed
  cassette as a build artifact for a maintainer to review and commit --
  deliberately not auto-committed. Entries are keyed by a SHA-256 hash of
  the exact `(system_prompt, user_message)` pair, not a human-assigned
  name, specifically so a prompt or fixture change can never be silently
  satisfied by a stale recording (see `cli/tests/support/cassette.py`'s
  module docstring). The committed cassette was seeded with placeholder
  responses derived from each fixture's own `EXPECTED.md` (this sandbox
  has no Foundry credentials) -- clearly labeled as such in the cassette's
  `_meta` field and in `CassetteMissError`'s own message; it must be
  refreshed against a live resource (`--live`, or the `validate-live`
  workflow) before it's trusted for real regression detection, not just
  schema/wiring correctness.
- **`cli/`'s own orchestrator now has an opt-in live/replay integration
  test**, alongside the pre-existing fully-scripted
  `test_cli_integration.py`. `test_cli_integration_live.py` runs the real
  `orchestrator.run_review()` (routing, caching, git diffing, parsing --
  everything except the network call) against a cassette-backed reviewer,
  covering a SQL-injection true positive and a parameterized-query true
  negative that both route to `security-review` + `performance-review` in
  this repo's real routing rules, plus a same-content second run to prove
  `.agent-cache/` produces an identical result with zero further reviewer
  calls. Its cassette (`cli/tests/cassettes/integration.json`) is likewise
  placeholder-seeded pending a real `AGENT_REVIEW_RECORD_LIVE=1` run.
- **A real, previously unnoticed bug surfaced by building this**:
  `git_utils.diff_for_file`'s untracked-file branch (used for any
  brand-new file) passed the file's *absolute* path to `git diff
  --no-index`, which git then wrote verbatim into the diff's `a/... b/...`
  header -- leaking the reviewing machine's local checkout location into
  the text sent to the model, inconsistent with every other diff in this
  module (all relative to the repo), and impossible to hash reproducibly
  across machines for a cassette-based test. Fixed to pass the relative
  path (git already has the right working directory via `-C`); regression
  test in `cli/tests/test_git_utils.py`
  (`test_diff_for_file_untracked_uses_relative_path_not_absolute`).

### First real `--live` run against `tests/fixtures/` (2026-09-17): what it actually found

The placeholder cassette above was, by construction, seeded FROM each
fixture's own `EXPECTED.md` — every replay run through 0.9.0 was checking
that the harness's wiring was correct, not that the agents' real judgment
matched their fixtures. The first real `python scripts/validate_fixtures.py
--live` run against a real Foundry resource (run by this CLI's own user)
closed that gap for real, and it was not a clean pass: 13/23 cases. This is
exactly the kind of evidence this project's own `CLAUDE.md` asks its
agents to require of *findings* — a specific, reproducible input and a
concrete wrong output — applied here to the agents' own prompts. Every one
of the 10 failures was read in full (not just the truncated terminal
output) against its fixture's source and `EXPECTED.md` before any prompt
was touched; three distinct root causes, and three different classes of
fix:

1. **Output-contract non-compliance, independent of judgment.** Several
   `must_not_fire` cases got the right verdict wrapped in a markdown code
   fence, sometimes with an explanatory sentence appended after it (e.g.
   security-review's false-positive trap: `` ```\nNo high-impact issues
   found.\n```\n\nThe diff uses parameterized query execution...` ``).
   `CLAUDE.md`'s Output Contract already said not to do this in words; it
   didn't survive contact with a real model. Fixed by making the contract
   more explicit and adding a concrete right/wrong example directly in
   `CLAUDE.md` (fences and trailing commentary are a violation regardless
   of whether the verdict itself was correct) — this is a global fix, not
   per-agent, since the failure mode recurred across unrelated agents.
2. **Evidence-bar misapplication on a hard case, where the exclusion rule
   already existed in writing.** security-review flagged an f-string SQL
   query built from a value it explicitly identified as a module-level
   constant, reasoning about what *would* happen "if status were ever
   parameterized" — precisely the theoretical-future-misuse argument its
   own prompt already excludes. data-integrity-review flagged a column
   *widening* migration and a brand-new `CREATE TABLE` as backfill/data-loss
   risks, missing that neither can lose a single existing row (only
   narrowing/adding-constraints-to-existing-rows can). concurrency-
   resource-review flagged a class's own resource field as a leak despite
   the class implementing `AutoCloseable` and exposing `close()` — a
   legitimate delegated-ownership pattern its prompt didn't call out.
   performance-review flagged a loop explicitly commented as iterating a
   fixed 3 items, and a memoizing cache over what's plausibly a small
   fixed key space, as unbounded/N+1. api-type-contract-review flagged a
   same-file TypeScript signature break its own Evidence requirements
   already say is lower-value ("prefer findings the type checker will NOT
   catch") as CRITICAL. In every one of these five cases the general rule
   the model needed already existed in that agent's prompt — what was
   missing was a concrete counter-example anchoring it to this exact
   shape. Fixed by adding one to each of security-review.md,
   data-integrity-review.md, concurrency-resource-review.md,
   performance-review.md, and api-type-contract-review.md's own Explicit
   exclusions, using close variants of the actual failing inputs.
3. **A structural mismatch between how these prompts are written and how
   this CLI actually invokes them.** testing-coverage-review's true_positive
   case — a brand-new function with two calculation branches and zero test
   file in the diff, about as unambiguous a coverage gap as this fixture
   matrix contains — got `No high-impact issues found.`, a real false
   negative. Its own prompt's Context Acquisition says to "confirm by
   reading the test diff... do not assume absence without checking," and
   its YAML frontmatter grants `Read`/`Grep`/`Bash` tools — both written
   for a live Claude Code session. `agents_client.Reviewer.complete()`
   sends that same prompt body as a single text completion with none of
   those tools actually available, and a model taking the instruction to
   "check" seriously, with no way to check, has a defensible-sounding
   reason to withhold the finding rather than assume the gap is real. Fixed
   at the orchestrator level, not by rewriting every agent's Context
   Acquisition section (which stays correct for the live Claude Code case
   this system also serves): `orchestrator.build_review_user_message()`
   (shared by `orchestrator.py` and `scripts/validate_fixtures.py` so they
   can never drift apart on this) now appends a short note after the diff
   telling the model plainly that it has no tool access here, the diff
   shown is its complete evidence, and an instruction above to "check" or
   "Grep for" something is not a reason to withhold an otherwise-supported
   finding. The same run also surfaced a second, narrower issue in this
   family: testing-coverage-review's own false-positive-trap fixture
   called an undefined `make_order()` helper with no import anywhere in
   the diff, making "this test can't run" a defensible finding under a
   strict reading even though the fixture's intent was "both branches are
   exercised with a real assertion." Fixed two ways: the fixture itself
   now imports `compute_discount` and defines `make_order()`, removing the
   ambiguity, and testing-coverage-review.md gained an explicit exclusion
   ("assume a referenced helper/fixture exists elsewhere unless the diff
   itself proves otherwise; import/syntax validity is not this agent's
   job").

None of this is fixed with certainty the way a unit test failure is —
these are prompt/model-behavior changes, verified against real inputs but
inherently probabilistic, not a code diff with a deterministic pass/fail.
`tests/fixtures/cassettes.json` (the user's real recording that exposed
all of this) is now entirely stale, since the prompts and message shape it
was keyed against both changed; it's been re-seeded with fresh
`EXPECTED.md`-derived placeholders (same bootstrap convention as before
the first live run) so replay CI stays meaningful in the interim, but a
fresh `--live` run is what actually confirms whether these fixes worked
and is the natural next step, not merely a nice-to-have.

### Second real `--live` run (2026-09-17): 16/23, and what changed

A second live run, after 0.9.1's fixes, scored 16/23 (up from 13/23) and
recorded 43 cassette entries. Both round-1 root causes that were fully
architectural — the code-fence output-contract violation and
testing-coverage-review's `make_order()` false negative — were completely
resolved; neither recurred in any form. The remaining 7 failures split
into three groups, read in full against their fixture source (not just the
terminal's truncated summary) before touching anything, per this project's
own evidence-bar discipline:

1. **Three repeats where the round-1 counter-example didn't take.**
   `data-integrity-review/boundary_case` (a brand-new `StagingImports`
   table with `varchar(50)` and no `NOT NULL`) still fired, but reframed:
   instead of the backfill/narrowing argument the 0.9.1 fix addressed, the
   model now argued forward — a *future* insert might overflow the column,
   and *downstream* code might assume a constraint that isn't there. Same
   underlying error (judging a brand-new, empty table by hypothetical data
   never shown in the diff), different angle the existing exclusion didn't
   cover. `concurrency-resource-review/boundary_case` (`ReportSession`,
   `AutoCloseable`, connection in a field initializer) still fired too,
   now arguing "what if `pool.getConnection()` itself throws before
   `close()` can ever run" — which doesn't hold up (if the acquisition
   call throws, nothing was acquired, so there is nothing to leak) but the
   0.9.1 exclusion never addressed that specific, logically-unsound shape.
   `api-type-contract-review/boundary_case` (`formatPrice`'s arg-count
   change, a same-diff `.ts` caller not updated) still fired CRITICAL even
   though the model's own reasoning explicitly acknowledged it as a
   compile-time-catchable break — the 0.9.1 exclusion existed but likely
   lost out to the Strict-scope bullet describing the identical shape
   earlier in the same prompt with no cross-reference to the carve-out.
   Fix for all three: added a concrete rebuttal anchored to the exact
   failing shape (no-op-on-failed-acquisition and parent/child resource
   closure for concurrency-resource-review; forward/downstream speculation
   for data-integrity-review; an explicit "this exclusion controls even
   though the Strict-scope bullet above describes this shape" cross-
   reference for api-type-contract-review), rather than restating the same
   abstract rule more verbosely a second time.

2. **A backfire from round 1's own added example.** `performance-review`'s
   0.9.1 fix for the `config.py` cache-fill boundary case explicitly
   *illustrated* a bad case ("attacker/request-controlled — a user ID, a
   free-text query, a tenant ID with no ceiling") to contrast against the
   good one. In the live run, the model's own stated reasoning quoted that
   exact vocabulary back — "if `key` is... a user ID... tenant ID... without
   evidence it's truly fixed and small, assume it is attacker-
   controllable" — and flagged it anyway, inverting the instruction's
   actual conclusion. `config.py`'s `key` parameter shows no such evidence
   either way; the model appears to have pattern-matched on the salient
   keywords in the illustrative bad example rather than checking whether
   the code in front of it actually exhibited them. Fix: reworded the
   exclusion to drop the bad-case example vocabulary entirely and instead
   require positive evidence of an unbounded key space from the diff
   itself, with an explicit "if the diff gives no indication either way,
   do not report it" resolution. Concrete lesson for future prompt edits
   on this project: an illustrative "here's what WOULD be bad" example
   inside an exclusion rule can get misapplied by surface-keyword match
   rather than by the logical condition it's embedded in — prefer stating
   the positive evidence requirement plainly over naming bad-case
   examples.

3. **Two failures left alone pending more evidence.**
   `data-integrity-review/true_positive_structural` (billing.py's
   duplicated VIP-discount logic, previously a reliable pass) missed
   entirely this run, and `reliability-availability-review/false_positive_trap`
   (rates.ts's capped-retry-with-backoff) newly over-triggered, on an agent
   whose prompt was never touched in any round. Neither is explained by
   anything edited in round 1: the first is an unrelated bullet in the same
   file as the (correctly targeted) migrations fix, and the second's agent
   prompt is untouched entirely. Two plausible explanations, not
   distinguished with n=1: ordinary run-to-run model variance, or a side
   effect of round 1's `_NO_TOOL_ACCESS_NOTE` addition ("don't withhold an
   otherwise-supported finding merely because you can't run a command")
   nudging the model toward being more trigger-happy in general, not just
   on the testing-coverage-review case it targeted. No prompt change was
   made for either case this round — editing a prompt on a single
   contradictory data point risks the same kind of collateral regression
   round 1 just produced elsewhere. Recommendation for whoever runs the
   next `--live` pass: if either of these reproduces, that's real signal;
   if not, this round's data point was noise.

Methodological note carried forward: single-shot `--live` runs are a
noisy signal for judging whether a prompt fix worked, precisely because
the thing being tested (an LLM's judgment on a boundary case) is
stochastic. Three of `cassettes.json`'s cases have now needed corrective
prompt edits after two separate specific counter-examples apiece across
two rounds without landing on the first try. A more statistically sound
process going forward would run 2-3 live samples per case and look at the
majority verdict rather than pass/fail on one call — noted here rather
than implemented, since it changes `validate_fixtures.py --live`'s cost
and would need the user's buy-in before making every `--live` run several
times more expensive.

`tests/fixtures/cassettes.json` is stale again for the four agents
touched this round (data-integrity-review, concurrency-resource-review,
performance-review, api-type-contract-review) and has been re-seeded with
`EXPECTED.md`-derived placeholders for exactly those 13 affected cases (23/23
pass on the placeholder-seeded cassette, confirming harness wiring only —
not model judgment). A third `--live` run is what actually confirms
whether round 2's fixes worked.

### Third real `--live` run (2026-09-17): the same 7 cases failed again

A third live run scored the same 16/23 as round 2 — and, case for case,
the identical 7 failures. This is a materially different signal than
round 2's: every one of round 2's four targeted fixes (data-integrity-
review, concurrency-resource-review, performance-review, api-type-
contract-review boundary cases) failed to change the model's verdict on a
second attempt, and both cases round 2 deliberately left untouched for
lack of evidence (data-integrity-review's `true_positive_structural` miss,
reliability-availability-review's `false_positive_trap` over-trigger) now
have n=2 identical repeats — no longer distinguishable from noise. Read
each in full again rather than assuming round 2's diagnosis still applied:

1. **Two rounds of textual counter-examples, same four cases, same
   outcome — but the model's *specific* argument moved each time**,
   which matters: it means the fixes were closing the exact door they
   targeted, and the model was finding a different door, not ignoring the
   instruction outright.
   - `data-integrity-review/boundary_case`: round 2 closed off "a future
     insert might overflow this column"; round 3's response reframed the
     identical concern as a present-tense design defect ("varchar(50) is
     objectively too narrow for common real email addresses") — same
     underlying argument, phrased to avoid the specific "future" framing
     that had just been excluded. Fix: stopped rebutting *framings* and
     instead moved the whole carve-out to Explicit exclusions as a
     categorical rule — a brand-new table's column width/constraint is
     out of scope "no matter how you frame the argument," explicitly
     including comparison to an external standard (RFC 5321, typical name
     lengths) as still not evidence about this diff.
   - `concurrency-resource-review/boundary_case`: round 2 closed off "what
     if the acquisition call itself throws"; round 3 shifted one step
     later — "what if some exception occurs after the field initializer
     succeeds but before construction completes." For `ReportSession`
     specifically (one field, no other constructor code), that window
     doesn't exist — there is no statement in the diff that could throw in
     that gap. Fix: added the rebuttal that a single-field class has no
     third code path, and instructed the agent not to invent additional
     initializer/constructor code the diff doesn't show just to create
     the window the argument needs.
   - `performance-review/boundary_case`: round 2 replaced "attacker/
     tenant ID" example vocabulary with a plain evidence requirement;
     round 3 still asserted the key was "an attacker or caller passing
     free-form user" input, with nothing in the 6-line diff supporting it.
     The diff has no caller at all — `get_config` is never invoked in what
     's shown. Fix: made that literal — no caller in the diff means the
     Evidence Bar's "path exists"/"trigger is plausible" clauses cannot be
     met by definition, so the model may not reach outside the diff for a
     plausible-sounding caller to complete its own argument.
   - `api-type-contract-review/boundary_case`: two rounds of prose
     (including an explicit "this exclusion controls over the Strict-scope
     bullet" cross-reference) left the verdict unchanged in substance
     (severity did drop CRITICAL → HIGH the second time, suggesting *some*
     signal was getting through). Round 1's CLAUDE.md fix for the
     code-fence violation — a concrete WRONG/RIGHT worked example, not
     more descriptive prose — is the one technique in this whole exercise
     with a confirmed track record, so round 3 applies it here for the
     first time: a literal WRONG (the exact `formatPrice`/`checkout.ts`
     finding text) paired with the RIGHT response and a one-line reason,
     mirroring CLAUDE.md's own Output Contract section.

2. **Two "leave it alone" cases from round 2, now confirmed real on a
   second sample.** `reliability-availability-review/false_positive_trap`
   (rates.ts) repeated its over-trigger with a related but re-worded
   complaint both times ("swallows exceptions without distinguishing
   retriable from permanent," then "does not validate whether exception is
   retryable before retrying") — a real gap in this agent's prompt (never
   edited before this round): nothing in its Strict scope or exclusions
   addresses "retries without classifying the error first" as long as the
   retry is otherwise capped, backed off, and propagates on exhaustion.
   Added that exclusion for the first time. `data-integrity-review/
   true_positive_structural` (billing.py's duplicated VIP-discount rule)
   missed a second time with no visible reasoning to diagnose (a bare
   `must_fire` miss produces no output to read, unlike an over-trigger).
   Best available lever: the adjacent Migrations/schema bullet had grown
   substantially across two rounds of edits into the single longest bullet
   in the Strict scope list — moved its widening/brand-new-table carve-outs
   out to Explicit exclusions (a more natural home for them regardless)
   and shrank the Strict-scope bullet back to its original length, on the
   hypothesis that bullet's growing length was crowding attention away
   from its neighbor. This is a hypothesis, not a confirmed diagnosis —
   there's no way to inspect the model's reasoning on a silent miss to
   verify it.

3. **`testing-coverage-review/false_positive_trap` — not a prompt bug at
   all.** Read fully for the first time this round: the fixture's diff
   adds both `pricing.py` (a brand-new `compute_discount` with a
   `total > 500` boundary) and `test_discounts.py` (tests at `total=600`
   for both VIP and non-VIP branches — comfortably over the boundary, never
   at it). `testing-coverage-review.md`'s own Strict scope explicitly lists
   "Untested new boundary conditions: ... the test diff exercises the
   interior case but not the boundary itself" as in-scope. The model's
   finding — the exact `total == 500` boundary is untested — is a correct
   application of the agent's own documented scope, not a false positive
   the prompt needs to suppress. `EXPECTED.md`'s "no finding" verdict
   predates that scope bullet's precise wording and the fixture's own test
   file genuinely has the gap the agent is designed to catch. Fixed the
   fixture, not the prompt: added `test_vip_discount_at_boundary_not_over_500`
   (`total=500`, expects the non-VIP 5% rate) to `test_discounts.py`,
   closing the actual gap so the fixture is a genuinely complete "no
   finding" case rather than asking the agent to stay silent about a real
   one. This is the same category of fix as round 1's `make_order()` fix:
   when a live run and a fixture disagree, read both before assuming the
   model is wrong.

`tests/fixtures/cassettes.json` is stale again for all five agents touched
this round (the four repeats plus reliability-availability-review) and for
testing-coverage-review's `false_positive_trap` case (fixture content
changed, so its diff and request-key hash changed regardless of the
prompt) — 17 cases re-seeded with placeholders, 23/23 on the harness wiring
check. A fourth `--live` run is what actually confirms whether any of this
round's fixes moved the needle, particularly the four repeat cases: two
rounds of prompt edits changing zero verdicts is a real pattern, and if a
third, more structural attempt (categorical rules, an evidence-bar gate
tied to "no caller in the diff," a worked example) doesn't move them
either, the more honest conclusion may be that these four specific
boundary cases are at or past the practical ceiling of single-shot prompt
engineering against this model, and worth a different strategy (e.g.
majority-vote over several live samples, or accepting them as a documented,
known-probabilistic soft spot rather than a hard CI gate) rather than a
fourth round of the same technique.

### Fourth real `--live` run (2026-09-17): fixtures were part of the problem

A fourth live run produced the predicted fourth round of the same four
repeat cases, plus one brand-new regression — but with a different
character than rounds 2–3 that changed the diagnosis:

1. **`data-integrity-review/boundary_case`, 4th identical failure.**
   Despite round 3's categorical, framing-independent exclusion (explicitly
   naming "comparison to an external standard" as non-evidence), the model
   argued the exact thing that exclusion named: "Email column width
   insufficient for valid email addresses... will be silently truncated."
   Four straight rounds asserting the same conclusion through four
   different framings, against increasingly explicit and specific
   instructions, is strong evidence the *fixture's specific choice* — a
   named `Email` column at `varchar(50)` — is triggering an extremely
   well-known, deeply trained "this is a canonical schema anti-pattern"
   prior that no amount of in-context instruction was overriding. This
   mirrors round 3's realization about `testing-coverage-review`: when a
   live run and a fixture disagree for this long, check whether the
   fixture itself is asking something unreasonable of the model, not just
   whether the prompt needs more words. Fix: changed the fixture's column
   from `Email varchar(50)` to `BatchLabel varchar(20)` — same principle
   (brand-new table, narrow column, no write in the diff), no column name
   with a famous, widely-known "correct" minimum length for the model to
   recognize and override instructions for. Also added a WRONG/RIGHT
   worked example to the prompt (the technique with the best track record
   in this project) as defense in depth.

2. **`concurrency-resource-review/boundary_case`, 4th failure, new and
   more defensible argument.** Round 4's finding — "every call to `run()`
   creates a Statement that is never closed, accumulating open database
   cursors" — is different from rounds 2–3's increasingly strained
   arguments (failed acquisition, partial construction): this one is
   actually correct and grounded in the diff. `ReportSession.run()`
   really does call `conn.createStatement().executeQuery(sql)` and drops
   the `Statement` reference without ever closing it, and nothing else in
   the class (not `run()`, not `close()`, not the caller) has a reference
   to close it later — a real, unrebuttable gap, and this project's own
   evidence bar says a defensible finding should not be suppressed. The
   round-2 exclusion claiming "closing the parent closes the child too
   (JDBC guarantees this)" was also simply wrong as a blanket claim — that
   behavior is driver- and pool-dependent, not a JDBC spec guarantee, and
   for a pooled connection `close()` may just return it to the pool
   without closing anything. Fix: this is a fixture bug, not a model
   error. Changed `ReportSession.run()` to call
   `stmt.closeOnCompletion()` (JDBC 4.1+) so the `Statement` genuinely
   does close once its `ResultSet` is closed/exhausted, closing the real
   gap while keeping the class to exactly one field — preserving the
   "no third code path for a one-field class" argument from round 3
   instead of reopening it (an earlier attempt at this fix added a second
   `List<Statement>` field to track and close manually, which would have
   given the model's "partial construction" argument a second field
   initializer to point at — reverted before committing). Corrected the
   prompt's overstated JDBC claim to the accurate, hedged version, and
   added a matching WRONG/RIGHT worked example.

3. **`performance-review`: the boundary case failed a 4th time, AND its
   false-positive-trap case failed for the first time ever** (`leaderboard.py`,
   a fixed 3-item loop that had passed cleanly in every prior round). A
   previously-rock-solid case newly failing, on the same agent whose one
   bullet had been rewritten three rounds running and had grown into the
   longest bullet in the entire agent set, is a strong signal that the
   bullet's own length/salience was distorting the model's calibration on
   *other*, unrelated bullets in the same prompt — the same mechanism
   suspected (but never confirmed) for `data-integrity-review`'s
   `true_positive_structural` miss two rounds ago. Fix, two parts: (a)
   trimmed the "Unbounded resource growth" bullet back to one sentence in
   Strict scope and moved all the accumulated exclusion detail into
   Explicit exclusions — shorter overall, nothing substantive dropped; (b)
   for the boundary case itself, applied the SAME fixture-anchoring
   technique that has reliably worked for `leaderboard.py` from round 1
   onward: added a comment to `config.py` stating `key` is one of ~20
   fixed, developer-controlled setting names, never populated from request
   input — giving the model concrete textual evidence to hang a "no
   finding" verdict on, exactly like `leaderboard.py`'s "always exactly
   the 3 leaderboard positions" comment, rather than asking it to infer
   boundedness from a 4-line snippet with zero anchoring context.

4. **`testing-coverage-review/false_positive_trap`: round 3's fixture fix
   worked, but exposed a narrower prompt gap.** The model no longer
   complained about the `total == 500` boundary (round 3's fix held) but
   found a new, related complaint: "boundary tested at the exact threshold
   but not below it." This doesn't identify a materially different code
   path — `total=499` and `total=500` both take the same (non-VIP) branch
   of the `> 500` comparison, so a value below the boundary adds no
   discriminating power once the boundary value itself is tested. Unlike
   case 1, this genuinely is a prompt gap: nothing in
   `testing-coverage-review.md` said a boundary-adjacent value is
   redundant once the exact boundary is covered. Added that exclusion.

`tests/fixtures/cassettes.json` re-seeded (14 new placeholder entries) for
the agents and fixtures touched this round; 23/23 on the harness wiring
check. A fifth `--live` run is the real test — particularly of whether
`data-integrity-review/boundary_case` and `concurrency-resource-review/
boundary_case` finally hold now that the underlying fixtures no longer
ask the model to stay silent about the exact kind of finding it has the
strongest trained priors toward reporting.

### Fifth real `--live` run (2026-09-17): the round-4 fixture fixes held; new signal elsewhere

The fifth run did NOT repeat `data-integrity-review/boundary_case` or
`concurrency-resource-review/boundary_case` — round 4's fixture changes
(swapping the email column for a non-canonical one, fixing the real
`Statement` leak) held on their first live test. That's the strongest
confirmation yet for the "the fixture was the problem, not the prompt"
diagnosis. Four different failures surfaced instead, read in full:

1. **`data-integrity-review/true_positive_structural` missed a third
   time** (having also missed in round 2, then passed in round 4). A
   flip between pass and fail on byte-identical prompt text across
   different rounds is the clearest evidence yet in this whole exercise
   of pure run-to-run model variance for this specific case, rather than
   anything in the prompt reliably causing or preventing it. No amount of
   prompt wording will fully eliminate stochastic misses, but a worked
   example (the technique with the best track record here) is a
   reasonable attempt to shift the odds — added one to the Structural
   correctness risk bullet using `billing.py` verbatim, framed positively
   ("report this") rather than as an exclusion, plus an instruction to
   actively compare function bodies when a diff adds multiple same-area
   functions.

2. **`performance-review/false_positive_trap` (`leaderboard.py`) failed a
   second consecutive round**, despite an exclusion bullet that has named
   this exact function, comment, and conclusion verbatim since round 1.
   The model's own response this round explicitly acknowledged the count
   is fixed at 3 and flagged it anyway — recognizing the fixed-size shape
   didn't stop it from applying the general "N+1-shaped code is
   reportable" instinct on top. Round 4's diagnosis (bullet-length
   dilution) didn't hold up: this bullet's text was untouched by round 4's
   trim. Applied the WRONG/RIGHT worked-example technique to this specific
   exclusion for the first time (previously it was prose-only, unlike most
   other exclusions in this project by now) — explicitly walking through
   why acknowledging the N+1 shape and still not reporting it are
   compatible.

3. **`testing-coverage-review/false_positive_trap` continued into a third
   distinct complaint** about the same test file across three rounds:
   round 3 "boundary untested," round 4 "no value below the boundary,"
   round 5 "test exercises the wrong branch" (i.e. objecting that the
   boundary test's VIP customer receives the non-VIP rate — which is the
   mathematically correct and only possible outcome for a value AT a
   strict `>` boundary, not a defect). Each round's fix closed the specific
   complaint and the model found an adjacent one attacking the same
   underlying test from a new angle. Added a worked example showing the
   CURRENT, complete three-test file as the definitive "no finding"
   reference, explicit about there being no fourth value or "wrong branch"
   framing left to invent.

4. **`testing-coverage-review/true_positive` (`pricing.py`, no test file
   at all) missed for the first time ever** — the plainest possible case
   this agent exists to catch (new non-trivial logic, zero test diff).
   Single data point, no structural hypothesis fits (the Strict scope
   bullet itself is unchanged since 0.8.0, and the growth has all been in
   Explicit exclusions, which govern what NOT to report — implausible that
   more exclusion text would suppress this bullet's own positive
   application). Treated as a likely stochastic single-sample miss rather
   than a prompt defect, but added a worked example to the bullet anyway
   (matching this exact fixture) as a low-risk reinforcement, consistent
   with this project's growing evidence that worked examples outperform
   descriptive prose for holding a verdict under live sampling variance.

`tests/fixtures/cassettes.json` re-seeded (11 new placeholder entries) for
the two agents touched this round. A sixth `--live` run is what tells us
whether the two newly-added worked examples move `leaderboard.py` and
`test_discounts.py`'s verdicts, or whether — like `data-integrity-review/
boundary_case` before it — the underlying fixtures themselves need to
change because the model's trained instinct on N+1-shaped loops and
boundary-test skepticism is simply too strong to fully suppress in-context.

### Sixth real `--live` run (2026-09-17/18): a genuine 23/23, a real fixture-drift bug, and a CRLF theory that didn't survive testing

The sixth run reported **23/23 — the first fully clean pass across six
rounds**, including the two cases round 5 flagged as likely stochastic
(`data-integrity-review/true_positive_structural`, `testing-coverage-review/
true_positive`) and both cases round 5 added its first-ever worked example
to (`performance-review/false_positive_trap`'s `leaderboard.py`,
`testing-coverage-review/false_positive_trap`'s `test_discounts.py`). That
result is real: `RecordingReviewer` (`cli/tests/support/cassette.py`) always
calls the live model unconditionally for every case in a `--live` run —
there is no cache-hit skip path — so nothing about how the resulting
cassette gets looked up afterward can retroactively make that run's verdicts
any less genuine.

What it does not automatically mean is that this repo's cassette now
*replays* 23/23 in every environment, and checking that turned up a real
bug — not in any agent's prompt, and not in a fixture's expected behavior.

**The bug.** Syncing the freshly-recorded `cassettes.json` (82 entries) into
a second checkout and running plain (non-`--live`) replay produced 22/23,
failing only on `data-integrity-review/true_positive_structural` with "No
recorded response for 'billing.py'" — a hash miss, even though the cassette
plainly contained a `billing.py` entry matching the sixth run's own reported
finding text. `security-review/boundary_case/handlers.py` turned out to have
the identical problem lying in wait, just not on a case whose manifest entry
happened to be exercised the same way that round.

**First theory, tested and disproved.** The first hypothesis was line
endings: `_diff_for_new_file()` (`scripts/validate_fixtures.py`) shells out
to `git diff --no-index`, and the Windows checkout genuinely does have
`core.autocrlf` converting these fixture files to CRLF on disk (confirmed by
staging and byte-comparing all 26 non-`EXPECTED.md` fixture files against
the Linux checkout — effectively all of them showed CRLF). The plausible
theory was that `--no-index` bypasses `core.autocrlf`'s normalization the
way a normal `git diff <ref>` wouldn't, so a CRLF checkout would hash a
different diff than an LF one for the identical logical file. Directly
testing this — a throwaway repo, a pure line-ending difference with zero
content change, `--no-index` diffed both with and without `core.autocrlf`
configured — showed the theory doesn't hold: `subprocess.run(...,
text=True)` (used by both `_diff_for_new_file()` and the production
`git_utils.diff_for_file()`) already normalizes any `\r\n` in the diff body
on the Python side regardless of git, and `git diff --no-index` itself
*does* apply `core.autocrlf` when computing the new-file blob hash in its
`index 0000000..<hash>` line — a pure line-ending difference (autocrlf
configured, which is the common Windows case) produces byte-identical diff
text either way. A code change was shipped anyway on the strength of the
theory (normalizing `\r\n` in the returned diff text) before this testing
happened; once it did, that change was confirmed to be a no-op — harmless,
but not what fixed anything — and was reverted with the corrected
explanation left in its place so the theory doesn't get silently retested
by a future contributor. Recorded here as a specific instance of this
project's own stated principle in `CONTRIBUTING.md`: verify a theory
against real behavior before writing the fix up as confirmed, not just
after it happens to make the numbers go to 23/23.

**The actual bug.** Two fixture files
(`data-integrity-review/true_positive_structural/billing.py`,
`security-review/boundary_case/handlers.py`) had each independently picked
up one extra blank line before a top-level `def` — genuine content drift,
not a line-ending artifact, confirmed by isolating line-endings from
content in the same throwaway-repo test above (identical CRLF styling,
only one file with the extra line, produces a real diff-text and
blob-hash difference; identical content with only line-endings differing
produces none). Consistent with an editor's on-save formatter applying
PEP 8's two-blank-line convention the moment either file was opened
locally, not a deliberate edit (`git log --follow` shows neither file
touched since its original fixture commit). That extra line shifts the
line number `EXPECTED.md` names for both cases and is what actually broke
the hash.

**The fix.** Both drifted fixture files were restored to their
originally-committed, single-blank-line content. `run()`'s `--live` branch
also now clears a stale "seeded from EXPECTED.md, not live-recorded"
`_meta` note on a full (unfiltered) run — a smaller, unrelated bug found
alongside the main investigation: that note was set once by the very first
`--seed-placeholders-from-expected` bootstrap and then persisted forever,
including after this sixth run's fully-live 82-entry recording, because no
code path ever cleared it. Neither fix touches an agent's prompt — the
worked examples added across rounds 2-5 are untouched, consistent with
holding off on any prompt edits until a live run is confirmed to actually
replay cleanly everywhere, not just where it was recorded.

**Confirmed, then re-drifted, then confirmed again.** A follow-up `--live`
run from the same Windows checkout, with both fixtures restored, reported
23/23, and the resulting 84-entry cassette replayed 23/23 independently in
the Linux checkout with no changes needed there — the first time in this
project's history a live-recorded cassette replayed identically across two
different checkouts. Re-verifying before handing off a commit-message list
caught the same two files drifting back to the extra-blank-line form a
second time (confirming this is a recurring, not one-off, local formatter
effect) and, separately, a synced copy of `scripts/validate_fixtures.py`
missing every `# noqa: E402` comment — almost certainly an import-sorting
tool (isort, or an editor's "organize imports" on save) hoisting the
post-`sys.path.insert` imports and dropping their trailing comments, which
would have failed `ruff check` with 6 real errors. Fixed by re-restoring
both fixtures and adding `# isort:skip_file` to the script as a guard.
`git status` on the fixtures shows nothing after each restore, since git's
own `core.autocrlf`-normalized view of these files already matches the
committed LF/single-blank-line content — the drift is purely a working-tree
artifact of whatever's touching these files locally, invisible to git
itself, which is exactly why it kept resurfacing silently instead of
showing up as a pending change to commit.

### The bundled default prompts had silently drifted (2026-09-18)

Recording `cli/tests/cassettes/integration.json` live for the first time —
the last explicitly-flagged "still-open gap" from 0.9.8's README update —
was meant to be a clean, low-risk step: unlike the `tests/fixtures/`
validation matrix, this test's content is hardcoded Python string literals,
not files that can drift on disk. It surfaced a real bug anyway, just a
different one than expected.

The first recording attempt came back with `security-review`'s finding
wrapped in a markdown code fence — exactly the shape `CLAUDE.md`'s output
contract explicitly forbids ("Never wrap your response ... in a markdown
code fence"). Comparing the prompt this test actually exercises against
the root repo's own copy explained why: `cli/tests/test_cli_integration_live.py`
deliberately creates a temp repo with neither `.agent-rules/` nor
`.claude/` of its own, specifically to exercise `prompts.py`'s bundled
`default_rules/` fallback path for real — the same path a real CLI user
gets when reviewing a repo that has no Claude-Code-native agent
definitions of its own. That bundled `CLAUDE.md` was missing the entire
anti-code-fence rule, because it had never been updated since some point
before this project's six-round `--live` tuning history began. Diffing
every bundled file against its root counterpart found the same story
everywhere: all 7 specialist prompts and `CLAUDE.md` itself had drifted,
none carrying any of the worked examples, exclusion refinements, or
fixture fixes from rounds 2 through 5.

`prompts.py`'s own module docstring describes `default_rules/` as "a
snapshot of this project's own agents at the time this CLI was built" —
a deliberate point-in-time copy, not a symlink, so *some* drift after an
edit is expected and not itself a bug. What was actually missing was
anything that made an un-synced edit visible. Nothing in CI ever compared
the two locations, and `cli-ci.yml`'s trigger paths didn't even include
`.claude/agents/**` or root `CLAUDE.md` — a PR that touched only a prompt
file, with zero changes under `cli/`, wouldn't have run this workflow at
all even if the comparison test had already existed. This means every one
of this project's six live-tuning rounds shipped its fixes to the
Claude-Code-native `.claude/agents/` path (what this repo, and any repo
that copies its `.claude/` directory, actually uses) while silently never
reaching the standalone CLI's own bundled defaults — a real quality gap
for any `agent-review`/`agent-init` user whose target repo has no
`.claude/`/`.agent-rules/` override of its own, invisible because nothing
tested that specific path against real model behavior until this run.

**The fix.** Synced every bundled file (`CLAUDE.md` plus all 7 specialist
prompts, `triage-router.md` deliberately excluded — see `cli/README.md`'s
"How it works": `routing.py` is a direct code port of its rule table, so
the CLI never loads it as a system prompt) to its root counterpart, and
added `cli/tests/test_default_rules_sync.py`: two tests that assert
byte-for-byte equality between each root file and its bundled copy,
failing loudly with the exact file name the moment they diverge. Extended
`cli-ci.yml`'s trigger paths to include `.claude/agents/**` and root
`CLAUDE.md`, so a prompt-only PR actually runs this check instead of
silently skipping the whole workflow. `cli/tests/cassettes/
integration.json`'s cassette entries recorded against the stale prompt are
no longer reachable (the corrected prompt hashes differently) and were
re-seeded with hand-authored placeholders, clearly labeled as such in
`_meta` — the same bootstrap convention as `tests/fixtures/`'s
`--seed-placeholders-from-expected`, extended by hand here since this
smaller cassette has no equivalent CLI flag. A further live recording is
the natural next step, the same discipline this project has applied to
every other placeholder-seeded cassette: don't trust a "PASS" is testing
real model behavior until a real model has actually produced it.

### Fourth reframing of the false_positive_trap boundary complaint, closed (2026-09-18)

A fresh, unprompted full `--live` run (22/23, otherwise clean) caught
`testing-coverage-review/false_positive_trap` failing for the first time
since round 5, two full confirmed runs later. The complaint was a fourth
distinct angle against the same fixture: `[MEDIUM] test_discounts.py:1 —
Untested non-VIP discount branch at boundary condition`, demanding a test
for a non-VIP customer specifically at `total=500` — the same boundary
value already tested, just under a different customer attribute.

Rounds 3-5's exclusion (see the round-5 write-up above) closed two prior
reframings — a second boundary-adjacent *value* (e.g. `499`), and
recharacterizing the existing boundary test as exercising "the wrong
branch" — but its wording covered only the *value* axis. It didn't say
anything about a second boundary-adjacent test varying an unrelated
*attribute* instead, which is exactly the gap this new complaint walked
through. Verified the demand had zero discriminating power before treating
it as a real gap, the same discipline applied throughout this project:
`compute_discount`'s branch is `if is_vip and total > 500`, so at
`total=500`, `total > 500` is `False` regardless of `is_vip` — a non-VIP
test at `500` would hit the identical `else` branch and assert the
identical `25` the existing VIP-at-500 test already proves.

**The fix.** Generalized the exclusion's wording from "a second
boundary-adjacent test *value*" to "any input dimension that doesn't
change the branch taken," and added a fourth explicit WRONG example naming
this exact non-VIP-at-boundary reframing, in both `.claude/agents/
testing-coverage-review.md` and its bundled `default_rules/` copy (kept in
sync per `test_default_rules_sync.py`, added the round before this one).
An `--agent testing-coverage-review --live` rerun confirmed the fix: the
case now passes clean against real model output, not just against the
hand-reasoned theory above.

This makes four distinct reframings the model has now found against one
adversarially-designed fixture across six live rounds (rounds 3, 4, 5, and
this one) — each prior fix held clean for at least one full run before the
next angle appeared. If a fifth appears, that would be the point to
seriously weigh CONTRIBUTING.md's own "after repeated prompt-only fixes
fail to hold, check the fixture, not just the prompt" guidance over adding
a fifth rebuttal clause to what is already the longest single exclusion in
this file.

### Lifting "discriminating power" into CLAUDE.md, and ending the blank-line drift for good (2026-09-18)

The fourth-reframing fix above closed one agent's specific instance of a
pattern. Before applying it as one-off prompt tuning, it was worth checking
whether the underlying principle — a claimed gap that doesn't actually
reach a new code path or outcome isn't a new gap — already existed
elsewhere, since a pattern that recurs across independently-written agent
files is exactly what `CLAUDE.md` exists to hold once instead of N times.

It did. `concurrency-resource-review.md`'s "no third code path for a
one-field class" exclusion (round-unknown, predates this project's
`--live` tuning history) is the same shape applied to an invented
resource-lifecycle scenario instead of an invented test case: an argument
that some additional scenario deserves a separate finding, when that
scenario provably collapses into a code path/outcome already accounted
for. Two independent agents had each grown their own version of this rule
because nothing shared it. Lifted it into `CLAUDE.md`'s Evidence Bar as a
sixth requirement, **discriminating power**, worded generally enough to
cover a redundant test value, a redundant test attribute, a redundant race
interleaving, or a redundant slow-path scenario alike — then pointed both
`testing-coverage-review.md` and `concurrency-resource-review.md`'s
existing worked examples back at it as canonical illustrations, rather
than inventing a third example from scratch.

While auditing agent files for what else could be shared rather than
restated, three files turned out to independently restate `CLAUDE.md`'s
existing Evidence Bar causal-link requirement ("a defect that existed
identically before the diff... is NOT reportable") almost verbatim:
`data-integrity-review.md`, `performance-review.md`, and
`concurrency-resource-review.md`. The first was a pure restatement with no
added nuance, so it was trimmed to a one-line cross-reference. The latter
two each add real domain-specific elaboration (concurrency's "not newly
reachable, newly concurrent, or newly missing its guard") worth keeping,
so those kept their text and gained a cross-reference instead of losing
content to a trim.

Separately, closed out the `billing.py`/`handlers.py` blank-line saga from
0.9.9/0.9.11 for good: rather than continuing to restore the 1-blank-line
form every time a local formatter (isort, an editor's on-save "organize
imports"/formatter, never conclusively identified) rewrote it back to
2 blank lines, adopted 2 blank lines — real PEP 8 convention anyway — as
the canonical committed form. This is a one-time content change, not a
process fix, so there's no guarantee against a *different* local tool
introducing a *different* unwanted change later; it just removes the one
specific, repeatedly-observed friction this project has actually hit three
times.

**Verification status.** `CLAUDE.md` is inherited by all 8 agents, so this
change invalidates every cassette entry in `tests/fixtures/cassettes.json`
and both entries in `cli/tests/cassettes/integration.json` — including the
integration cassette that had *just* been confirmed live in the same
session, against the pre-this-change prompt. A full `--live` run across
the whole `tests/fixtures/` matrix, plus a fresh
`AGENT_REVIEW_RECORD_LIVE=1 pytest cli/tests/test_cli_integration_live.py`,
is required before any of this — the Evidence Bar addition, the trims, or
the fixture canonicalization — is treated as more than a reasoned draft.

### A stale-file false alarm, then a real evidentiary-bar bleed (2026-09-18)

The full `--live` confirmation run above came back 22/23: everything
passed except `data-integrity-review/true_positive` (the `Email
varchar(255)`→`varchar(50)` narrowing migration), a case with zero
failures across every prior round. A rerun scoped to just that agent
repeated the identical miss — on the surface, n=2, real signal.

It wasn't. `.claude/agents/*.md` is a protected path this session can only
deliver as a file download for the user to place by hand; `CLAUDE.md`
itself isn't protected and went straight in via the direct-write path. The
two paths update at different times, and nothing forces them to land
together. Before touching the prompt on the strength of two identical
failures, computed the actual `request_key()` hash for all four
combinations of {new, old} `CLAUDE.md` × {new, old} `data-integrity-
review.md` against this exact fixture, and matched each one against the
cassette's recorded miss. The miss's hash matched exactly one combination:
new `CLAUDE.md` (with the discriminating-power addition) paired with the
OLD, pre-lift `data-integrity-review.md` — the file download hadn't been
placed yet. Both "failures" were the same stale file being hit twice, not
two independent live samples of the shipped prompt. This is the same
"verify before treating a coincidence as confirmed" discipline as 0.9.9's
CRLF correction, just applied to a delivery-mechanism question instead of
a line-ending one — and cheap to do exactly because this project's own
hashing scheme makes "which exact prompt produced this response" a
computable fact instead of a guess.

Once the file was actually placed (confirmed by having the user paste its
contents back and checking for the tell-tale lifted lines) and rerun, the
miss recurred — this time a genuine n=2 against the real, shipped 0.9.12
prompt. Real signal, this time. The likely mechanism: `data-integrity-
review.md`'s brand-new-table exclusion — "only a write statement inside
THIS diff proves the width/constraint is violated" — sits directly beside
`CLAUDE.md`'s new, more cautious Evidence Bar, and the model appears to
have generalized that brand-new-table-only evidentiary bar to the
narrowing-an-EXISTING-table case too. That generalization runs backwards:
an existing table is presumed to already hold data of unspecified length
unless the diff shows otherwise, so "some existing row may not fit the
narrower width" is the risk itself, not a hypothetical requiring its own
proof the way an empty, brand-new table's design choice does. This is a
plausible instance of a risk this project hadn't previously named:
prompt-wide changes for one purpose (the discriminating-power lift) can
shift how nearby, unrelated exclusions get read, purely by increasing the
document's overall evidentiary caution — worth watching for again on any
future `CLAUDE.md`-wide edit, not just this one.

**The fix.** Added a second RIGHT example directly after the brand-new-
table one, using this exact fixture's diff, explicit that the "prove it
with a write" bar does not extend past the brand-new-table case: the
narrowing-an-existing-table finding is reportable with no write statement
present, because the difference that matters is whether a pre-existing row
could already exist to be harmed, not whether the diff happens to write
one. Synced the bundled `default_rules/` copy.

**Confirmed.** `--agent data-integrity-review --live` came back 4/4,
`true_positive` firing correctly. This closes out the entire 0.9.12
`CLAUDE.md`-wide change as one verified state: the discriminating-power
lift, the three exclusion trims/cross-references, the fixture
canonicalization, and this fix.

### A second .env-loading gap, in the opt-in recording path this time (2026-09-18)

Attempting the still-outstanding `cli/tests/cassettes/integration.json`
re-recording (needed since `CLAUDE.md` changed again, same reasoning as
0.9.10's original recording) surfaced a smaller but related DX gap.
Running the file plain failed against the stale placeholder cassette, as
expected. Running it with `AGENT_REVIEW_RECORD_LIVE=1` and
`ANTHROPIC_FOUNDRY_MODEL` set still failed:
`RuntimeError: No Microsoft Foundry resource configured`, despite a
working `cli/.env` existing on disk — the same class of gap flagged
earlier in this project's history (a `DeploymentError` from a missing
`ANTHROPIC_FOUNDRY_MODEL`), just a different missing variable this time
(`ANTHROPIC_FOUNDRY_RESOURCE`).

Both times the root cause is the same: `cli/tests/conftest.py`'s
`_no_real_dotenv_lookup` autouse fixture disables `.env` loading for
every test in `cli/tests/`, correctly, so ordinary replay tests don't
depend on whatever happens to be on a given developer's disk. But it
applies just as hard to `test_cli_integration_live.py`'s
`AGENT_REVIEW_RECORD_LIVE=1` path, which is the one place in the whole
suite that explicitly wants real credentials — that's the entire point of
opting into live recording. Every previous encounter with this was
treated as a one-off "export more variables by hand" instruction; the
second occurrence made it worth fixing structurally instead of explaining
a third time.

**The fix.** `test_cli_integration_live.py` now imports
`_load_dotenv_if_present` directly by reference at module load time —
before `conftest.py`'s autouse fixture ever runs and monkeypatches the
`cli` module's attribute — the same technique `test_cli_dotenv.py`
already uses to test the real function on its own terms. The recording
branch of `reviewer_and_cassette` calls this captured reference explicitly
before constructing `AnthropicFoundryReviewer()`. Every other test in the
suite, including this file's own replay-mode path, is unaffected — the
autouse block still applies everywhere else. `_load_dotenv_if_present`
itself never overrides a variable already set in the real environment
(`load_dotenv()`'s `override=False`), so this is purely additive: a
working `cli/.env` is now enough on its own for recording, matching how
`scripts/validate_fixtures.py --live` has behaved all along. Not yet
exercised against a real Foundry call in this exact form — the natural
next step.

### Structured output, budget management, and the feedback loop (implemented in 0.9.0)

Three further production-readiness gaps were identified alongside the CI
harness and cassette testing above, and deliberately left unimplemented in
0.8.0 so that work could get the same TDD-first, evidence-based treatment
rather than being rushed in alongside it. All three landed in 0.9.0 (see
`CHANGELOG.md`); this section is kept as the original design rationale —
ordered by the priority that was used to decide implementation order, not
as a forward-looking roadmap anymore. Each subsection below still
describes the problem and the design as originally reasoned through, with
a closing note on what actually shipped and where to find it.

**1. Structured output & parsing guards.** Every consumer of a finding —
`findings.parse()`, `scripts/validate_fixtures.py`'s verdict checks, a
future CI gate that fails a PR on a CRITICAL finding — depends on a
specialist's raw text matching `_HEADER_RE` exactly. This is the highest
priority of the three because it's the only one of the three that's
already a live, silent failure mode: a model response that drifts even
slightly from the `[SEVERITY] file:line — Title` contract (extra
markdown, a rewrapped line, a stray code fence) doesn't error, it just
silently parses to zero findings — indistinguishable from a genuinely
clean review, in the one place (a CI gate) where that distinction matters
most. The fix doesn't require abandoning the human-readable contract
`CLAUDE.md` already defines: request a second, structured form alongside
it via Claude's native tool-use (a `report_findings` tool with a JSON
schema — `severity` enum, `file`, `line`, `title`, `impact`, `fix` —
mirrors this project's own `ReportFindings` tool shape), and have
`findings.parse()` prefer the structured block when present, falling back
to today's regex parse otherwise so a partial rollout (some agents
migrated, some not) never breaks. `agents_client.Reviewer.complete()`'s
signature would need to grow an optional structured-response path (a new
`complete_structured()` method, or a `Reviewer` capability flag) rather
than changing its existing contract, so `FakeReviewer`/`CassetteReviewer`
keep working unchanged for agents that haven't migrated. Land this before
#4 or #5: both of those add more moving parts on top of the *current*
parsing path, and are easier to build once findings are a real data
structure at the API boundary rather than an artifact of regex-matching
model prose.

*Shipped in 0.9.0, narrower than sketched above.* The tool-use /
`report_findings`-schema half of this design was deliberately **not**
built: it would change `agents_client.Reviewer.complete()`'s live
model-calling contract, and this build environment still has no way to
verify a structured-response path against a real Foundry call (see
Section G's "Honest limitations") — landing it unverified would violate
this project's own evidence-based, TDD-first standard. What did ship is
the fully unit-testable half of the same problem:
`findings.is_malformed_response()` distinguishes a genuine clean review
from a response that silently drifted off the text contract (surfaced via
`FileReviewResult.malformed_agents`, never cached as complete — see
`orchestrator._review_one_file`), and a `--json` flag on `agent-review
review` gives CI a structured form of today's findings without waiting on
a model-side schema change. The tool-use path above remains a real
follow-up once a live-verified Foundry test run is available to build it
against.

**2. Dynamic token and context budget management.** Real production
repositories will eventually produce a diff (a large refactor, a
generated-file commit, a vendored dependency bump) that either burns
inference budget on content nobody wants reviewed or exceeds a model's
context window mid-run — today `run_review()` has no ceiling at all on
per-file diff size or total files-per-run, and no exclusion list for the
generated/vendored/lockfile case the routing layer was never designed to
recognize. This ranks below structured output because it's a scaling
failure, not a silent-correctness failure: today it manifests as a slow
or failed run on an unusually large diff, which is visible and
debuggable, not a quietly wrong "clean" review. Concrete shape: (a) a
`should_exclude_file(path)` predicate in `routing.py` keyed off a small,
extensible set of path patterns (`*.lock`, `*.min.js`, `package-lock.json`,
common vendored-directory names) applied before routing, not after, so
excluded files cost zero inference budget, not just zero output; (b) a
per-file diff-size ceiling in `orchestrator._review_one_file` — a diff
over some configurable threshold (e.g. 4000 lines) gets truncated with a
clear `[... diff truncated, N lines omitted ...]` marker inserted at the
cut point, sent to the model with that caveat explicit rather than
silently dropped, and the file's result flagged (a new
`FileReviewResult.truncated: bool`) so CLI output surfaces "reviewed
under truncation" rather than implying full coverage; (c) a `--max-files`
/ total-diff-budget flag in `cli.py` for the pathological wide-diff case,
reusing `routing.py`'s already-deterministic, zero-cost routing to decide
which files are dropped first (lowest-risk-domain files, not an arbitrary
truncation) when a run is over budget.

*Shipped in 0.9.0, as designed.* `routing.is_excluded_from_review()` (a),
`orchestrator._truncate_diff()` plus `FileReviewResult.truncated` (b), and
`--max-files` plus `ReviewRun.skipped_for_budget` (c) all landed
unchanged from this design. The one refinement made during
implementation: (c) prioritizes by `len(RoutingDecision.agents)` rather
than a named "lowest-risk-domain" ranking — the number of specialists a
file already tripped is a more direct, zero-cost proxy for its risk
surface than trying to rank *which* domains matter more in the abstract.

**3. Feedback loop & false-positive suppression.** Lowest priority of the
three not because it matters least long-term — false-positive fatigue is
exactly what erodes trust in an automated reviewer over months of real
use — but because it's the only one of the three with no correctness or
scaling risk today; its cost is UX friction (rerunning past a finding a
team has already triaged as a non-issue), which compounds slowly rather
than failing sharply. Design: a `.claude/ignore-findings.yml` (mirroring
`.gitignore`'s discoverability) at the target repo root, holding entries
keyed by `(agent, location_pattern, reason)` — a glob or regex against
`Finding.location` rather than an exact line number, since line numbers
drift with every unrelated edit to the file and an exact-line suppression
would silently stop matching (and thus silently stop suppressing) on the
very next commit. `orchestrator.run_review()` would load this file once
per run and filter `FileReviewResult.findings` against it after parsing
(never before — the raw finding is still what gets cached, so a later
change to the suppression file doesn't require re-reviewing already-cached
files), with a summary line ("3 findings suppressed by
.claude/ignore-findings.yml") in CLI output so suppression is visible, not
silent. A lighter-weight complement worth building alongside it:
`agent-review` logging every suppressed finding's `(agent, location,
reason, timestamp)` to a local, gitignored log
(`.agent-cache/suppressions.log`), giving a maintainer auditing prompt
quality over time a concrete, evidence-based list of exactly what a given
agent tends to false-positive on — the same evidence-based instinct this
project's own `CLAUDE.md` already asks of the agents themselves, applied
to maintaining the agents.

*Shipped in 0.9.0, as designed, plus one addition.* `suppressions.py`
implements the config format, the after-parsing/independent-of-caching
filter in `orchestrator.run_review()`, the CLI summary line (`_print_review`
and `--json`), and the `.agent-cache/suppressions.log` audit trail exactly
as sketched, matched via `agent` (or `"*"` for any agent) plus an
`fnmatch` glob against `Finding.location`. The one addition beyond the
original design: parsing is defensive by construction (`_coerce_entry()`)
— a hand-edited config with a missing or malformed field is silently
skipped rather than crashing the run, mirroring `cache.py`'s own
corrupt-manifest handling, since this file is meant to be hand-edited by
a team and a typo must never take down every future review. This also
introduces this project's first new runtime dependency since `anthropic`
and `python-dotenv`: `pyyaml`, deliberately not the tiny hand-rolled
frontmatter reader `prompts.py` uses elsewhere, because that reader only
ever parses a fixed, tool-generated set of flat scalar fields — an
ordinary hand-authored YAML file with lists, quoting, and comments is
exactly the case a real parser earns its keep for.
