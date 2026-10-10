import time
import pytest
from research_agent.core.budget import Budget, BudgetExceeded, budget_scope, remaining_timeout
from research_agent.tools import register_tool, run_tool
from research_agent.core.schemas import RetrievalResult


def test_expired_tool_does_not_execute():
    calls = []
    @register_tool("budget_probe")
    def probe(query):
        calls.append(query)
        return RetrievalResult()
    with budget_scope(Budget({"deadline":time.time()-1})):
        with pytest.raises(BudgetExceeded):
            run_tool("budget_probe", "q")
    assert calls == []


def test_timeout_is_capped_by_remaining_deadline(monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: 1.0)
    with budget_scope(Budget({"deadline":1.2})):
        assert 0 < remaining_timeout(60) <= .2
