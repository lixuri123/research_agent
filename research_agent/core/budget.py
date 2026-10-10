"""Per-run budgets; reservations saved before model calls."""
from __future__ import annotations
import time
from contextvars import ContextVar
from contextlib import contextmanager
from threading import RLock

current_execution_guard = ContextVar('execution_guard', default=lambda: None)

class BudgetExceeded(RuntimeError):
    pass

class Budget:
    def __init__(self, limits=None, usage=None, save=None):
        self.limits = limits or {}
        self.usage = dict(usage or {"calls": 0, "tokens": 0})
        self.save = save or (lambda usage: None)
        self._lock = RLock()

    def check(self):
        current_execution_guard.get()()
        if time.time() >= self.limits.get("deadline", float("inf")):
            raise BudgetExceeded("任务总时间预算耗尽")

    def reserve(self, input_tokens, output_tokens):
        with self._lock:
            self.check()
            calls = self.usage.get("calls", 0) + 1
            tokens = self.usage.get("tokens", 0) + input_tokens + output_tokens
            if calls > self.limits.get("max_calls", 100):
                raise BudgetExceeded("模型调用次数预算耗尽")
            if tokens > self.limits.get("max_tokens", 200000):
                raise BudgetExceeded("Token 预算耗尽")
            candidate = dict(self.usage, calls=calls, tokens=tokens)
            self.save(dict(candidate))
            self.usage = candidate

    def settle(self, reserved, actual):
        if actual is not None:
            with self._lock:
                candidate = dict(self.usage)
                candidate["tokens"] += actual - reserved
                self.save(dict(candidate))
                self.usage = candidate

current_budget: ContextVar[Budget | None] = ContextVar("research_budget", default=None)

def remaining_timeout(configured):
    budget = current_budget.get()
    if budget:
        budget.check()
        return min(configured, max(.001, budget.limits.get('deadline', float('inf')) - time.time()))
    return configured

@contextmanager
def budget_scope(budget):
    token = current_budget.set(budget)
    try:
        yield budget
    finally:
        current_budget.reset(token)
