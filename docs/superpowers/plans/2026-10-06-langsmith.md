# LangSmith Implementation Plan

**Goal:** Trace Worker attempts, model calls, tools and delegated agents with business IDs, without changing durable task semantics.

**Architecture:** Explicit SDK tracing_context controls reporting. A safe task context creates an attempt root; small instrumented wrappers capture selected inputs and outputs. Native ChatOpenAI and LangGraph runs inherit that context, including copied child thread contexts.

**Tech Stack:** Python, LangSmith 0.14.x, LangChain, LangGraph, pytest.

**Completion:** Implemented and reviewed on 2026-10-06. Full Python suite: 171 passed, 1 skipped (Postgres smoke requires TEST_DATABASE_URL). Offline dependency lock check passed. Actual LangSmith cloud upload not verified: the local configuration has no LangSmith Key and tracing remains disabled; existing .env was preserved.

**Review corrections:** Start lease heartbeat before tracing initialization; flush queued traces with a five-second bound on orderly shutdown. Make existing chronological and cancellation fixtures deterministic instead of depending on equal clock ticks, short leases or releasing a child before cancellation commits. Business storage/cancellation rules remain unchanged.

## Constraints

- Default off; obey LANGSMITH_TRACING even when legacy environment flags are set.
- Never serialize repository/client objects or provider credentials.
- Reporting failures must not retry business operations or replace their exceptions.
- Preserve existing .env; use offline capture tests and document live activation separately.
- Workspace is not a Git checkout, so edit in place and do not attempt commits/worktrees.

## Task 1: Configuration and safe SDK contexts

- [x] Add tests in tests/test_observability.py for disabled/missing-key/configured tracing, credential exclusion, initialization/teardown failure, and exception identity.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/test_observability.py -q` and confirm missing behavior fails.
- [x] Extend Settings with endpoint/workspace. Replace obsolete global initialization with a cached explicit Client and task_trace/trace_span/observed helpers in observability.py.
- [x] Verify tests pass with captured SDK operations and no cloud requests.

## Task 2: Execution integration

- [x] Add Worker tests with a temporary SQL Repository and SDK RunTree capture for parent IDs, result status, repeated attempts and disabled reporting.
- [x] Add model and threaded child tests under the real SDK context. Use local ChatOpenAI _generate responses to avoid external model requests.
- [x] Run new tests and verify failures before integrating.
- [x] Wrap Worker claimed execution; persist langsmith_trace_started event with trace ID and project. Capture task result status/content after execution.
- [x] Instrument ModelGateway.invoke/invoke_structured, TaskCapabilities.call, DelegationController.call/_run with selected input/output callbacks, excluding self and credentials.
- [x] Verify tests plus Worker, model gateway, ordinary Agent and dynamic delegation regressions.

## Task 3: Dependency, instructions and final verification

- [x] Declare langsmith>=0.14.1,<0.15 directly and update uv.lock offline.
- [x] Extend .env.example and README with activation, endpoints, data reporting scope and restart/view instructions.
- [x] Inspect modifications for credential exposure and failure-path behavior; run focused and broader Python regressions.
- [x] Mark plan complete and report passing checks and whether actual cloud connectivity was tested.
