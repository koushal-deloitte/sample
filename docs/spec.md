# Fallout Agent — Project Spec & Implementation Plan

---

## Commit Plan File — Task Plan

### What to do
Copy `/root/.claude/plans/i-am-starting-a-vectorized-tarjan.md` into the repository at `docs/spec.md`, then commit and push it.

### Why `docs/spec.md`
- Keeps the project root clean (CLAUDE.md already lives there)
- `docs/` is the conventional home for design/spec documents
- The filename `spec.md` is descriptive and searchable

### Steps
1. Create `docs/` directory in `/home/user/sample/`
2. Copy the plan file content into `docs/spec.md`
3. `git add docs/spec.md`
4. Commit with message: `docs: add project spec and implementation plan`
5. Push to `claude/new-project-spec-driven-4b6dze`

---



### What to create
A `CLAUDE.md` at the repo root that serves two audiences:
1. **Claude Code** (this tool) — how to behave when developing in this repo
2. **Human developers** — project orientation, setup, conventions

### Sections

#### 1. Project Overview
One-paragraph description of what Fallout Agent is and the two sub-systems.

#### 2. Architecture Quick-Reference
Module map (src layout, key entry points), data flow diagram in ASCII, key env vars.

#### 3. Dev Setup
```bash
pip install -e ".[dev]"
cp .env.example .env        # fill in real values
alembic upgrade head        # needs PostgreSQL running
uvicorn src.chatbot.api:app --reload   # chatbot UI
python -m src.agent.runner             # fallout runner
```

#### 4. Testing
```bash
pytest tests/ -v                          # unit tests (no external deps)
SKIP_EVALS=0 pytest tests/evals/ -v      # live LLM evals (needs EHAP)
```

#### 5. Karpathy Guidelines (AI development principles)
Adapted from Andrej Karpathy's published principles for working with LLM-based systems:

**For Claude Code (this tool) when working in this repo:**
- **Run, don't just read.** After every change, run `pytest` and verify the output. Reading code is not a substitute for executing it.
- **One thing at a time.** Make the smallest change that demonstrates the idea. Never batch unrelated changes into a single edit.
- **No silent assumptions.** If a requirement is ambiguous (especially around PHI, EHAP API shape, or SQS message format), stop and ask rather than guess.
- **Never skip tests.** A change without a test is a bet. In medical insurance code, it's a bad bet.
- **Migrations are irreversible.** Never auto-generate or auto-apply an Alembic migration without showing the SQL and getting explicit approval.
- **PHI is sacred.** Never log, print, or expose subscriber PII (SSN, DOB, name) in any tool call, output, or test fixture. Use `mask_phi()` from `src/models/fallout.py`.
- **The spec is the source of truth.** If you change behaviour that contradicts the spec file, update the spec first and get agreement before coding.
- **Prefer boring code.** Reach for the existing pattern in the codebase before introducing a new abstraction. Three similar lines beat a premature helper.

**For the Fallout Agent itself (mirrors good LLM-agent design):**
- Start simple and measure before adding complexity.
- Overfit on known cases first (build skills for the most frequent error codes) before generalising.
- Always have an escape hatch — the `escalate` tool and UNKNOWN_FALLOUT skill ensure the agent never silently fails.
- Eval everything: resolution rate, tool call count, escalation rate. Don't trust that a skill works without numbers.

#### 6. Key Files Cheat-Sheet
| File | Purpose |
|---|---|
| `src/config.py` | All env-var config (single source of truth) |
| `src/ehap/client.py` | EHAP HTTP client — update when API schema confirmed |
| `src/agent/orchestrator.py` | Core agent loop — touch carefully |
| `src/agent/guardrails.py` | All guardrail logic lives here |
| `src/skills/validator.py` | Skill Markdown validation + PHI check |
| `src/db/migrations/versions/` | Alembic migrations — never edit existing ones |

#### 7. Adding a New Skill
1. Use the chatbot UI or write a Markdown file with required front-matter (`name`, `error_codes`)
2. Validate: `python -c "from src.skills.validator import parse_and_validate; parse_and_validate(open('myskill.md').read())"`
3. Insert via `SkillRepository.create()` or the chatbot
4. Write a fixture in `tests/evals/test_skill_evals.py`

### File to create
`/home/user/sample/CLAUDE.md` — new file, ~120 lines



## Context
An American medical insurance provider is modernising their enrollment process. When an eligibility check fails for a subscriber, the record lands in a **fallout queue**. This system picks up those records, automatically resolves them using AI, and lets business users manage the resolution playbooks without writing code.

---

## What We Are Building

### Part 1 — Fallout Processing Agent
An always-running Python service that:
1. **Polls AWS SQS** for fallout records (each record contains a `transaction_id`)
2. **Enriches** each record by calling an internal subscriber API with the `transaction_id` → returns canonical subscriber JSON
3. **Routes** the fallout to the correct skill file using `error_code` (primary) + `error_message` (secondary) matching
4. **Runs an LLM agent loop** via EHAP (internal enterprise Claude platform, custom HTTP API):
   - Skill Markdown file becomes the system prompt
   - Subscriber canonical JSON is the user context
   - Agent executes tool calls (REST/SOAP API calls, DB reads/writes, human escalation/ticketing) iteratively until resolved or escalated
5. **Acknowledges** the SQS message on success; lets the message return to queue (up to dead-letter limit) on failure

### Part 2 — Business Chatbot (Skill Manager)
A browser-based chat UI backed by a FastAPI service that allows business users to:
- **Create** new skill files by describing a new fallout scenario in natural language
- **Read / explain** existing skills
- **Edit / update** existing skills
- **Delete / deactivate** skills

The chatbot uses the same EHAP/Claude integration and has access to skill CRUD tools.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        SQS Fallout Queue                         │
└───────────────────────────────┬──────────────────────────────────┘
                                │ poll
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  sqs_consumer.py              Fallout Runner                      │
│  - polls SQS                                                      │
│  - calls subscriber API (transaction_id → canonical JSON)        │
│  - routes error_code → skill                                      │
│  - runs agent loop                                                │
└──────────┬────────────────────────────────────┬──────────────────┘
           │ load skill                          │ tool calls
           ▼                                    ▼
┌─────────────────────┐          ┌──────────────────────────────┐
│  PostgreSQL          │          │  Agent Tools                  │
│  skills table        │          │  - REST/SOAP API caller       │
│  (Markdown content) │          │  - DB read/write              │
└─────────────────────┘          │  - Human escalation/ticketing │
                                 └──────────────────────────────┘
                                           │ EHAP HTTP API
                                           ▼
                                 ┌──────────────────┐
                                 │  EHAP (Claude)    │
                                 │  custom HTTP API  │
                                 └──────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Chatbot (FastAPI + HTML/JS)                                      │
│  Business user chat → EHAP → skill CRUD tools → PostgreSQL       │
└──────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### `skills` table (PostgreSQL)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT | Human-readable name |
| `description` | TEXT | Short description for routing disambiguation |
| `error_codes` | TEXT[] | List of error codes this skill handles |
| `content` | TEXT | Markdown instructions (the skill itself) |
| `is_active` | BOOLEAN | Soft delete / disable |
| `version` | INTEGER | Incremented on each edit |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

### `fallout_runs` table (audit log)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `transaction_id` | TEXT | From SQS |
| `error_code` | TEXT | |
| `error_message` | TEXT | |
| `skill_id` | UUID FK | Skill applied |
| `status` | TEXT | `resolved`, `escalated`, `failed` |
| `resolution_notes` | TEXT | Agent summary |
| `created_at` | TIMESTAMPTZ | |

---

## Directory Structure

```
fallout-agent/
├── src/
│   ├── ehap/
│   │   └── client.py           # EHAP custom HTTP client (stub auth, configurable base URL)
│   ├── agent/
│   │   ├── runner.py           # Main SQS polling loop
│   │   ├── orchestrator.py     # enrich → route → agent-loop → log
│   │   └── tools/
│   │       ├── api_caller.py   # Generic REST/SOAP tool
│   │       ├── db_tool.py      # DB read/write tool
│   │       └── escalation.py   # Create ticket / escalate tool
│   ├── skills/
│   │   ├── repository.py       # PostgreSQL CRUD for skill files
│   │   └── router.py           # error_code → skill lookup (exact match + fuzzy fallback)
│   ├── chatbot/
│   │   ├── api.py              # FastAPI app with /chat endpoint
│   │   ├── handler.py          # Chatbot agent loop (skill CRUD tools)
│   │   └── static/
│   │       └── index.html      # Simple browser chat UI
│   ├── models/
│   │   ├── fallout.py          # Pydantic: FalloutRecord, SubscriberData
│   │   └── skill.py            # Pydantic: Skill, SkillCreate, SkillUpdate
│   └── db/
│       ├── connection.py       # SQLAlchemy async engine setup
│       └── migrations/         # Alembic migrations
├── tests/
│   ├── test_router.py
│   ├── test_orchestrator.py
│   └── test_chatbot.py
├── pyproject.toml              # Dependencies (fastapi, sqlalchemy, boto3, httpx, pydantic, alembic)
├── .env.example
└── README.md
```

---

## Key Implementation Details

### EHAP Client (`src/ehap/client.py`)
- Thin `httpx`-based async HTTP client
- `base_url`, `api_key` (or other auth) loaded from env vars
- Exposes: `async def chat(messages, tools, system) -> EHAPResponse`
- `EHAPResponse` normalises the response into: `content: str`, `tool_calls: list[ToolCall]`
- Stub the request/response shape for now; real schema to be confirmed with EHAP team

### Agent Loop (`src/agent/orchestrator.py`)
```
1. Call subscriber API → canonical_json
2. route(error_code, error_message) → skill
3. Build messages: [system=skill.content, user=canonical_json + "Resolve this fallout"]
4. Loop:
   a. ehap.chat(messages, tools=AVAILABLE_TOOLS)
   b. If response.tool_calls: execute each, append results to messages, continue
   c. If no tool_calls: extract resolution text, break
5. Log result to fallout_runs table
6. Ack SQS message
```

### Skill Router (`src/skills/router.py`)
- Primary: exact match on `error_codes` array in `skills` table (`error_code = ANY(error_codes)`)
- Fallback: if no match, use a generic "UNKNOWN_FALLOUT" skill that instructs the agent to escalate to human
- Future: LLM-based semantic matching (out of scope for v1)

### Chatbot Agent (`src/chatbot/handler.py`)
- System prompt: instructs Claude it is a skill management assistant
- Available tools: `list_skills`, `get_skill`, `create_skill`, `update_skill`, `deactivate_skill`
- Chatbot confirms with user before destructive operations (delete/overwrite)
- Agent loop identical pattern to the fallout agent loop

### Web UI (`src/chatbot/static/index.html`)
- Single HTML file, vanilla JS, basic chat bubble UI
- Calls `POST /chat` with `{session_id, message}`
- FastAPI maintains conversation history in-memory per session (dict keyed by session_id)

---

## Technology Choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | User requirement |
| HTTP client | `httpx` (async) | Modern, clean async API |
| Web framework | FastAPI | Async, Pydantic-native |
| ORM | SQLAlchemy 2 (async) | Industry standard, works with Alembic |
| Queue | `boto3` SQS | User requirement |
| DB | PostgreSQL | Best for text-heavy skill content, full-text search |
| Agent framework | Custom lightweight loop | EHAP is non-standard; no heavy framework needed |
| Config | `pydantic-settings` + `.env` | Type-safe config |
| Tests | `pytest` + `pytest-asyncio` | Standard |

---

## Implementation Sequence

1. **Project scaffold** — `pyproject.toml`, folder structure, `.env.example`
2. **DB layer** — SQLAlchemy models, Alembic migrations, `SkillRepository`
3. **EHAP client** — stubbed HTTP client with configurable base URL
4. **Agent tools** — `api_caller`, `db_tool`, `escalation` (all accept config from skill instructions)
5. **Skill router** — exact match lookup
6. **Fallout orchestrator** — enrich → route → agent loop → log
7. **SQS consumer** — polling loop, ack/nack logic
8. **Chatbot handler** — skill CRUD agent loop
9. **FastAPI chatbot API** — `/chat` endpoint + session management
10. **Web UI** — `index.html` chat interface
11. **Tests** — unit tests for router, orchestrator, chatbot

---

## Guardrails

### Agent Execution Guardrails
| Guardrail | Mechanism | Where |
|---|---|---|
| Max tool calls per run | Hard limit (e.g. 20 calls); escalate if exceeded | `orchestrator.py` |
| Idempotency | Check `transaction_id` in `fallout_runs` before processing; skip duplicates | `orchestrator.py` |
| Tool allowlist per skill | Skill Markdown declares `allowed_tools:` front-matter; orchestrator enforces it | `orchestrator.py` + skill schema |
| PHI/PII masking in logs | Strip or hash subscriber fields (SSN, DOB, name) before structured logging | `models/fallout.py` + logging middleware |
| Dry-run mode | `DRY_RUN=true` env var → log all tool calls but don't execute them | all tools |
| Confidence gate | If agent's final message contains uncertainty markers, auto-escalate instead of resolving | `orchestrator.py` post-loop check |
| Dangerous action confirmation | Tools that write/update records require a `confirm=true` parameter set by a prior reasoning step | `db_tool.py`, `api_caller.py` |

### Chatbot Guardrails
- Confirm before destructive operations: chatbot must echo back the full skill diff and ask "Confirm?" before saving edits or deleting
- Skill validation on save: parse the Markdown front-matter (error codes, name, allowed_tools) and reject if required fields are missing
- Rate limiting: max N chatbot sessions / min per IP (simple in-memory token bucket)

---

## Evals

### Automated Evals (run in CI + nightly)
| Eval | What it measures | How |
|---|---|---|
| **Skill resolution rate** | % of synthetic fallout records correctly resolved per skill | `tests/evals/test_skill_evals.py` — fixture records with expected outcomes |
| **Router accuracy** | Correct skill matched for given error_code/message | Parameterised pytest cases |
| **Agent loop efficiency** | Avg tool calls to resolution; flag regressions | Logged in `fallout_runs.tool_call_count` |
| **Chatbot intent accuracy** | Correct CRUD intent extracted from natural language prompts | Golden test set of 20+ prompts with expected tool calls |
| **LLM-as-judge** | Claude evaluates quality of agent reasoning trace (score 1-5) | `tests/evals/judge.py` — post-run scorer using EHAP |

### Additional DB columns for evals
Add to `fallout_runs`:
- `tool_call_count` INTEGER
- `llm_calls` INTEGER
- `duration_ms` INTEGER
- `human_feedback` TEXT (set when a human corrects an escalation)

### Eval Dashboard
A FastAPI `/evals` page (read-only) showing:
- Resolution rate per skill (last 7 days)
- Average tool calls per run
- Escalation rate trend
- Skills with declining resolution rate (flagged for review)

---

## Self-Learning / Continuous Improvement

### Feedback Loop
1. When a human resolves an escalated fallout, the resolution UI (ticketing system) posts back to `POST /feedback` with `{transaction_id, resolution_steps, outcome}`
2. The system stores this in a `fallout_feedback` table
3. **Auto-suggestion**: if the same error_code accumulates ≥ 5 unmatched fallouts in 24h, a daily job creates a draft skill (via chatbot) pre-populated with the common resolution steps from feedback, and alerts the business team

### Skill Version History
- Every save of a skill creates a row in `skill_versions` table (snapshot of `content` + `version` + `updated_by`)
- Chatbot supports: "Roll back MISSING_NPI skill to version 3"
- Allows comparison of resolution rates before/after a skill edit

### Skill Effectiveness Monitoring
- Nightly job: for each active skill, compute `resolution_rate = resolved / (resolved + escalated)` over the last 30 days
- If `resolution_rate < 0.5` → mark skill as `needs_review`, alert business team via email/notification

### New Fallout Type Detection
- If `error_code` has no matching skill and appears > threshold times → create a `draft_skill` entry and notify the business team to fill it in via the chatbot

---

## Observability

- **Structured logging**: every agent run emits JSON logs with `transaction_id`, `skill_id`, `tool_calls`, `status`, `duration_ms`
- **Trace IDs**: a `run_id` (UUID) flows through every log line in a single fallout processing run
- **Metrics** (emit to CloudWatch or Datadog via env-configured sink):
  - `fallout.queue_depth` — SQS approximate message count
  - `fallout.resolution_rate` — rolling 5-min window
  - `fallout.agent_loop_iterations` — histogram
  - `fallout.escalation_rate` — rolling 5-min window
- **Alerting thresholds** (configured externally, documented in README):
  - DLQ depth > 10 → page on-call
  - Resolution rate < 60% over 1h → alert business team
- **Health endpoint**: `GET /health` returns queue connectivity, DB connectivity, EHAP reachability

---

## Compliance & Security (HIPAA-relevant)

| Concern | Approach |
|---|---|
| PHI in logs | Structured log fields for subscriber data are masked (e.g. `ssn → ***`, `dob → ***`) |
| Audit trail | `fallout_runs` and `skill_versions` are append-only (no deletes) |
| Skill content safety | Chatbot rejects skill instructions that attempt to log or expose PHI |
| Secrets management | All credentials via env vars / AWS Secrets Manager; never in code or DB |
| DB encryption | PostgreSQL TLS + encryption at rest (infrastructure concern, documented) |

---

## Updated Directory Structure

```
fallout-agent/
├── src/
│   ├── ehap/
│   │   └── client.py
│   ├── agent/
│   │   ├── runner.py
│   │   ├── orchestrator.py        # includes guardrail checks
│   │   ├── guardrails.py          # max_tool_calls, confidence_gate, dry_run
│   │   └── tools/
│   │       ├── api_caller.py
│   │       ├── db_tool.py
│   │       └── escalation.py
│   ├── skills/
│   │   ├── repository.py          # includes version history writes
│   │   ├── router.py
│   │   └── validator.py           # front-matter parser + field validation
│   ├── chatbot/
│   │   ├── api.py
│   │   ├── handler.py
│   │   └── static/index.html
│   ├── monitoring/
│   │   ├── metrics.py             # emit CloudWatch/Datadog metrics
│   │   ├── eval_runner.py         # nightly skill effectiveness job
│   │   └── feedback_handler.py    # POST /feedback endpoint
│   ├── models/
│   │   ├── fallout.py
│   │   └── skill.py
│   └── db/
│       ├── connection.py
│       └── migrations/
├── tests/
│   ├── test_router.py
│   ├── test_orchestrator.py
│   ├── test_chatbot.py
│   ├── test_guardrails.py
│   └── evals/
│       ├── test_skill_evals.py    # resolution rate evals
│       └── judge.py               # LLM-as-judge scorer
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Updated Data Model

### Additional tables

**`skill_versions`** — immutable history of every skill edit
| Column | Type |
|---|---|
| `id` | UUID PK |
| `skill_id` | UUID FK |
| `content` | TEXT |
| `version` | INTEGER |
| `updated_by` | TEXT |
| `created_at` | TIMESTAMPTZ |

**`fallout_feedback`** — human correction feedback
| Column | Type |
|---|---|
| `id` | UUID PK |
| `transaction_id` | TEXT |
| `run_id` | UUID FK → fallout_runs |
| `resolution_steps` | TEXT |
| `outcome` | TEXT |
| `created_at` | TIMESTAMPTZ |

**`draft_skills`** — auto-suggested skills pending business review
| Column | Type |
|---|---|
| `id` | UUID PK |
| `error_code` | TEXT |
| `suggested_content` | TEXT |
| `trigger_count` | INTEGER |
| `status` | TEXT (`pending`, `promoted`, `rejected`) |
| `created_at` | TIMESTAMPTZ |

---

## Updated Implementation Sequence

1. **Project scaffold** — `pyproject.toml`, folder structure, `.env.example`
2. **DB layer** — SQLAlchemy models (all tables), Alembic migrations, `SkillRepository`
3. **EHAP client** — stubbed HTTP client with configurable base URL
4. **Agent tools** — `api_caller`, `db_tool`, `escalation`
5. **Guardrails** — `guardrails.py` (tool limit, idempotency, dry-run, PHI masking)
6. **Skill router + validator** — exact match lookup, front-matter validation
7. **Fallout orchestrator** — enrich → route → guardrail checks → agent loop → log
8. **SQS consumer** — polling loop, ack/nack logic
9. **Self-learning jobs** — `eval_runner.py`, `feedback_handler.py`, draft skill suggestion
10. **Chatbot handler** — skill CRUD agent loop with confirmation guardrail
11. **FastAPI API** — `/chat`, `/feedback`, `/health`, `/evals` endpoints
12. **Web UI** — `index.html` chat interface + eval dashboard page
13. **Tests + evals** — unit tests, golden eval set, LLM-as-judge

---

## Verification Plan

- `pytest tests/` — unit tests with mocked EHAP, SQS, and DB
- `pytest tests/evals/` — run skill resolution eval suite against golden fixtures
- Start the FastAPI server (`uvicorn src.chatbot.api:app`) and open the browser chat UI
- Test chatbot: "Create a skill for error code MISSING_NPI that calls the NPI lookup API"
- Test guardrails: inject a synthetic run exceeding max tool calls → verify escalation fires
- Test orchestrator: inject a synthetic `FalloutRecord` and run `orchestrator.process(record)` → verify `fallout_runs` row with correct `tool_call_count`
- Test idempotency: replay the same `transaction_id` → verify second run is skipped
- Test dry-run: set `DRY_RUN=true` → verify tools log but don't execute
- Test feedback loop: POST `/feedback` with a corrected resolution → verify `draft_skill` triggers after threshold
