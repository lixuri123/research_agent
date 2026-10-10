# Agent file proposals

**Goal:** Allow ordinary Agent tasks to propose changes to existing workspace text files, with human-reviewed application.

**Boundary:** No direct Agent writes, shell execution, file creation/deletion, sandbox or scheduler. Only a live task owning its workspace may submit proposals. User approval is separate from task completion and is bound to immutable proposal contents.

- [x] Add focused tests for durable proposals, idempotency, exact review tokens, rejection, conflict detection, workspace/task constraints.
- [x] Add SQL-backed proposal service, bounded unified diff, and pending/applying/applied/rejected/conflict states. Reuse WorkspaceService path checks and optimistic save; recover approved application after interruption without blindly overwriting.
- [x] Expose listing, preview and human-decision endpoints; initialize schema before serving requests.
- [x] Register workspace_propose_write only in scoped worker capabilities. Use run + step identity for proposal idempotency; no apply tool is exposed to the model.
- [x] Add inline file-change cards with diff preview, explicit confirm/reject, and unsaved-editor protection. Refresh proposal state independently of model output.
- [x] Run focused tests and frontend build; document current limits.

Verification: 7 focused tests passed; TypeScript/Vite production build passed. Read-only review found no blocking issues. No live model calls, broad test suite, or browser interaction verification. Additional lease/owner mismatch regression cases remain optional follow-up coverage; production guards are implemented and were reviewed.
