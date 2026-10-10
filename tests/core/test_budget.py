import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import pytest
from research_agent.core.budget import Budget, BudgetExceeded


def test_call_budget_survives_reopen():
    ledger = {}
    save = lambda value: ledger.update(value)
    b = Budget({"max_calls": 1, "max_tokens": 10000}, save=save)
    b.reserve(10, 20)
    with pytest.raises(BudgetExceeded):
        Budget({"max_calls": 1, "max_tokens": 10000}, usage=ledger).reserve(10, 20)


def test_deadline_blocks_call():
    with pytest.raises(BudgetExceeded):
        Budget({"deadline": time.time() - 1}).reserve(1, 1)


def test_token_reservation_never_overruns():
    b = Budget({"max_tokens": 100})
    with pytest.raises(BudgetExceeded):
        b.reserve(50, 51)


def test_concurrent_reservations_serialize_saves_and_keep_limit():
    workers = 8
    start = Barrier(workers)
    save_lock = Lock()
    active_saves = 0
    peak_saves = 0
    saved = []

    def save(value):
        nonlocal active_saves, peak_saves
        with save_lock:
            active_saves += 1
            peak_saves = max(peak_saves, active_saves)
        time.sleep(.02)
        saved.append(value)
        with save_lock:
            active_saves -= 1

    budget = Budget({"max_calls": 4, "max_tokens": 40}, save=save)

    def reserve():
        start.wait(timeout=5)
        try:
            budget.reserve(5, 5)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: reserve(), range(workers)))

    assert results.count(True) == 4
    assert peak_saves == 1
    assert saved == [
        {"calls": calls, "tokens": calls * 10} for calls in range(1, 5)
    ]
    assert budget.usage == {"calls": 4, "tokens": 40}


@pytest.mark.parametrize("operation", ["reserve", "settle"])
def test_failed_save_does_not_publish_usage(operation):
    ledger = {"calls": 1, "tokens": 20}

    def fail_save(value):
        assert budget.usage == ledger
        raise OSError("storage unavailable")

    budget = Budget({"max_calls": 2, "max_tokens": 100}, usage=ledger, save=fail_save)
    with pytest.raises(OSError, match="storage unavailable"):
        if operation == "reserve":
            budget.reserve(5, 5)
        else:
            budget.settle(10, 4)
    assert budget.usage == ledger
