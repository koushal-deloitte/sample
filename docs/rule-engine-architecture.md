# Architecture: AI-Assisted Business Rules Platform on GoRules Zen Engine (Python Runtime)

## Scope and grounding

This document designs a business-rules execution platform on the **GoRules Zen Engine** (`github.com/gorules/zen`), using its **Python bindings** (`pip install zen-engine`, `import zen`) as the execution runtime — not Go. Rules are authored as JSON Decision Model (JDM) graphs; the engine core is Rust, exposed to Python via a native PyO3 extension.

Claims about the engine's actual behavior are tagged:
- **[confirmed]** — verified directly against `docs.gorules.io/reference/python`, the `bindings/python/README.md` and `bindings/python/zen.pyi` type stub in `github.com/gorules/zen`, and a live GitHub issue.
- **[inferred]** — reasonable engineering inference where the public docs were incomplete; verify against the exact pinned `zen-engine` version before relying on it.

Two facts materially shape this design:

1. **[confirmed]** The Python package is a **native extension** (PyO3/maturin build against the Rust core), published as prebuilt wheels for `linux-x64-gnu`, `linux-arm64-gnu`, `darwin-x64`, `darwin-arm64`, `win32-x64-msvc` — **musl is not supported**. This rules out Alpine-based container images; the deployment base image must be Debian/Ubuntu-slim or an equivalent glibc target.
2. **[confirmed]** `ZenEngine` exposes both sync and async evaluation: `evaluate()`, `evaluate_batch()`, `async_evaluate()` on `ZenEngine`, and `ZenDecision.evaluate()` with an async variant. `ZenEngineOptions` accepts `loader` (sync or async callback/config) and **`customHandler: Callable`**. **[confirmed via GitHub issue #269, opened Nov 2024, "documentation" label, still open]** custom-node support in the Python binding is real but under-documented and has shown a concrete serialization bug (`missing field 'config'`) when following the Go-style custom node pattern. Treat the Python custom-handler path as **less mature than Go's** — usable, but only after your own integration test against the exact pinned version, and never as the sole mechanism for a correctness-critical DB/API call.

This second point changes the center of gravity of the whole data-fetch design relative to a Go-based version: in Python, **pre-fetch/hydration into the input context is the primary integration mechanism, not just a performance optimization**, with the custom handler reserved for cases that genuinely cannot be pre-fetched, adopted only behind its own test suite.

---

## A. JDM primer and how rule dependencies map onto it

### A.1 Node types [confirmed]

| Node type | Purpose |
|---|---|
| `inputNode` / `outputNode` | Graph entry/exit, optional JSON-Schema validation |
| `decisionTableNode` | Spreadsheet-style rules under a hit policy (`first` / `collect`) |
| `expressionNode` | Pure data transform in the ZEN expression language |
| `switchNode` | Conditional branching |
| `functionNode` | Embedded JS (QuickJS) with `dayjs`/`big.js` — compute only, short hard timeout, not a sanctioned I/O path here |
| `decisionNode` | References another decision by key, resolved recursively through the same `loader`, depth-guarded |
| custom node | Delegates to the single process-wide `customHandler` |

### A.2 Two levels of "rule B depends on rule A"

- **In-graph (`decisionNode`)**: use for a sub-decision that's always part of one cohesive decision, owned by the same team/release cadence (e.g., a "final price" graph that always calls a "base discount" sub-decision). Resolved recursively by `loader`, bounded by a max-depth guard.
- **Cross-decision orchestration (a DAG built above Zen — the Rule Dependency Graph, "RDG")**: for business-level dependencies across independently owned/versioned rule sets, especially where DB/API calls happen *between* decisions. Not a Zen feature — an explicit metadata artifact this platform owns, stored beside (not inside) JDM content. Each RDG node references a `(decision_key, pinned_version)`, declares `depends_on`, field mappings from upstream outputs to downstream inputs, and a per-node timeout/retry/fail-mode policy.

**Rule of thumb:** `decisionNode` for same-team, always-invoked sub-decisions; the RDG for cross-team dependencies, independent versioning/rollback, or any dependency that requires an external call in between. Most deployments use both — RDG nodes that are themselves `decisionNode`-composed internally.

### A.3 Expression language boundary [confirmed]

The ZEN expression language (`expressionNode`, decision-table cells) is pure and side-effect-free: arithmetic/comparison/logical/ternary/null-coalescing operators, ranges, `map`/`filter`/`some`/`all`. No network or file I/O. **Rule for authors and the AI generator**: if a step only reshapes data already in context, it's an `expressionNode`/table cell — never a function or custom node. If it needs data not yet in context, it belongs in the pre-fetch stage (default) or, only when genuinely unavoidable, a tested custom node.

---

## B. Data-fetch model for DB/API calls (Python-specific)

### B.1 Primary mechanism: pre-fetch/hydration, driven by the Orchestrator

Because Python's custom-handler path is real but immature **[confirmed via #269]**, the default architecture fetches all *statically determinable* external data **before** calling `evaluate()`/`async_evaluate()`, in Python, using `asyncio`, and merges results into the input context dict:

1. For each RDG node, the Orchestrator knows in advance (from the RDG's declared field mappings and each decision's documented input schema) which external lookups it needs and which keys depend on upstream RDG node outputs already resolved by the time this node runs.
2. It issues those fetches concurrently via `asyncio.gather(...)`, through the Data-Fetch Layer (B.3), and merges the results into the `context` dict passed to `ZenDecision.evaluate(context, {"trace": True})` (or the async variant).
3. This avoids the native-extension boundary crossing entirely for the common case, and keeps 100% of I/O in ordinary, testable Python `async def` code — no serialization surface, no dependency on the under-documented `customHandler` contract.

### B.2 Secondary mechanism: `customHandler`, for genuinely dynamic mid-graph lookups

Reserved for the minority of cases where *which* record to fetch is only known after a mid-graph computation (e.g., "look up the pricing tier that this customer's computed segment maps to"), where pre-fetching every possible branch would be wasteful or impossible.

- Register **one** `customHandler` callable per `ZenEngine` instance — [confirmed] this is a single, process/engine-wide callback, mirroring Zen's Go binding design (one global dispatch point, not one per rule).
- Because the callback shape is under-documented for Python, do not assume Go's exact `NodeRequest{Node, Input}` shape carries over 1:1 — **write a small integration test against the pinned `zen-engine` version first** (evaluate a JDM fixture containing one custom node, log exactly what the callback receives and what shape it must return) before writing any production dispatch logic. Budget for this as an explicit spike task, not an assumption.
- **Correlation problem**: like Go, there is no confirmed per-call correlation/context parameter. Two options, in preference order:
  1. **Explicit correlation ID in the input context** (safe default): inject a `"__cid"` key into the top-level input dict before calling `evaluate`; since Zen cascades context variables to descendant nodes **[confirmed via engine architecture]**, the custom node's input includes it. The handler looks up a `RequestScope` (an `asyncio`-safe structure — plain dict guarded by a lock, or an `asyncio.Lock`-protected registry — keyed by `"__cid"`) holding the request's deadline, per-source loaders, and cache handle.
  2. **`contextvars.ContextVar` propagation** (potential optimization, unverified): Python's `contextvars` normally propagate through `asyncio` tasks automatically, which would let the handler read request-scoped state without an explicit ID. **Do not rely on this** until you've confirmed PyO3 invokes the Python callback on the same OS thread/task context as the calling coroutine — if the Rust side dispatches the callback from a Tokio worker thread via `Python::with_gil`, contextvars will **not** propagate correctly. Treat option 1 as the default; only adopt option 2 after an explicit experiment.
- Handler must catch and translate all exceptions into the documented error return rather than letting a Python exception cross back into the Rust/PyO3 boundary uncontrolled — verify the expected error-signaling convention in the same integration-test spike.

### B.3 DataLoader-style batching + memoization, request-scoped

Use `aiodataloader` (the asyncio port of the Facebook DataLoader pattern) or an equivalent small hand-rolled version, one loader instance per distinct downstream source (e.g. `CustomerByIdLoader`, `PricingApiLoader`), constructed fresh per request (per RDG execution) and discarded after:

- **Memoizes** within the request — the same customer/account/reference row needed by three dependent RDG nodes is fetched once.
- **Batches** where the backing call supports it (`SELECT * FROM customers WHERE id = ANY($1)` instead of N single-row queries), via `aiodataloader`'s built-in micro-batching window.
- Never shared across requests — cross-request caching is the separate, explicit tier below.

### B.4 Two-tier caching

| Tier | Scope | Lifetime | Use for |
|---|---|---|---|
| Request-scoped `DataLoader` | One RDG execution | ms–low seconds | Repeated lookups within one evaluation |
| Shared TTL cache | Process-wide / Redis | Explicit TTL, cache-aside | Slow-changing reference data (tax tables, product catalogs, rate cards) |

Recommended: `aiocache` (or a plain `cachetools.TTLCache` per pod) as L1 for hot reference data, Redis as L2 for cross-pod consistency, with version-tagged cache keys (e.g. `taxtable:v2026-07`) so an update produces a new key rather than requiring blind invalidation, and pub/sub-driven invalidation (Redis keyspace notifications) rather than TTL-only expiry for correctness-sensitive data like rate cards.

### B.5 Pooling, timeouts, cancellation, circuit breakers

- **DB**: `asyncpg` pool (or SQLAlchemy async engine) with a bounded pool size, one pool per logical database, created once at process startup and shared.
- **Internal REST APIs**: one `httpx.AsyncClient` per downstream service, connection-pooled, with `asyncio.timeout(...)`/per-request deadlines derived from the inbound request's overall evaluation budget (e.g. 2–5s).
- **Circuit breakers**: wrap each downstream client with `aiobreaker` (or `pybreaker` adapted for async) — a flaky pricing API should trip its breaker and fail fast/fall back rather than blocking every evaluation that touches it.
- **Fallback policy**: declared per RDG node — `fail-closed` (abort the whole run) vs `fail-open-with-default` (use last-known-good cached value, flag the result `degraded`) — a rule-owner decision recorded in RDG metadata, not a silent engineering default.

### B.6 Concurrency model

Unlike a cgo binding (which pins an OS thread per blocked call), Python's `async_evaluate`/async `ZenDecision.evaluate` are awaitable **[confirmed]**, so the event loop is not blocked while the Rust core runs. This makes the natural scaling unit an `asyncio` event loop per worker process (e.g. one Uvicorn/FastAPI worker), with:

- RDG execution using `asyncio.TaskGroup` (Python 3.11+) or `asyncio.gather` to run independent branches of the dependency DAG concurrently, each branch awaiting its own `ZenDecision.async_evaluate(...)` plus any pre-fetches.
- A process-wide `asyncio.Semaphore` bounding concurrent in-flight `evaluate` calls, sized from load testing — **[inferred]** verify empirically whether the native extension release the GIL during evaluation for all node types (pure JDM/expression evaluation likely does via Rust; the embedded QuickJS `functionNode` execution path is less certain) before assuming perfect intra-process parallelism. If profiling shows GIL contention under load, scale via multiple worker **processes** (Gunicorn+Uvicorn workers, or plain multiprocessing) behind a load balancer rather than assuming a single process saturates multiple cores.

---

## C. Dependency orchestration (the RDG DAG)

A first-class, versioned metadata artifact (not JDM), e.g.:

```
RDG "loan-underwriting@v7":
  nodes:
    fraud-check:   decision="fraud/fraud-check@v3"
    credit-score:  decision="credit/credit-score@v5", depends_on=[fraud-check]
    pricing:       decision="pricing/final-price@v12", depends_on=[credit-score]
    underwriting:  decision="underwriting/decision@v4", depends_on=[credit-score, pricing]
  edges: field mappings, e.g. underwriting.input.creditScore <- credit-score.output.score
  policy per node: {timeout, fail_mode: closed|open_with_default, retries}
```

- Build the DAG, topologically sort it, execute with `asyncio.TaskGroup`/`gather`: nodes with no unmet dependencies launch as soon as their inputs are ready; independent branches (e.g. `fraud-check` and an unrelated `sanctions-check`) run concurrently.
- Each node's input is assembled from the original request payload plus declared outputs of its dependencies (Section B field-mapping), plus any pre-fetched external data (B.1).
- **Short-circuit on failure**: a `fail_mode: closed` node cancels the whole `TaskGroup`; nodes downstream of a `fail_mode: open_with_default` node instead get the substituted default and are annotated `degraded=true`.
- **Partial results**: the response always includes a per-node status map (`succeeded | failed | skipped-upstream-failure | degraded-default`).
- **Two version axes**: decision version (content-addressed, e.g. SHA-256 of canonicalized JDM JSON) and RDG version (pins a specific set of decision versions) — production traffic always evaluates against one pinned RDG version, giving reproducibility for audit/"why did this fire in January" questions and safe canary rollout (RDG v8 to 5% of traffic while v7 serves the rest).

---

## D. Performance optimizations specific to this (Python) runtime

1. **One `ZenEngine` per process, created at startup**, never per request — [confirmed] evaluation is designed for concurrent workloads; re-creating the engine per call would re-pay any native-extension init cost every time.
2. **Cache `ZenDecision` handles**, not raw JSON: call `engine.get_decision(key)` / `engine.create_decision(bytes)` once per `(key, version_hash)` and retain the handle in an in-process cache (`dict` or LRU) keyed by `f"{key}@{version_hash}"`. Repeated evaluations call `.evaluate()`/`.async_evaluate()` directly, skipping reload/reparse. Invalidate only on an explicit new-version-published event from the Rule Store, not by polling.
3. **Pre-warm hot paths at startup**: eagerly load and evaluate (synthetic input) the top-N decisions by traffic volume so the first real request doesn't pay first-parse cost.
4. **Prefer pre-fetch/hydration over custom nodes wherever the data dependency is statically known** (Section B.1) — this is not just a latency optimization here, it's also the more reliable, better-tested code path given the Python custom-handler's documentation/maturity gap.
5. **`evaluate_batch()` for independent bulk evaluation** [confirmed method exists on `ZenEngine`]: where the same decision must run against many independent input rows (e.g. scoring a batch of records under one decision, or evaluating several sibling/leaf RDG nodes that all invoke the same decision key with different inputs), prefer the native `evaluate_batch` over a Python-level loop of individual `evaluate` calls — it avoids per-call Python↔Rust boundary overhead N times over.
6. **Stay on the native wheel, avoid musl/Alpine base images** — confirm the deployment container uses a glibc-based image (`python:3.12-slim`, not `python:3.12-alpine`) since no musl wheel is published; building from source in a musl environment is unsupported and should not be attempted as a workaround.
7. **Benchmark before trusting assumptions**: measure (a) cold decision load time, (b) warm `evaluate`/`async_evaluate` latency with zero external data needs, (c) the same with N pre-fetched fields merged into context, (d) actual GIL behavior under concurrent load (via `asyncio` task concurrency and, separately, multi-process worker concurrency) to decide the real worker-process count rather than guessing.

---

## E. AI-assisted rule lifecycle

### E.1 Authoring: NL → JDM

1. **Schema-constrained generation**: the LLM never emits free-form JSON; it's constrained to valid node/edge shapes for the node types in Section A.1, explicitly steered by the A.3 boundary — pure logic becomes `expressionNode`/`decisionTableNode`; any need for external data becomes a *named* Orchestrator pre-fetch requirement (a request to engineering to add a fetch key), not inline code, and never a custom node authored by the model itself.
2. **Structural validation via Zen's own Python API** — immediately after generation: `engine.create_decision(content)` as a fast correctness oracle (malformed graphs fail here at negligible cost), plus `zen.validate_expression(expr)` **[confirmed module-level function]** to validate each individual expression-language snippet the model wrote, catching syntax errors at the cell level before a full-graph run.
3. **Mandatory simulation gate**: before any version can be marked `ACTIVE`, run it (with `trace: True`) against (a) a curated historical regression set with known-correct outputs, (b) edge/boundary fuzz cases, and (c) — for a modified existing rule — a side-by-side output diff against the previous version across the same inputs, surfaced as "N of M historical cases changed outcome." Any newly changed output requires explicit human review.
4. **Human-in-the-loop approval, always**: AI-authored/modified versions land in `PENDING_REVIEW` with the diff/simulation report attached; a human rule owner approves before promotion to `ACTIVE`. **No AI-authored rule is ever auto-deployed to production.**

### E.2 Maintenance: versioning, diffing, impact analysis

- **Semantic diff, not text diff**: node-added/removed/modified, decision-table row-level diff, edge rewiring — fed to an LLM as structured input to produce a human-readable change summary (grounds the narrative in a deterministic diff, avoiding invented changes).
- **Impact analysis**: union of (a) the RDG's declared cross-decision edges and (b) a static scan of all `decisionNode` key references across the whole Rule Store corpus. The AI's job is to rank/explain this graph-derived impact list ("touches a table used by 14 downstream decisions across 3 domains"), not to derive the dependency edges itself.
- **Deprecation**: `ACTIVE → DEPRECATED → RETIRED`; impact analysis blocks retirement while any RDG still references the version, surfacing the exact consumer list.

### E.3 Q&A / Explainability

- **Trace capture is ground truth**: every evaluation run with `trace: True` persists its per-node `{node_id, node_name, node_type, input, output, timing}` trace, the RDG's per-node status map, and resolved decision/RDG version identifiers, to a Trace Store keyed by correlation ID.
- **"Why did rule X produce Y?"** is answered by retrieving the exact trace and **deterministically walking the node path** (which `switchNode` branch fired, which table row matched, what each pre-fetch/custom-node call returned); the LLM narrates that already-correct walk in natural language rather than re-deriving the logic itself — avoiding a fluent-but-wrong explanation.
- **"What rules apply to X" / "what changed between v3 and v4"** are RAG queries over an index of JDM content (chunked per node/table row), RDG manifests, semantic diff summaries, and rule metadata — metadata-filtered first, vector-searched second.

---

## F. System / module layout

| Component | Responsibility |
|---|---|
| **Rule Store** | Source of truth for JDM content, RDG manifests, versions, approval state. Postgres, content-addressed, append-only versioning, `pgvector` for RAG embeddings. |
| **Rule Registry / Loader Service** | Implements the `loader` contract Zen expects; serves JDM bytes by key, backed by the Rule Store + cache; publishes invalidation events on new-version activation. |
| **Evaluation Workers** | Stateless Python (FastAPI + Uvicorn) processes, each holding one warm `ZenEngine` + cached `ZenDecision` handles (Section D); expose an internal `evaluate(decision_key, input, opts)` endpoint; horizontally scaled, concurrency bounded per-process (B.6). |
| **Orchestrator** | Resolves an RDG manifest, drives pre-fetch (B.1) and topological execution across Evaluation Workers via `asyncio.TaskGroup`, applies field mappings and failure policy, assembles partial results. Owns the request-facing API. |
| **Data-Fetch Layer** | Per-source `aiodataloader`s, `asyncpg`/`httpx` pools, circuit breakers, shared TTL cache client (Section B). Used directly by the Orchestrator's pre-fetch stage, and by the `customHandler` when that path is used. |
| **Trace Store** | Append-only per-evaluation traces + RDG execution status, keyed by correlation ID and decision/RDG version. Postgres (time-partitioned) or ClickHouse if volume warrants. |
| **AI Authoring Service** | NL→JDM generation, schema/expression validation, simulation-harness invocation, diff/approval workflow (E.1, E.2). |
| **AI Q&A Service** | RAG pipeline over {Rule Store, RDG manifests, diff summaries, Trace Store}; deterministic trace-walker + LLM narrator (E.3). |
| **Vector/Embeddings Store** | `pgvector` on the Rule Store's Postgres cluster by default; escalate to a dedicated vector DB only if corpus size/query volume demands it. |

### Request flow

1. Client → Orchestrator: evaluation request with business payload + decision/RDG version selector.
2. Orchestrator resolves the RDG manifest, builds the DAG, generates a correlation ID.
3. For each DAG node as its dependencies resolve: run declared pre-fetches concurrently (B.1), merge into context, call the Evaluation Worker's `async_evaluate(decision_key@version, context, {"trace": True})`.
4. Any custom-node hit (rare, B.2) resolves the correlation ID from the injected `"__cid"` field and dispatches through the same Data-Fetch Layer.
5. Orchestrator applies field mappings to unblock dependent nodes; independent branches proceed concurrently; assembles final response + per-node RDG status; asynchronously persists the trace bundle.
6. AI Q&A Service later answers questions against persisted traces + Rule Store content, independent of the hot path.

---

## G. Key risks and tradeoffs

1. **Python custom-node handler immaturity** — real (a `customHandler` option exists) but under-documented, with an open serialization bug reported against the exact pattern that would otherwise be copied from Go (issue #269). Mitigation: treat pre-fetch/hydration as the default, gate any custom-node usage behind its own integration test against the pinned `zen-engine` version, and budget an explicit spike before depending on it for a production rule.
2. **GIL behavior under concurrent evaluation is not fully confirmed** for every node type (pure JDM/expression evaluation likely releases the GIL via the Rust core; the embedded QuickJS `functionNode` path is less certain). Mitigation: profile before assuming a single process fully parallelizes across cores; default to multi-process worker scaling (Gunicorn+Uvicorn) as the safe baseline.
3. **No musl/Alpine support** — a real deployment constraint, not a style preference; a musl-based container build will not work with the published wheels.
4. **AI-authored rule correctness** is the largest single safety risk in this system. Mitigations are non-negotiable: schema-constrained generation, `create_decision`/`validate_expression` structural validation, mandatory historical-regression simulation with diffing against the prior version, and a human approval gate before any `ACTIVE` promotion.
5. **Cache staleness for reference data** — mitigate with versioned cache keys, explicit pub/sub invalidation for correctness-sensitive data (rate cards, tax tables), and tagging evaluation traces with the reference-data version actually used so "why did this fire" can distinguish stale-cache causes from rule-logic causes.
6. **Single global `customHandler`/`loader` per engine** — all routing for custom-node dispatch and decision loading funnels through one callable each; a bug here is systemic. Exceptions must be caught and translated inside the handler, never allowed to propagate uncontrolled across the PyO3 boundary; kind-based dispatch tables should be tested independently of any specific rule content.
7. **Recursion/cycles** — `decisionNode` composition has an engine-enforced max-depth guard; cross-decision cycles in the RDG are not caught by Zen itself and need explicit cycle-detection at RDG publish time.

---

## Critical files for implementation

The repository is currently empty; these are the highest-priority files to create first, since everything else depends on getting their contracts right:

- `rules_engine/zen_runtime.py` — process-lifetime `zen.ZenEngine` singleton, `loader` wiring to the Rule Store, `ZenDecision` handle cache keyed by `key@version_hash` (Section D)
- `rules_engine/datafetch/loaders.py` — per-source `aiodataloader` instances, `asyncpg`/`httpx` pool wiring, circuit breakers (Section B.3–B.5)
- `rules_engine/datafetch/custom_handler.py` — the single `customHandler`, correlation-ID lookup, kind-based dispatch, **plus its own integration test fixture** exercising a minimal custom-node JDM graph against the pinned `zen-engine` version before any production dependency is added
- `rules_engine/orchestrator/dag.py` — RDG manifest model, pre-fetch stage, topological execution via `asyncio.TaskGroup`, field-mapping and failure-policy handling (Section C)
- `rules_engine/rulestore/schema.sql` — Rule Store schema: JDM documents, RDG manifests, version/approval state, `pgvector` embeddings table (Section F)
