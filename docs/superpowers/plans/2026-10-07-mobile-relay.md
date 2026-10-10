# Mobile Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a locally testable mobile control flow with authenticated WSS relay, desktop pairing confirmation, existing task execution and version-bound approvals.

**Architecture:** A standalone FastAPI relay hosts the mobile bundle and routes bounded messages between authenticated browser devices and an outbound desktop connector. The desktop stores final authorization in its SQL database and exposes only a named business-method allowlist. Existing API/Worker and local workbench remain operational without remote connectivity.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy, httpx, websockets 16, React/TypeScript/Vite; QR generation as a small explicit dependency if needed.

## Global Constraints

- Approved design: ../specs/2026-10-07-mobile-relay-design.md.
- Trusted relay with HTTPS/WSS, no claim of end-to-end encryption; development HTTP/WS only on explicit loopback setting.
- No real model calls, real credentials, public deployment or external messaging during implementation.
- No Git metadata exists; save edits in place and use file-based review rather than commits.
- Credentials never in URL queries, logs or browser localStorage; browser authentication in Secure/HttpOnly/SameSite cookie.
- Max message 1 MiB, per-peer queue 100 messages and 4 MiB; heartbeat 20s, stale 60s, reconnect cap 30s.
- Personal sessions and existing workspace grants scoped explicitly; device identity cannot replace local data owner.
- Existing API transport and remote failure must not break local task execution.

## Task 1: Protocol, authenticated relay and persistent pairing

**Files:** research_agent/remote/{__init__,protocol,relay_store,relay}.py; tests/remote/test_relay.py.

**Interfaces:** Relay factory `create_app(database_url, public_url, development=False) -> FastAPI`; global app loaded from environment for deployment. Host bootstrap HTTPS `/remote/hosts/register` returns host_id and secret once. Host WSS `/remote/ws/host` authenticates Authorization Bearer secret. Host control messages `pair.create`, `pair.confirm`, `device.revoke`, `host.disable` have request_id and structured result. Invite redemption POST `/remote/pair/redeem`, pairing completion POST `/remote/pair/finish`, state GET `/remote/session`, browser WSS `/remote/ws/device`. Invite token lives in fragment; finish proves pending device secret with no access before host confirmation. Desktop receives `pair.pending` with invite_id/device_id/code/name, browser is issued cookie only after host authorization. Forwarded request identities derived from validated cookie; relay strips client identity fields and attaches `_device_id`. Relay returns offline error if no host. All responses from host routed to matching device+request, never broadcast across devices. `task.subscribe` events routed only to subscribing device.

- [ ] Write TestClient tests for unpaired session, expired/reused invite, rejected origin, cookie issuance after confirmation, host/device routing and revocation. Example: `assert client.get('/remote/session').status_code == 401`.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/remote/test_relay.py -q --basetemp=.tmp/relay-tests`; first run must fail due to missing implementation.
- [ ] Implement persistent SQLite/SQLAlchemy state and atomic invite transition; hash credentials, rate-limit bootstrap/redemption, never expose persistent raw host credential again.
- [ ] Implement websocket lifetimes, bounded queues, heartbeat and stale cleanup; host replaces prior connection safely, idle credentials revalidated and revoked sockets closed.
- [ ] Repeat test command and inspect code against design. Document any exact protocol adjustments before next task.

## Task 2: Local authorization, connector and business adapter

**Files:** research_agent/remote/{local_store,credentials,adapter,connector,local_routes}.py; research_agent/api.py; tests/remote/test_local.py.

**Interfaces:** Local store uses Repository.engine and a single enabled host config, paired device records, invitations and revocation outbox. Connector reads local state, connects to configured relay and responds to named request messages. `adapter.dispatch(device_id, message)` checks enabled state, authorization version and workspace before every operation, then calls fixed existing business routes through in-process ASGI httpx (not generic client-supplied URL). Local management `/remote/local/status`, `/remote/local/config`, `/remote/local/invites`, `/remote/local/devices/{id}/decision`, `/remote/local/devices/{id}` are loopback/local browser only, have origin checks and prohibit remote protocol invocation. Windows secret protection via DPAPI, non-Windows restricted file; outside workspace roots. Config can specify loopback development relay explicitly.

- [ ] Write tests for revoked devices, workspace/report/file-change ID leakage, injected tool capabilities, unknown methods, repeated task key, and no network/model calls. Example: `with pytest.raises(PermissionError): adapter.dispatch(revoked_id, request)`.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/remote/test_local.py -q --basetemp=.tmp/local-tests` and verify missing behavior.
- [ ] Implement permission store, protected host credential, explicit typed schemas and allowlist adapter, safe local management routes.
- [ ] Implement connector pairing state machine, re-confirmation after partial failure, offline-safe revocation and reconnect. Task events use durable cursor with gap-safe supplement or snapshots, never raw arbitrary HTTP forwarding.
- [ ] Repeat focused tests; verify task success follows SQL commit and local approvals retain existing version checks.

## Task 3: Mobile browser and desktop connection settings

**Files:** frontend/src/remote/{transport,MobileApp,RemoteSettings}.tsx or .ts, frontend/src/remote/remote.css, frontend/src/main.tsx, frontend/src/AgentApp.tsx, frontend/src/api.ts, frontend/package.json if QR support needed.

**Interfaces:** Browser entry `/mobile/` on relay serves same Vite build selected by pathname. Transport `call(method, payload, idempotencyKey?) -> Promise` generates request_id only per attempt, holds stable mutation key at operation layer. Session HTTPS establishes host/device identity before WSS. `RemoteSettings` uses local management API, displays QR, pending code, device scope and revoke. Mobile UI opens pairing fragment, removes it immediately, waits for desktop approval, then lists host/workspaces/sessions/tasks and supports ordinary/research submit plus report/file approval. Existing local `api.createTask(body, key = crypto.randomUUID())` and createResearch accept reusable key without breaking callers.

- [ ] Add meaningful transport tests for disconnect rejection, response correlation, immutable retry key and subscription deduplication with available test runtime; run and observe failure before transport implementation.
- [ ] Implement compact responsive interface with existing visual language, clear offline/unknown submission states, errors and disabled approval when data incomplete. Do not expose provider edits or directory binding on mobile.
- [ ] Add desktop settings to existing avatar menu and modal structure; QR uses a real encoded matrix, not remote image generation service.
- [ ] Run `npm run build` in frontend; perform visual checks of 390px mobile and desktop settings when local browser tooling is available.

## Task 4: Startup, deployment docs, integration and independent review

**Files:** scripts/start.py, tests/test_start_services.py or existing startup tests, pyproject.toml/uv.lock, docs/guides/mobile-remote.md, config/remote-relay.example.env, README.md, docs/README.md, deployment example.

- [ ] Add failing tests ensuring optional connector failure cannot stop API/Worker, shutdown cleans all launched processes, loopback-only development requirements and pairing/request/reconnect in real websocket integration.
- [ ] Integrate optional connector supervisor with existing launcher; disabled by default, no secrets in process args and no port exposure. Explicitly declare websocket/QR dependencies and update lock coherently without unrequested upgrades.
- [ ] Document local simulation, production relay/TLS deployment, trusted-server boundary, device recovery, heartbeat/offline behavior and troubleshooting.
- [ ] Run focused remote tests, affected existing API/workspace/approval/startup tests, frontend build and integration smoke. Do not run unrelated broad suite absent new concern.
- [ ] Independent reviewer checks files against design and reports actionable correctness/security gaps; fix all important findings, repeat affected tests and final verification.
- [ ] Update this ledger with exact results and limitations. Public deployment remains a separate user-authorized stage requiring actual host/domain configuration.

## Progress ledger

- Planning: complete; no unapproved scope change.
- Baseline: 11 API/workspace tests and 4 launcher tests passed. Windows sandbox socketpair hangs require the already-approved unrestricted pytest prefix.
- Task 1: complete; independent re-review approved, 10 focused relay tests passed. Report: .tmp/mobile-relay-task1-report.md; review: .tmp/mobile-relay/task-1-review.md.
- Task 2: implemented; 10 focused local tests and 28 combined affected tests passed before user's testing pause. Preliminary independent source review found no confirmed blocker; final runtime validation deferred to user. Added explicit personal_access in workspace.list for mobile scope selection after the testing pause. Report: .tmp/mobile-relay/task-2-report.md.
- Task 3: implemented; mobile browser, desktop connection settings, local QR generation, frontend transport and iPhone foreground recovery. Production delivery build completed with exit 0, frontend/dist generated (Vite main-bundle size advisory only). No subsequent tests or browser/physical-device checks. Report: .tmp/mobile-relay/task-3-report.md.
- Task 4: implemented; startup, static hosting, dependencies and deployment documentation received source self-review only. No subsequent tests or runtime validation. Report: .tmp/mobile-relay/task-4-report.md.
- User steering (2026-10-08): stop test workflows; prioritize implementation. User will perform acceptance testing. Subsequent changes receive code self-review and necessary static artifact generation only; no new test runs or browser smoke. Parallel frontend/startup work uses disjoint ownership and dispatching-parallel-agents skill.
- Device clarification (2026-10-08): user uses iPhone. Mobile entry targets Safari with safe-area layout and foreground reconnect; no physical iPhone validation performed.
- Delivery: all four implementation tasks are written. Acceptance workflows remain explicitly deferred to the user. Public Relay deployment and real phone reachability still require a server/domain/TLS configuration. Existing approval routes may return already-processed conflicts after unknown retries; inspect final version/state before resubmitting. Desktop must stay running and awake.
