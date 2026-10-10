# dsh-style read-only execution implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the native helper and review; keep integration in this session. No Git repository is present, so no worktree or commits are possible.

**Goal:** Deliver the approved first phase: fixed workspace text search through a Windows restricted-token helper, durable execution records and explicit workspace permission.

**Architecture:** Python owns task/workspace authorization, input snapshots and result persistence. A C++ Windows helper owns restricted-token creation, Low integrity, suspended spawn and Job lifetime; its target is trusted rg only. Only read-only operations are exposed; arbitrary scripts and original-directory ACL mutations are excluded.

**Tech Stack:** Python/uv, SQLAlchemy, FastAPI, React/TypeScript, Windows SDK C++.

## Global Constraints

- Execute only trusted fixed text search. No shell=True, arbitrary flags/scripts, network isolation claim or unconfined fallback.
- Windows file enforcement is partial; file reads and network are not isolated.
- Independent workspace execution permission defaults off. Existing agent_read is also required.
- Default execution timeout 15 seconds, retained output 16 KiB. Bound input preparation to 100 files, 4 MiB and 5 seconds.
- User workspace ACLs remain unchanged. No administrator/system account/firewall initialization.
- Tests are targeted; no live LLM calls or full project suite.

## Task 1: native Windows helper

Files: `native/windows-sandbox/main.cpp`, `native/windows-sandbox/build.ps1`, `native/windows-sandbox/README.md`, `tests/test_windows_execution_native.py`.
Read requirements in `docs/superpowers/plans/dsh-native-brief.md` for the exact helper protocol.

- [x] Write focused native smoke/boundary tests first; run to show missing helper failure.
- [x] Implement --probe and read-only --search, own target stdio pipes and emit one helper JSON envelope.
- [x] Build using available cl/Windows SDK; verify restricted search and timeout cleanup on synthetic materials only.
- [x] Read-only review before claiming native capability; no writable ACL phase in this deliverable.

## Task 2: Python execution service and durable permission

Files: `research_agent/execution/{__init__,policy,storage,service}.py`, `research_agent/execution/providers/windows_acl.py`, `research_agent/config.py`, `research_agent/capabilities.py`, `research_agent/api.py`, `tests/test_execution.py`.

Interfaces: `ExecutionService(workspaces, settings, provider=None).status()`, `.permission(workspace_id)`, `.set_permission(workspace_id, enabled)`, `.search(workspace_id, run_id, worker_id, operation_id, arguments, check_lease=None)`, `.list(workspace_id, run_id)`.

- [x] Write tests for disabled permission, protected input staging, changed replay request, saved-result reuse, unavailable backend and lease checks. Run `uv run pytest tests/test_execution.py -q` to observe failures before implementation.
- [x] Validate only query/max_results; snapshot WorkspaceService-readable UTF-8 files into randomized internal inputs with manifest versions, skip protected/link/binary/oversize materials and report omissions.
- [x] Store permissions in a separate owner/workspace table to avoid migrations of existing workspace schema. Store execution identity bound to owner/run/step, argument digest and manifest; use SQL task row lease checks and per-operation OS lock to prevent simultaneous live-worker execution.
- [x] Resolve helper and rg from trusted installation locations, reject workspace/execution writable locations; sanitize target environment, poll and cancel on lease loss. If unavailable, return bounded structured error without spawning a target.
- [x] Expose workspace execution permission, backend status and per-run execution log endpoints; add workspace_search_text to TaskCapabilities only for enabled, readable workspace and live worker context, rechecking at actual call.
- [x] Run the focused tests and two existing file-proposal regression tests; no full workspace/project suite.

Read-only search is auto-authorized by explicit permission; no new tool-approval state is needed in phase 1. Keep future one-shot risk approval and side-effect recovery as the next planned phase, not a fake working UI.

## Task 3: UI, docs and release verification

Files: `frontend/src/{types,api,AgentApp}.tsx/ts`, `frontend/src/components/ExecutionPanel.tsx`, `docs/local-execution.md`, `README.md`, `.env.example`.

- [x] Match the existing restrained agent conversation style; add a workspace permission control with explicit partial-isolation confirmation and real backend status. Show bounded per-turn logs, not an unrestricted terminal.
- [x] Document native build, rg configuration, permission activation, disabled reasons and security boundaries; main API and worker remain uv-managed.
- [x] Run `npm run build` in frontend once and a small final targeted Python/native set. Read-only final review; fix concrete blockers, not broad unrelated refactoring.

## Progress

- Design approved by user on 2026-10-03; strict read/network isolation requirement replaced for this phase by explicitly disclosed dsh partial backend.
- Plan self-review: staged writable copies, original-file writeback and tool-risk approval are later phases; no fake implementation or blanket safety claim.
- Final verification: 15 targeted tests passed in 3.78 s, including separate native boundary diagnostic and two original-file proposal regressions. Frontend `npm run build` passed. No LLM/network requests or full project suite.
- Read-only final review accepted first phase after fixing trusted program location and conservative saturation/truncation checks. Native binary built with installed MSVC; no Git/commit/worktree operations.
- Host caveat: the Codex restricted execution context can fail CreateRestrictedToken with Win32 87. Normal-user-token verification passed using reviewed escalation; runtime probe errors remain fail-closed. This is not an unrestricted target fallback.
- Crash caveat: the Python operation lock is not inherited by helper. Normal lease cancellation waits for helper termination before releasing it; parent death closes its liveness pipe, while a recovered read-only call can briefly overlap cleanup of the old helper. No side-effect command may use this recovery rule; future write phase needs stronger broker ownership/reconciliation.
