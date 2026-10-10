# Storage implementation report

> 历史文档：保留设计与实施背景，当前结构请参阅 [目录说明](../architecture/directory-structure.md)。

Implemented SQLAlchemy Core schema in research_agent/persistence/repository.py. Repository records, report versions, stages, and events persist transactionally on PostgreSQL or explicitly configured SQLite; exceptions propagate and there is no memory fallback.

Contract: absent get/get_run/get_report/get_stage return None. Invalid lease, status, approval, and conflicting idempotency raise ValueError. submit/claim/get/get_run/heartbeat/finish/approve return JSON-compatible run records. Records include payload, result, approval, current_report_version_id, worker_id, lease_expires_at (Unix seconds), and revision. save_report returns immutable version record with report and canonical SHA-256 content_hash. Stage writes upsert (including budget ledger); get_stage returns the result payload directly. Internal run/stage/report-list/event reads rely on the caller to enforce owner scope; public task/report reads and approvals enforce owner_id.

Claims use PostgreSQL SKIP LOCKED and optimistic conditional updates. Expired leases can be reclaimed; stale or expired workers cannot heartbeat or finish. Each worker_id must identify a unique worker incarnation because the required API does not carry a separate fencing token. Version/hash-bound approvals are persisted in the same transaction as queue/rejection state and audit event. Conflicting decisions fail; repeated same decision returns existing record. Rejection removes final_report from the result atomically. New report versions invalidate prior decision.

TDD evidence: before implementation, direct Python assertion failed with “Durable storage repository is missing”. The pytest command initially could not execute because root-managed dependencies were still being installed (pytest and SQLAlchemy absent). Compileall passed. Full pytest evidence will be appended once dependencies are available.

Tests cover file-database reopen, owner scoping, idempotency conflict, lease expiry/reclaim/fencing, heartbeat, version mismatch, decision idempotency/conflict, rejection, JSON stage upsert, required report before approval wait, and an optional PostgreSQL durability smoke gated by TEST_DATABASE_URL.

Final verification: `.venv/Scripts/python.exe -X utf8 -m pytest tests/persistence/test_storage.py -q` passed with **13 passed, 1 skipped** (1.79s). PostgreSQL smoke was skipped because TEST_DATABASE_URL was unset; actual PostgreSQL execution remains unverified.

Additional red/green cycles: lease expiry between state read and update initially failed (worker could finish after expiration); optional artifact worker_id and concurrent heartbeat/stage cases failed due missing API. Added SQLite BEGIN IMMEDIATE mutation transactions, PostgreSQL SELECT FOR UPDATE for run mutations, live-expiry predicates in conditional updates, optional worker_id fences for save_report/save_stage; those tests passed. A changed version after approved resume initially could publish without fresh approval; persisted approval_required now prevents that and its regression passed.

Schema note: create_all creates new schema; existing legacy tables are not migrated. This phase-one repository introduces a new schema; existing development databases created during partial implementation should be recreated. Production upgrades need explicit schema migrations.
