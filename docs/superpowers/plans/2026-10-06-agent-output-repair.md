# Agent output repair implementation plan

**Goal:** Eliminate identical format retries and unnecessary greeting task work.

**Architecture:** ModelGateway separates transport errors from bounded output repair, traces validation, and supports opt-in json_object. Personal Agent selects narrowly defined greeting input before capability discovery; other tasks plan with a separate prompt and validated text.

- [x] Add failing model gateway regressions for format feedback, one-repair bound, opt-in response_format, validation trace errors and accurate changed-message budget.
- [x] Implement gateway settings and repair/validation logic; run gateway tests.
- [x] Add greeting tests ensuring one call and no memory/capability discovery; add planning rejection/isolation tests.
- [x] Implement greeting checkpoint/result and isolated planning; update old greeting tracing test to new expected behavior.
- [x] Keep test tracing offline even when user's .env enables cloud tracing.
- [x] Update docs and example settings; run full regressions; request read-only review of failure/recovery/budget paths.

Validation: full offline suite passed with 186 tests and one skipped PostgreSQL environment smoke test. The uploaded trace's exact root input selects the greeting path. No real model requests or credential changes. Independent read-only review completed with no critical or important findings in lease fencing, recovery, budget accounting, provider overrides, or tracing isolation.
