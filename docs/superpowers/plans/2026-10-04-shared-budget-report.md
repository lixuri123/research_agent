# Shared budget implementation report

Task 1 of `2026-10-04-dynamic-agents.md` is implemented in `research_agent/budget.py` and `tests/test_budget.py`.

- Added a concurrent reservation test that checks save callbacks are serialized, persisted snapshots advance in order, and only four of eight calls can consume a four-call / 40-token budget.
- Added reserve and settle save-failure tests that check published in-memory usage remains unchanged, including while the save callback runs.
- `Budget` now uses an `RLock` around reservation, settlement, and saving. It saves a candidate usage copy before publishing that copy to `usage`.

Red run: `.venv/Scripts/python.exe -m pytest tests/test_budget.py -q --basetemp .pytest-shared-budget-red` failed as expected with 3 failures and 3 passes. Four save callbacks overlapped; reserve and settle both exposed changed usage before a failed save.

Green run: `.venv/Scripts/python.exe -m pytest tests/test_budget.py tests/test_model_gateway.py -q --basetemp .pytest-shared-budget` passed, 10 tests in 1.44 seconds.
