# Fallout Agent — CLAUDE.md

## Project Overview

Fallout Agent is an AI-powered medical insurance eligibility fallout resolution system built for an American insurance provider modernising their enrollment process. When an eligibility check fails for a subscriber, the record lands in an AWS SQS fallout queue. The system has two parts:

1. **Fallout Processing Agent** — a continuously running Python service that polls SQS, enriches each record via an internal subscriber API, routes it to the matching skill file (by error code), and runs a Claude-powered agent loop (via the internal EHAP platform) to resolve the fallout autonomously using tool calls (REST APIs, DB queries, escalation).

2. **Business Chatbot** — a browser-based chat UI (FastAPI + vanilla JS) that lets non-technical business users create, view, edit, and deactivate skill files in natural language. Skill files are Markdown documents stored in PostgreSQL that act as the system prompt for the agent — one skill per fallout type.

---

## Architecture Quick-Reference

```
SQS Fallout Queue
       │ poll
       ▼
 runner.py  ──► orchestrator.py
                │  1. fetch subscriber (HTTP)
                │  2. route error_code → skill (PostgreSQL)
                │  3. agent loop via EHAP
                │     ├─ call_api tool
                │     ├─ query_db tool
                │     └─ escalate tool
                └─ log → fallout_runs table

FastAPI chatbot  ──► handler.py  ──► EHAP  ──► skill CRUD tools  ──► PostgreSQL
```

### Key Entry Points

| Command | What it starts |
|---|---|
| `python -m src.agent.runner` | SQS polling loop (fallout processor) |
| `uvicorn src.chatbot.api:app --reload` | Business chatbot web UI + API |
| `python -m src.monitoring.eval_runner` | Nightly skill effectiveness job |

### Key Env Vars (see `.env.example` for full list)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async URL |
| `SQS_QUEUE_URL` | AWS SQS fallout queue |
| `EHAP_BASE_URL` | Internal Claude platform base URL |
| `EHAP_API_KEY` | EHAP auth token |
| `SUBSCRIBER_API_BASE_URL` | Subscriber enrichment API |
| `DRY_RUN` | `true` = log tool calls but don't execute |
| `MAX_TOOL_CALLS_PER_RUN` | Hard cap on agent tool calls (default 20) |

---

## Dev Setup

```bash
# 1. Install dependencies
pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env
# Edit .env with real values for DATABASE_URL, EHAP_BASE_URL, SQS_QUEUE_URL, etc.

# 3. Run database migrations (requires PostgreSQL)
alembic upgrade head

# 4. Start the chatbot UI
uvicorn src.chatbot.api:app --reload
# Open http://localhost:8000

# 5. Start the fallout agent (separate terminal)
python -m src.agent.runner
```

---

## Testing

```bash
# Unit tests — no external dependencies, always run these
pytest tests/ -v

# Live LLM evals — requires real EHAP connection
SKIP_EVALS=0 pytest tests/evals/ -v

# Single test file
pytest tests/test_guardrails.py -v
```

**After every code change, run `pytest tests/ -v` before considering the change done.**

---

## Key Files Cheat-Sheet

| File | Purpose | Touch with care? |
|---|---|---|
| `src/config.py` | All env-var config — single source of truth | No |
| `src/ehap/client.py` | EHAP HTTP client — stub until API schema confirmed | Yes — update `_parse()` when EHAP schema is confirmed |
| `src/agent/orchestrator.py` | Core agent loop: enrich → route → LLM → log | Yes — central to correctness |
| `src/agent/guardrails.py` | All guardrail logic (tool limit, confidence gate, PHI mask) | Yes — medical compliance |
| `src/skills/validator.py` | Skill Markdown validation + PHI-leak detection | Yes — security boundary |
| `src/skills/router.py` | Error-code → skill routing + UNKNOWN_FALLOUT fallback | No |
| `src/skills/repository.py` | PostgreSQL CRUD for skills + version history | No |
| `src/db/orm_models.py` | SQLAlchemy ORM models (all 5 tables) | Yes — schema changes need a new migration |
| `src/db/migrations/versions/` | Alembic migration files | Never edit existing migrations |
| `src/chatbot/handler.py` | Chatbot agent loop with skill CRUD tools | No |

---

## Adding a New Skill

1. Write a Markdown file with required YAML front-matter:
   ```markdown
   ---
   name: MY_ERROR_CODE Handler
   description: Handles MY_ERROR_CODE fallouts
   error_codes:
     - MY_ERROR_CODE
   allowed_tools:
     - call_api
     - escalate
   ---

   ## Objective
   ...
   ```
2. Validate it before saving:
   ```bash
   python -c "from src.skills.validator import parse_and_validate; parse_and_validate(open('myskill.md').read()); print('OK')"
   ```
3. Insert via `SkillRepository.create()` or the chatbot UI.
4. Add a golden fixture to `tests/evals/test_skill_evals.py`.

---

## Karpathy Guidelines

Adapted from Andrej Karpathy's principles for building and maintaining LLM-based systems. These apply to **both** Claude Code (this tool) when developing in this repo and to how the Fallout Agent itself is designed.

### For Claude Code (AI coding assistant rules for this repo)

**Run, don't just read.**
After every change, run `pytest tests/ -v` and verify the output. Reading code is not a substitute for executing it. A passing test suite is the only honest signal.

**One thing at a time.**
Make the smallest change that demonstrates the idea. Never batch unrelated changes into a single commit or edit. If a change is hard to describe in one sentence, it's probably two changes.

**No silent assumptions.**
If a requirement is ambiguous — especially around PHI handling, EHAP API shape, SQS message format, or database schema — stop and ask rather than guess. In medical systems, wrong assumptions become compliance incidents.

**Never skip tests.**
A change without a test is a bet. In medical insurance code, it's a bad bet. If you add a new code path, add a test for it.

**Migrations are irreversible.**
Never auto-generate or auto-apply an Alembic migration without explicitly showing the SQL and getting approval. Dropped columns and renamed tables cannot be undone in production without data loss.

**PHI is sacred.**
Never log, print, or expose subscriber PII (SSN, DOB, name) in any tool call output, log line, or test fixture. Use `mask_phi()` from `src/models/fallout.py:mask_phi`. When in doubt, mask it.

**The spec is the source of truth.**
If a code change contradicts the spec in `CLAUDE.md` or the project spec file, update the spec and get agreement before writing code. Code that disagrees with the spec is a bug, not a feature.

**Prefer boring code.**
Reach for the existing pattern in the codebase before introducing a new abstraction. `SkillRepository` already handles all DB access — don't bypass it. Three similar lines beat a premature helper function.

**Don't touch what you don't need to.**
`orchestrator.py` and `guardrails.py` are the most critical files in the system. If the task doesn't require changing them, don't change them.

### For the Fallout Agent System (LLM agent design principles)

**Start simple and measure.**
Build skills for the highest-volume error codes first. Get them working and measured (resolution rate, escalation rate) before building 100 skills. Don't optimise what you haven't measured.

**Overfit before you generalise.**
A skill that perfectly handles the 5 most common MISSING_NPI patterns is more valuable than a skill that handles 50 patterns poorly. Nail the known cases first.

**Always have an escape hatch.**
Every agent run has two guaranteed exits: the `escalate` tool and the `UNKNOWN_FALLOUT` fallback skill. The agent must never silently fail or loop forever. The `MAX_TOOL_CALLS_PER_RUN` guardrail is the last line of defence.

**Eval everything, trust nothing.**
Resolution rate, escalation rate, tool call count, and LLM-as-judge scores are the ground truth. A skill that "looks right" but has a 40% resolution rate is broken. Run `tests/evals/` regularly and watch the dashboard at `/evals`.

**The agent is not infallible.**
Every autonomous action the agent takes (API calls, DB writes) has a confirmation guardrail (`confirm=true`). Every skill declares its `allowed_tools`. The system is designed to fail safe — escalation is always better than an incorrect resolution.

**Human feedback closes the loop.**
When a human corrects an escalated fallout, that resolution should flow back via `POST /feedback`. That feedback is the training signal for improving skills. Capture it; don't discard it.

---

## Compliance Notes (HIPAA)

- Subscriber PII is masked in all logs via `mask_phi()` in `src/models/fallout.py`
- `fallout_runs` and `skill_versions` are append-only — no row is ever deleted
- Skill content is validated for PHI-exposing patterns before save (`src/skills/validator.py`)
- All secrets (DB password, EHAP key, AWS credentials) via env vars only — never hardcoded
- PostgreSQL TLS and encryption at rest are infrastructure concerns — document in your deployment runbook
