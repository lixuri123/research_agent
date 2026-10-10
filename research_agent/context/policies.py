"""Versioned routing policy and source allowlists."""

ROUTER_VERSION = "context-router/v1"
CONTEXT_VERSION = 1
COMPLETED_TURN_STATUSES = frozenset({"completed", "partial", "insufficient_evidence"})
ACTIVE_TASK_STATUSES = frozenset({"queued", "running", "waiting_approval"})
SOURCE_KINDS_BY_ROUTE = {
    "independent": frozenset({"profile", "memory"}),
    "followup": frozenset({"history", "profile", "memory"}),
    "task": frozenset({"history", "profile", "memory", "task"}),
    "multi_task": frozenset({"history", "profile", "memory", "task"}),
}
