# EduSense

AI-powered self-study support system for university students: manage learning
activities, upload and organize study documents, ask questions grounded in
those documents, practice with generated question banks, and receive
AI-assisted feedback — with subscriptions and administration on top.

A personal learning-support product (not a university administration or
attendance system).

---

## Implementation status

**48 / 51 backlog tasks complete · 335 tests passing · ruff + mypy clean ·
architecture invariants enforced**

| Milestone | Status | Delivered |
|---|---|---|
| M0 — Backend Foundation & Infrastructure | ✅ 6/6 | Docker Compose (PostgreSQL 16 + pgvector, Redis 7, MinIO), async FastAPI app, Alembic baseline (38 tables), health/readiness probes, pytest sandbox, GitHub Actions CI |
| M0.5 — Workflow & Verification Pipeline | ✅ 4/4 | Repository-native workflow CLI (see [Workflow engine](#workflow-engine)) |
| M1 — Auth, RBAC & Profiles | ✅ 7/7 | Registration (Argon2id + single-use email verification), JWT login/refresh/logout, password reset (revokes all sessions), profile CRUD, login attempt limiting (NFR04) + security audit log, Google social login |
| M2 — Academic Study Core | ✅ 5/5 | Courses CRUD, assignments (status transitions), exam tracking, weekly recurring schedules with overlap warnings, aggregated calendar |
| M3 — Documents & Ingestion | ✅ 6/6 | MinIO client (private bucket, presigned URLs), multipart upload with magic-byte validation, Celery + PostgreSQL IdempotencyGuard, PDF/DOCX extraction + chunking, pgvector embedding ingestion, document search/filter/tags |
| M4 — Core AI & RAG | ✅ 8/8 | Central Gemini adapter (circuit breaker + retry), atomic Redis quota reservation (fail-closed), usage/token logging, AI chat with history, permission-scoped vector retrieval (ownership predicate inside the SQL), prompt-injection sanitization, GroundingChecker with citations, golden-dataset RAG evaluation |
| M5 — Advanced AI & Automation | ✅ 9/9 | Summarization (background jobs), MCQ + essay question-bank generation, essay grading with rubric, practice attempts & scoring, AI study plans, plan item lifecycle, progress dashboard, Celery Beat deadline/exam email reminders, notifications (read-side) |
| M6 — Subscriptions & Admin | ⛔ 3/6 | Admin RBAC gate, account management (suspend/activate + session revocation), AI usage analytics & quota policy config, system storage/resource reports. **Blocked:** SePay payment, subscription lifecycle, expiry worker — awaiting Product Owner decision ([DECISION-PO-02](workflow/decisions.json)) |

### Verification status (be aware)

- ✅ **Verified**: full unit/integration-sandbox test suite (335 tests; external
  services mocked per policy), lint (ruff), types (mypy), architecture
  boundary checks, full `workflow validate` pipeline.
- ⚠️ **Not yet run end-to-end on this machine**: Docker services were never
  started here, so live verification of Alembic against real PostgreSQL, MinIO
  round-trips, Gemini calls, SMTP delivery, and queue transport is still
  pending. Follow [Running the full stack](#running-the-full-stack) to close
  that gap.
- 🔒 **Pending Product Owner decisions**: ~15 proposed defaults are implemented
  and flagged in each task report (`workflow/reports/TASK-*.md`) — password
  policy, token TTLs, reminder windows/thresholds, chunk sizes, quota caps.
  See [Open decisions](#open-decisions).

---

## Tech stack

| Layer | Technology |
|---|---|
| API | Python 3.12, FastAPI (async), Pydantic v2 Settings |
| Database | PostgreSQL 16 + pgvector (38-table baseline via Alembic) |
| Cache / quota gate | Redis 7 (namespaced keys with TTL, fail-closed) |
| Object storage | MinIO (private bucket, presigned URLs; DB stores keys only) |
| Async jobs | Celery + Redis (worker + Beat), PostgreSQL-backed IdempotencyGuard |
| AI | Google Gemini (generation + embeddings) through a single shared adapter — model names from config, never hard-coded |
| Email | SMTP (stdlib, worker thread; unconfigured host gracefully skips email legs) |
| Auth | JWT (HS256, 15 min access / 7 d refresh) + Argon2id + RBAC (Student / Admin) |
| Testing | pytest + pytest-asyncio, httpx ASGITransport, all externals mocked |
| Quality gates | ruff, mypy, AST-based architecture invariant checks, GitHub Actions CI |

---

## Architecture

Service-based architecture with a shared PostgreSQL database:

```
Vue Web Client ──► API Backend (FastAPI) ──► AI Service (internal, service-auth)
                        │                        │
                        ▼                        ▼
                 PostgreSQL + pgvector ◄── shared providers adapter (only Gemini SDK import point)
                        ▲                        │
        MinIO ◄── Clients ┘            Celery Worker / Beat (Redis queues)
```

Hard invariants (enforced by `python workflow/workflow.py validate
--check-architecture`):

1. Routers → Services → Repositories. Routers hold no business logic and
   never import repositories; only repositories own SQL.
2. Only `app/providers` may import the Gemini SDK. Timeouts, retries, circuit
   breaker, and usage logging live there.
3. Every protected endpoint declares its auth dependency; ownership is
   enforced server-side per record (clients never supply user ids).
4. Long AI work runs in Celery — never inside a request. The API returns a
   `job_id` the client polls.
5. Retrieval SQL carries the ownership predicate **inside** the query
   (never unscoped-then-filtered). Every generated answer passes the
   GroundingChecker; insufficient evidence returns a standard refusal.
6. Quota is reserved in Redis before any provider call, refunded on failure;
   Redis unavailable ⇒ reject (fail closed).
7. Worker tasks pass the IdempotencyGuard; a task is `done` only after
   success. Duplicate reminders are suppressed by deduplication keys.
8. The MinIO bucket is private; serving goes through ownership-checked
   presigned URLs.

### Project structure

```
backend/
├── app/
│   ├── api/            # thin routers (v1) + health
│   ├── clients/        # MinIO storage, Google OAuth
│   ├── core/           # config, database, security, errors, lifespan
│   ├── providers/      # Gemini adapter, circuit breaker, usage logger (only SDK import point)
│   ├── repositories/   # ALL SQL lives here
│   ├── services/       # business logic (auth, chat, quota, grading, ...)
│   └── worker/         # celery app, scheduler (Beat), idempotency, tasks/
├── migrations/         # Alembic (async) — 0001 baseline: 38 tables + pgvector
└── tests/              # 335 unit/integration-sandbox tests (externals mocked)
workflow/               # backlog engine: CLI, dispatch/review pipeline, reports
docs/                   # architecture, source-of-truth, design references
.agents/                # agent rules, skills, definitions
```

---

## Getting started

### Prerequisites

- Docker Desktop (running)
- Python 3.12 with a virtual environment for host-side commands
  (migrations, Celery, tests)
- A Gemini API key for AI features (aistudio.google.com — optional; without
  it everything non-AI still works and AI endpoints fail closed with 503)

### Quick start

```powershell
# 1. Configure
Copy-Item backend\.env.example backend\.env   # set GEMINI_API_KEY, SECRET_KEY

# 2. Infrastructure + API container
docker compose up -d --build
curl.exe http://localhost:8000/health/ready   # expect status: ready

# 3. Migrate (38 tables + pgvector)
cd backend
.venv\Scripts\python.exe -m alembic upgrade head

# 4. Celery worker + Beat (two terminals; --pool=solo is required on Windows)
.venv\Scripts\celery.exe -A app.worker.celery_app.celery_app worker -Q edusense_default,edusense_documents,edusense_ai,edusense_emails -l info --pool=solo
.venv\Scripts\celery.exe -A app.worker.celery_app.celery_app beat -l info --pool=solo
```

Then open **http://localhost:8000/docs** — the interactive OpenAPI UI covers
every endpoint (use the *Authorize* button with a Bearer token).

Register the first user via `POST /api/v1/auth/register`, then promote it to
admin (there is no admin signup path by design):

```powershell
docker exec edusense-postgres psql -U edusense_user -d edusense_db -c "INSERT INTO user_roles (user_id, role_id) SELECT u.id, r.id FROM users u, roles r WHERE u.email = 'you@example.com' AND r.name = 'admin' ON CONFLICT DO NOTHING;"
```

<details>
<summary>Alternative: run the API from the venv (no backend container)</summary>

```powershell
docker compose up -d postgres redis minio minio-init
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

`DATABASE_URL` defaults to `localhost:5432` and matches the compose ports.
</details>

---

## API surface (v1)

| Group | Endpoints |
|---|---|
| Auth | register, login, refresh, logout, password reset (request/confirm), Google social login |
| Users | `GET /users/me`, profile update, JWT-protected |
| Courses | CRUD with ownership isolation, natural-key uniqueness, delete impact report |
| Assignments / Exams / Schedules | nested under owned courses; overlap warnings; calendar aggregation (`GET /calendar`) |
| Documents | multipart upload (magic-byte validated), listing/search/tag management, presigned downloads |
| AI jobs | summarize, question-bank generation, study-plan generation — `202 + job_id` then poll `GET /ai/jobs/{id}` |
| Chat | conversations, history, grounded RAG answers |
| Practice | attempts, MCQ auto-grading + essay rubric grading, score tracking |
| Dashboard | overview counts, per-course progress with risk status |
| Notifications | list/unread count/mark-read (read-side only — writers are Worker/Progress/API) |
| Admin | account management (suspend revokes sessions), AI usage analytics, quota policies, system reports |

---

## Testing

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q          # 335 tests
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

External services (PostgreSQL, Redis, MinIO, Gemini, SMTP) are **always
mocked** in tests — no real services are contacted, no real emails are sent.
Test authorization, not just happy paths: cross-user access attempts, RBAC
denials, suspended accounts, and idempotency/retry semantics all have
dedicated cases.

---

## Workflow engine

The repository carries its own evidence-driven automation (`workflow/` — see
[`workflow/README.md`](workflow/README.md)):

```powershell
python workflow/workflow.py status        # milestone progress + next eligible task
python workflow/workflow.py validate      # architecture + ruff + mypy + pytest gates
python workflow/workflow.py audit         # live Docker/DB/MinIO/Redis/API probes
python workflow/workflow.py orchestrate   # dispatch/ingest tasks to ZCode subagents
```

Tasks flow through a deterministic FSM (READY → RUNNING → REVIEW → DONE):
`orchestrate` emits a bounded dispatch brief for a specialist subagent, the
subagent writes a handoff report, the next `orchestrate` **re-runs every
verification command itself** before the task may enter REVIEW, and the
Product Owner approves via `workflow.py review`. Every task has a handoff
report in `workflow/reports/` and a QA brief in `workflow/dispatches/`.

---

## Open decisions & known limitations

**Blocked (Product Owner decision required — `DECISION-PO-02`):**
M6-01 SePay payment integration, M6-02 subscription lifecycle, M6-03 expiry
worker. The proposed default is mock payment mode in development; live
payments need approved Pro pricing.

**Implemented defaults awaiting confirmation** (all flagged in task reports):
password policy (≥8 chars, letter+digit), verification-token TTL (24h) and
reset-token TTL (15 min), no refresh-token rotation, login rate limits
(5/15/15), document upload caps (20 MB / 10 files), list defaults to
active-only, calendar UTC dates with a 56-day cap, chunk sizing (450/75
tokens), reminder windows (24 h assignments / 72 h exams) and scan cadence,
risk-status thresholds (50 %), question-count cap (20), chat length cap
(8 000 chars).

**Dependency additions to be ratified** (pipeline stack completion):
`pypdf`, `python-docx`, `celery[redis]` — pure-Python libraries required by
the declared ingestion/queue architecture.

**Integration verification** remains open until the Docker stack is run on
this machine (see [Verification status](#verification-status-be-aware)).

---

## Documentation

- [`AGENTS.md`](AGENTS.md) — project rules and invariants (source of truth for agents)
- [`docs/project-documents/`](docs/project-documents/) — architecture design, user stories, source-of-truth summary, SQL schema
- [`workflow/README.md`](workflow/README.md) — workflow engine and subagent pipeline
- [`workflow/reports/`](workflow/reports/) — per-task completion reports with real verification evidence
- [`docs/DESIGN.md`](docs/DESIGN.md) — frontend design tokens

---

## Security notes for development

Default credentials (`minioadmin`, `edusense_dev_password`, dev `SECRET_KEY`)
are for local development only. Change `SECRET_KEY` and all service
credentials before any real deployment. Never commit `.env`. JWTs expire;
refresh sessions are revoked on logout, password reset, and suspension.
Passwords are hashed with Argon2id; only SHA-256 hashes of verification and
refresh tokens are ever stored.
