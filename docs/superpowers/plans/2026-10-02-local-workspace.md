# Local workspace implementation plan

**Goal:** Bind a real local directory to a durable workspace, keep multiple conversations in it, and let the user and read-only Agent inspect its files.

**Architecture:** A SQL-backed workspace registry references directories, never copies them. Workspace identity and conversation identity are separate; workspace identity also supplies the existing project context. A dedicated file service validates every path. React adds a workspace selector and an optional file side pane beside chat.

**Constraints:** Keep uv, avoid new dependencies, no broad regression suite or live model calls. Default server allowlist is its current working directory. No shell execution, file deletion, file creation or Agent writes. Local single-user service, loopback only, not a multi-user security sandbox. User explicitly grants model access to readable text at binding.

## Execution

- [x] Add targeted tests for persistence, traversal/secret denial, edit conflicts, permission-scoped Agent tools and task association; run only this file.
- [x] Implement `research_agent/workspaces.py`: durable registry, immutable session association, bounded UTF-8 reads/listing, optimistic text saves. Hide sensitive paths, reject links and multi-link files.
- [x] Add workspace API routes before static mount; propagate `workspace_id` through durable task payloads and list results. Preserve legacy unbound tasks.
- [x] Add read-only workspace tools to `TaskCapabilities`; the Worker uses the same validated service. Include directory context in the tool catalog, not arbitrary file contents in prompts.
- [x] Add frontend workspace selector/binding form and file pane. Filter conversations by workspace, carry context on task submission, offer dirty-state confirmation and external-change feedback.
- [x] Verify with focused pytest and `npm run build`; document local directory allowlist, permissions, restart and current limits.

Verification: 6 focused tests passed, TypeScript/Vite build passed. UI smoke used a temporary database and browsed an existing Markdown file without writing it or invoking a model. The smoke exposed concurrent first-request schema creation; API lifespan now initializes task and workspace schemas before serving requests, with a targeted startup check.

## Acceptance

Opening another conversation does not discard workspace membership. Restarting API/Worker preserves bound directories. A task can browse/read only its assigned, model-readable workspace. Saving requires the last content hash; conflicting edits are not overwritten. PDF/binary/large files are not loaded as text. The UI never claims terminal or Agent write capabilities.
