# Phase One Implementation Plan

> 历史文档：保留设计与实施背景，当前结构请参阅 [目录说明](../architecture/directory-structure.md)。

> Agent workers: use subagent-driven-development and test-driven-development. Current directory has no Git repository; changes stay in place and no commit/worktree is fabricated.

Goal: durable asynchronous research execution with version-bound approval and traceable evidence.
Architecture: SQLAlchemy repository (Postgres production, file SQLite for offline tests), separate Worker, LangGraph persistent checkpoints, typed evidence/report outputs.
Constraints: Python 3.10+, no real model calls or credentials needed for tests; no silent memory persistence fallback; no final report or trusted memory on rejection; use run_id as thread_id.

## Task 1 — durable task repository
- [x] Add research_agent/persistence/repository.py and tests/persistence/test_storage.py using red/green tests.
- [x] Repository(database_url) creates SQLAlchemy schema. submit(topic, requirements='', session_id=None, owner_id='local', idempotency_key=None, budget=None) -> record with task_id/run_id/status/payload; duplicate owner/key and identical input returns original; mismatch raises ValueError.
- [x] get(task_id, owner_id='local'), get_run(run_id), claim(worker_id, lease_seconds=120), heartbeat(run_id, worker_id, lease_seconds), finish(run_id, worker_id, status, result), save_report(run_id, report: dict) -> version record, approve(report_version_id, approved, owner_id='local') -> run record, get_report(version_id, owner_id='local'), reports_for_run(run_id), events(run_id). Records are JSON-compatible dicts. Claim sets running with worker_id and expiry; expired running is reclaimable. Finish fenced by owner lease, stores result, clears lease; waiting_approval is unclaimable until decision. approve binds current saved version/hash and persists decision; approved queues same run for resume, rejected atomically terminates and excludes final_report. Durable stages: save_stage(run_id, stage, input_hash, result), get_stage(run_id, stage, input_hash) with JSON payload.
- [x] Tests cover reopen, duplicate submit/conflict, expired claim, fencing, approval/rejection/version mismatch and JSON artifacts; optional Postgres integration via TEST_DATABASE_URL.

## Task 2 — evidence and workflow
- [x] Extend models with EvidenceChunk, Finding, Claim/ClaimEvidence and report verification metadata. Add evidence.py for stable version/chunk IDs, offsets, abstract-only labels, citation validation and evidence gates.
- [x] Test rejection loops, no evidence, invalid citation, rejected approval and revision feedback with fake model agents; use real graph and persistent checkpointer.
- [x] Add extract node, evidence gate, typed findings, source-aware Reviewer; bind claims to source chunks; preserve invalid citations as validation errors. Count revision and reset initial state. Budget provides total deadline/calls/token/document limits; LLM gateway checks per-call and disables layered retries.
- [x] Replace memory checkpoint fallback with explicit SqliteSaver (local) / PostgresSaver context lifecycle; add dependencies and compose Postgres.

## Task 3 — worker/API
- [x] Replace BackgroundTasks and Redis authority with repository-backed submit/query/approval. Worker claim/recover uses graph state under run_id; save report before waiting status; approval enqueues resume. Store stage artifacts, persist terminal outputs; rejection never invokes consolidation.
- [x] Tests cover API idempotency and worker recreation at approval, plus restart between stages and fail-closed storage. Use separate CLI python -m research_agent.worker.

## Task 4 — evaluations and rollout
- [x] Fix citation precision denominator, null unscorable metrics, failure handling and context-injection measurement; tests with invalid/no/extra citations.
- [x] Update README/.env.example/Dockerfile/docker-compose/regression/demo. Run isolated pytest, compileall and container/Postgres tests if available. Final review checks first-stage acceptance criteria.

## Verification ledger

All four implementation tasks completed. Final offline suite: 48 passed, 1 optional Postgres test skipped. Postgres/container runtime and real model quality remain unverified; see phase_one_acceptance.md for precise scope. No Git repository exists, so no commits created.
