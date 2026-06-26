# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clawith is an open-source multi-agent collaboration platform — a "digital employee" system where AI agents have persistent identity (`soul.md`), long-term memory (`memory.md`), autonomous awareness (cron/interval/webhook triggers), and can communicate with each other (A2A) and with humans via omni-channel integrations (Feishu, DingTalk, WeCom, Slack, Discord).

## Rules & Documentation Map

Canonical project rules and reference docs:

- `AGENTS.md` — project-level entry point for agent workflow
- `CONTRIBUTING.md` — dev workflow, PR conventions, Windows setup notes
- `README.md` — quick start, prerequisites, deployment options
- `backend/ALEMBIC_GUIDELINES.md` — required reading before writing DB migrations
- `RELEASE_NOTES.md` — version-by-version changelog (used by `/release` work)
- `helm/QUICKSTART.md` (and `QUICKSTART_EN.md`) — Kubernetes deployment

> **Note:** Earlier revisions of this file referenced `.agents/rules/` and `ARCHITECTURE_SPEC_EN.md`. Those files no longer exist in the repo; do not look for them.

## Commands

### Full Stack (recommended)

```bash
# One-command setup (creates .env, PostgreSQL, installs deps)
bash setup.sh            # production runtime only
bash setup.sh --dev      # also installs pytest + ruff

# Start all services → Frontend http://localhost:3008, Backend http://localhost:8008
bash restart.sh

# Stop
bash restart.sh stop
```

### Backend (Python / FastAPI)

```bash
cd backend

# Activate venv created by setup.sh (Linux/macOS)
source .venv/bin/activate
# Windows: .venv\Scripts\activate

# Run dev server (single process — `api` role)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run all tests
pytest

# Run a single test file
pytest tests/test_auth.py -v

# Run a single test
pytest tests/test_auth.py::test_login -v

# Lint / format
ruff check .
ruff format .

# Database migrations — see backend/ALEMBIC_GUIDELINES.md first
alembic upgrade head
alembic revision --autogenerate -m "description"
alembic history
```

The backend can be split into multiple processes via the `PROCESS_ROLE` env var (`api`, `worker`, `beat`, comma-separated). See `app/main.py:_process_roles()`.

### Frontend (React / TypeScript / Vite)

```bash
cd frontend

npm install

# Dev server (http://localhost:5173)
npm run dev

# Type-check + production build (tsc then vite build — both must pass)
npm run build

# Preview production build
npm run preview
```

## Architecture

### Monorepo Layout

- `backend/` — Python 3.11+ FastAPI app (see `backend/ALEMBIC_GUIDELINES.md` before DB work)
- `frontend/` — React 19 TypeScript app (Vite, reads version from `VERSION` file)
- `helm/clawith/` — Kubernetes Helm chart (see `helm/QUICKSTART.md`)
- `deploy/` — Production docker-compose variants (`docker-compose.yml`, `docker-compose-multi.yml`, `nginx/`)
- `setup.sh` / `restart.sh` — Root-level install & lifecycle scripts

### Backend Structure (`backend/app/`)

| Directory | Purpose |
|-----------|---------|
| `api/` | FastAPI route modules — one per domain (`auth.py`, `agents.py`, `chat_sessions.py`, `websocket.py`, …) |
| `services/` | Business logic — per-domain helpers, plus `services/llm/` (unified LLM abstraction), `services/trigger_runtime/`, `services/storage_runtime/`, `services/sandbox/`, `services/realtime_runtime/` |
| `models/` | SQLAlchemy 2.0 async ORM entities (one per table) |
| `schemas/` | Pydantic request/response models |
| `dao/` | **Data Access Object layer** — `BaseDAO` + per-entity DAOs. Uses a `ContextVar`-bound session so DAOs transparently join the request's transaction. Prefer over direct `select()` calls in new code. |
| `core/` | Auth, events, middleware, logging, security |
| `alembic/` | DB migrations (59+ revisions; follow `ALEMBIC_GUIDELINES.md`) |

**Critical files / layers:**
- `api/websocket.py` — Tool-calling loop (up to 50 iterations: LLM → Tool → Context reassembly), LLM streaming. Uses `call_llm_with_failover` from `services/llm/`.
- `api/gateway.py` — OpenClaw edge-node protocol (poll/report/send for local agents).
- `services/agent_tools.py` — All file-based tools (`read_file`, `write_file`, `send_message_to_agent`, etc.). Workspace paths are well-known: `soul.md`, `memory/memory.md`, `skills/`, `workspace/`.
- `services/agent_context.py` — Assembles LLM context from `soul.md`, system prompts, `memory.md`, skill index, relationships.
- `services/trigger_daemon.py` — Aware Engine scheduler (15s tick, 30s dedup, A2A wake chain depth ≤ 3). Trigger evaluation/invocation extracted to `services/trigger_runtime/`.
- `services/llm/` — Unified LLM client with failover (`failover.py`), streaming (`client.py`), tool-finish handling (`finish.py`).
- `services/storage.py` + `storage_runtime/` — Pluggable storage backend (local FS or S3-compatible).
- `app/database.py` — Engine, session factory, `_session_ctx` ContextVar used by `BaseDAO`.

### Frontend Structure (`frontend/src/`)

| Directory | Purpose |
|-----------|---------|
| `pages/` | Top-level routes — lazy-loaded in `App.tsx`. Heavy ones live in subdirs: `agent-detail/`, `enterprise-settings/` |
| `components/` | Reusable UI: `AgentSidePanel`, `FileBrowser`, `WorkspaceOperationPanel`, `MarkdownRenderer`, etc. |
| `stores/index.ts` | **Single file** with all Zustand stores (`useAuthStore`, `useAppStore`, `usePermissionStore`, `useI18nStore`) |
| `services/api.ts` | Single Axios client (all API calls go through here) |
| `hooks/` | Custom React hooks |
| `i18n/` | Translation JSON (`en.json`, `zh.json`) + `index.ts` + `templateTranslations.ts` |
| `types/` | Shared TypeScript types |
| `utils/` | Helpers |

**Critical files:**
- `App.tsx` — Router with protected routes; lazy-loads every page; sets up `NotificationBar`, `ProtectedRoute`, `CompanyAdminRoute`.
- `pages/AgentDetail.tsx` (~427 KB) — Agent chat UI, settings, triggers, relationships. Largest file in the repo.
- `pages/EnterpriseSettings.tsx` (~256 KB) — Enterprise config, channels, auth providers.
- `stores/index.ts` — Auth persistence via `localStorage` token; read it once on store init.

### Key Data Models (`backend/app/models/`)

- `Agent` — Digital employee entity (native or OpenClaw edge node)
- `Participant` — Multi-party communication routing anchor (determines left/right bubble rendering)
- `ChatSession` / `ChatMessage` — Full audit trail including tool_call snapshots
- `AgentTrigger` + `AgentTriggerExecution` — Aware Engine scheduling (`cron`, `once`, `interval`, `poll`, `on_message`, `webhook`)
- `AgentAgentRelationship` — Strict A2A access control (agents must have explicit relationship to communicate)
- `Tenant` / `OrgDepartment` / `OrgMember` — Multi-tenant isolation (all entities carry `tenant_id`)
- `LLMModel` — Provider/model registry used by `services/llm/failover.py`

### Multi-Tenant Pattern

Every database entity includes `tenant_id`. All queries must filter by tenant. The `OrgMember` table maps external channel users (Feishu/DingTalk/WeCom) to internal users.

### DAO / Session Pattern (since v1.10.2)

- `app/database.py:_session_ctx` — `ContextVar[AsyncSession | None]` carries the current request's session.
- `app/dao/base.py:BaseDAO` — Generic CRUD on top of that ContextVar. DAOs created so far: `user_dao`, `tenant_dao`, `org_member_dao`, `participant_dao`, `identity_dao`, `identity_provider_dao`, `invitation_code_dao`, `system_setting_dao`.
- **Rule of thumb:** new persistence code should go through DAOs (or extend `BaseDAO`) rather than opening new sessions with `async_session()` directly. The ContextVar pattern is what makes the 50-iteration tool-calling loop and the trigger daemon safe to share sessions across awaits.

### WebSocket Tool-Calling Loop

The core LLM execution in `api/websocket.py` runs up to 50 iterations. Each iteration: call LLM → parse tool calls → execute tools → reassemble context → repeat. Resource warnings fire at 80% of the round limit. High-risk tools (`write_file`, `delete_file`) have hard parameter validation. Streaming JSON fragments are reassembled by `extract_partial_content()`.

### Agent Workspace

Each agent has a private file workspace under `backend/agent_data/<agent-uuid>/` (mounted at `/data/agents/` in containers). Well-known paths inside: `soul.md` (personality), `memory/memory.md` (long-term memory), `skills/` (markdown skill defs), `workspace/` (general working files). All four are injected into every LLM context via `services/agent_context.py`.

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 15+ / SQLite (dev), Redis 7+
- **Frontend**: React 19, TypeScript, Vite 6, Zustand 5, TanStack Query 5, React Router 7, i18next
- **LLM**: Unified abstraction in `services/llm/` supporting OpenAI, Anthropic Claude, DeepSeek, and others
- **Integrations**: Feishu/Lark, DingTalk, WeCom, Slack, Discord, Jira/Confluence, Microsoft Teams
- **Linting**: Ruff (Python, line-length 120, target py311), TypeScript strict mode
- **Testing**: pytest + pytest-asyncio (asyncio_mode = "auto")

## Code Guidelines

- **Python Imports**: Python imports should be placed at the top of the file (file header) as much as possible. Avoid inline imports within functions or methods unless strictly necessary (e.g., to prevent circular import dependencies).
