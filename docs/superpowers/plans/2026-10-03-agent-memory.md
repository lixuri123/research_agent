# General Agent memory integration

**Goal:** Give ordinary Agent tasks bounded, provenance-aware long-term background without changing the research graph or granting capabilities from memory.

**Architecture:** Reuse LightMemoryStore and MemoryManager. AgentMemory selects active semantic/episodic records in owner/project/session scope, prioritizes the current project, limits serialized context, and revalidates versions before use. Initial selection is checkpointed; task-local memory_search is read-only. Results contain reference metadata and warnings for audit.

**Constraints:** No live model requests, embeddings or new dependencies. No automatic activation, no trusted-memory writeback, no new research workflow. Memory is untrusted historical background, subordinate to current requests and tool authorization. Invalid or unavailable memory degrades to empty background with a visible warning. Previously started tasks without a memory checkpoint resume without adding new background.

## Steps

- [x] Write a small focused test file: scope/lifecycle/hash filtering, bounded selection, revision revalidation, Agent prompt integration, revocation between steps, and read-only tool behavior.
- [x] Add active-entry lookup and optional recall filtering/scope prioritization without changing legacy defaults.
- [x] Add `research_agent/memory/agent_context.py` for bounded selection, verification, and metadata.
- [x] Integrate initial snapshot and background into planner and decision calls, revalidate memory tool observations before sending them to models, and expose memory metadata in task results/events.
- [x] Register memory_search in TaskCapabilities using the task's immutable owner/project/session context.
- [x] Run only focused tests plus directly related store checks; update documentation and stale capability descriptions.

## Acceptance

Unrelated owners/projects/sessions, candidate/stale/deleted entries and changed Markdown never enter new prompts. Memory contents are not injected as system instructions. Revoking/updating a selected entry removes it from subsequent prompts. Search grants no writes or external-tool authorization. No extra model call is used to retrieve memory.
