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
Expected: `[MEDIUM] billing.py:8 — compute_invoice_total duplicates compute_checkout_total's VIP-discount rule`
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
is required either way, then either an **API key**
(`ANTHROPIC_FOUNDRY_API_KEY` / `--resource`'s sibling `api_key=`, the
default) or **Entra ID** (Azure AD) via `azure-identity`'s
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
cli/src/agent_review/discovery.py       # language + test-runner auto-discovery
cli/src/agent_review/prompts.py         # .agent-rules/ -> .claude/ -> bundled default_rules/ lookup
cli/src/agent_review/findings.py        # output-contract parser/sorter (shared with the orchestrator)
cli/src/agent_review/agents_client.py   # Reviewer protocol + real AnthropicFoundryReviewer (Azure only)
cli/src/agent_review/commit.py          # staged-diff commit message generator (no co-author trailer)
cli/src/agent_review/healing.py         # guarded self-healing: propose a patch, apply only if --apply
cli/src/agent_review/init.py            # agent-init: scaffolds .agent-rules/, .agent-cache/, DESIGN.md
cli/src/agent_review/default_rules/     # bundled snapshot of CLAUDE.md + the 7 specialist prompts
cli/tests/                              # 80 pytest tests, including full CLI-entry-point integration tests
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
real functional gap, not an edge case). `discovery.py` detects language mix
and test runner via marker files (`pyproject.toml`/`pytest.ini` → pytest,
`package.json` → npm test, `pom.xml` → Maven, `build.gradle[.kts]` →
Gradle, `Cargo.toml` → cargo, `CMakeLists.txt` → ctest, `*.csproj`/`*.sln`
→ dotnet). `commit.py` generates a semantic commit message for the target
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
  configured, so every automated test still exercises orchestration logic
  (routing, caching, git diffing, patch application) against a
  hand-written `FakeReviewer`/`ScriptedReviewer` rather than the network;
  `cli/tests/test_agents_client.py` still only covers
  `AnthropicFoundryReviewer`'s constructor-time validation and its error
  handling, not a real `messages.create()` call. But the real client has
  now been exercised for real, by an actual user, against an actual
  resource, and returned a real (clean) review -- getting there also
  surfaced and fixed several real config/UX gaps along the way: a
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
