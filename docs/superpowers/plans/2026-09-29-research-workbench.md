# Research Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete React research workbench served by the existing FastAPI application and make the full project reproducible with uv.

**Architecture:** A Vite React SPA calls the existing same-origin REST API through a typed client. FastAPI gains one task-list endpoint and conditionally mounts the production frontend build after all API routes. Docker builds the SPA in a Node stage and installs Python dependencies from `uv.lock` in a uv-managed runtime stage.

**Tech Stack:** Python 3.10–3.12, FastAPI, SQLAlchemy, uv, React 19, TypeScript, Vite, react-markdown, lucide-react, npm

## Global Constraints

- Use `pyproject.toml` and `uv.lock` as the only maintained Python dependency sources.
- Keep the API on `127.0.0.1:8000` for local use.
- Cover tasks, events, approval, reports/evidence, sessions, memories, and Skills.
- Add only one focused backend test; use type checking, production build, and a final smoke check for the frontend.
- Preserve all existing API contracts.

---

### Task 1: Reproducible uv environment and task-list API

**Files:**
- Modify: `research_agent/storage.py`
- Modify: `research_agent/api.py`
- Modify: `tests/test_worker_api.py`
- Create: `uv.lock`
- Delete: `requirements.txt`
- Delete: `constraints.txt`

**Interfaces:**
- Produces: `Repository.list_runs(owner_id='local', limit=50, offset=0) -> list[dict]`
- Produces: `GET /research?limit=50&offset=0 -> {"tasks": [...]}`

- [ ] Add one test that submits two tasks, calls `GET /research`, and asserts newest-first ordering plus topic projection.
- [ ] Run `uv --cache-dir .uv-cache run pytest tests/test_worker_api.py -k list_research -q` and confirm it fails because the route is absent.
- [ ] Implement `list_runs` using `select(runs).where(owner_id).order_by(created_at.desc()).limit(limit).offset(offset)` and return projected JSON-safe rows.
- [ ] Add the collection route before `/research/{task_id}`, validate `limit` from 1–100 and `offset >= 0`, and return `task_id`, `run_id`, `status`, `topic`, `session_id`, `current_report_version_id`, and `created_at`.
- [ ] Generate `uv.lock`, sync with `uv sync --extra dev --frozen`, and remove the duplicate requirements files.
- [ ] Re-run only the focused test and confirm PASS.

### Task 2: React workbench

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/components/ResearchViews.tsx`
- Create: `frontend/src/components/KnowledgeViews.tsx`

**Interfaces:**
- Consumes: all REST routes from `research_agent/api.py`, including the new `GET /research`.
- Produces: `npm run build` output in `frontend/dist`.

- [ ] Define typed request helpers for health, tasks, task detail/events/report/approval, session messages, memories, memory lifecycle actions, and Skills.
- [ ] Build the shell with responsive sidebar, service status, navigation, toast errors, and editorial visual tokens.
- [ ] Build dashboard, research creation, task list/detail, two-second active-task polling, Markdown report rendering, evidence/citation panels, and approval controls.
- [ ] Build session viewer/composer, memory list/search/create/edit/activate/invalidate/delete/publish controls, and Skill list.
- [ ] Add accessible loading, empty, offline, confirmation, and error states.
- [ ] Run `npm run build` and confirm TypeScript and Vite complete successfully.

### Task 3: Same-origin serving, Docker, and operator documentation

**Files:**
- Modify: `research_agent/api.py`
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Produces: `GET /` serving `frontend/dist/index.html` when built.
- Produces: a Docker image containing both the SPA and uv-managed Python runtime.

- [ ] Add conditional static mounting after API route declarations so backend imports still work when `frontend/dist` is absent.
- [ ] Convert Dockerfile to a Node frontend build stage plus an official uv-based Python stage using `uv sync --frozen --no-dev`.
- [ ] Update README with `uv sync --extra dev`, frontend development commands, production build/start commands, and the project-local cache workaround.
- [ ] Ignore `.uv-cache`, `frontend/node_modules`, and `frontend/dist` while ensuring Docker receives the source required for both builds.
- [ ] Run the focused backend test and `npm run build`.
- [ ] Start FastAPI on an unused port, verify `/health`, `/`, and `/research`, then stop the temporary process.

