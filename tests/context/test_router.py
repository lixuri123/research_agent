"""Compact behavioral coverage of deterministic context routing."""
import pytest
from pydantic import ValidationError

from research_agent.context.router import decide_route
from research_agent.context.schemas import ContextIndex, ContextOptions, ContextRequest


def request(question, **options):
    return ContextRequest(question=question, owner_id="owner", session_id="s", workspace_id="w",
                          submitted_at=100, run_id="new", context=ContextOptions(**options))


def index(**overrides):
    values = dict(turns=[dict(id="turn", status="completed", user="Redis 方案",
                             assistant="Redis 可用于数据库队列。", associated_task_ids=["a"])],
                  tasks=[dict(id="a", title="论文实验", workspace_id="w"),
                         dict(id="b", title="实习准备", workspace_id="w")])
    values.update(overrides)
    return ContextIndex(**values)


def test_options_normalize_and_reject_conflicts():
    assert ContextOptions(task_ids=[" a ", "a"]).task_ids == ["a"]
    for options in [dict(mode="independent", task_ids=["a"]), dict(task_ids=[""]),
                    dict(task_ids=["x" * 256]), dict(task_ids=[str(i) for i in range(11)]),
                    dict(mode="task", task_ids=["a", "b"]), dict(unknown=True)]:
        with pytest.raises(ValidationError):
            ContextOptions(**options)


def test_explicit_modes_and_ids_have_priority():
    assert decide_route(request("继续论文实验", mode="independent"), index()).rule_id == "context-router/v1/R01"
    explicit = decide_route(request("Redis 是什么", mode="followup", turn_ids=["turn"]), index())
    assert (explicit.route, explicit.turn_ids, explicit.rule_id) == ("followup", ("turn",), "context-router/v1/R02")
    targets = decide_route(request("Redis 是什么", task_ids=["a", "b"]), index())
    assert (targets.route, targets.task_ids, targets.rule_id) == ("multi_task", ("a", "b"), "context-router/v1/R03")
    missing = decide_route(request("新问题", mode="task"), index())
    assert missing.needs_clarification and not missing.task_ids


@pytest.mark.parametrize("question", ["Redis 有什么缺点？", "帮我规划学习 Python", "如何让程序继续运行并处理任务队列？"])
def test_self_contained_requests_stay_independent(question):
    result = decide_route(request(question, turn_ids=["turn"]), index())
    assert (result.route, result.turn_ids, result.rule_id) == ("independent", (), "context-router/v1/R09")


@pytest.mark.parametrize("question", ["这个方案有什么缺点？", "你刚才提到 Redis，它有什么缺点？"])
def test_followup_uses_answer_anchor(question):
    result = decide_route(request(question), index())
    assert (result.route, result.turn_ids, result.task_ids) == ("followup", ("turn",), ())
    assert result.rule_id.endswith("R07")


def test_missing_anchor_and_ambiguous_task_do_not_load_candidates():
    missing = decide_route(request("接着说"), index(turns=[]))
    assert missing.needs_clarification and not missing.turn_ids and missing.rule_id.endswith("R08")
    for question in ("继续那个任务", "请再继续这个任务？"):
        unresolved = decide_route(request(question), index(turns=[]))
        assert unresolved.needs_clarification and unresolved.rule_id.endswith("R08")
    ambiguous = decide_route(request("继续论文实验"), index(tasks=[dict(id="a", title="论文实验"), dict(id="b", title="论文实验")]))
    assert ambiguous.needs_clarification and not ambiguous.task_ids
    unbound = decide_route(request("继续上一个任务"), index(turns=[dict(id="t", status="completed", assistant="方案")]))
    assert unbound.needs_clarification and not unbound.task_ids


def test_full_titles_ids_and_comparison_resolve_only_named_targets():
    assert decide_route(request("继续论文实验"), index()).task_ids == ("a",)
    # A longer new title is not an alias for the shorter existing title.
    assert decide_route(request("继续论文实验设计"), index()).route == "independent"
    assert decide_route(request("继续新论文实验"), index()).route == "independent"
    assert decide_route(request("比较论文实验设计和实习准备的优先级"), index()).route == "independent"
    assert decide_route(request("回顾任务 ID: a"), index()).task_ids == ("a",)
    comparison = decide_route(request("比较论文实验和实习准备的优先级"), index())
    assert (comparison.task_ids, comparison.route) == (("a", "b"), "multi_task")
    assert comparison.rule_id.endswith("R06")
    assert decide_route(request("继续上一个任务"), index()).task_ids == ("a",)


def test_active_enumeration_is_bounded_and_empty_is_not_fabricated():
    active = index(active_tasks=[dict(id="a"), dict(id="b")])
    result = decide_route(request("帮我排一下正在做的所有任务"), active)
    assert (result.task_ids, result.rule_id) == (("a", "b"), "context-router/v1/R05")
    overflow = decide_route(request("这个工作空间的进行中任务"), index(active_overflow=True))
    assert overflow.needs_clarification and not overflow.task_ids
    assert decide_route(request("我正在做的所有任务"), index()).task_ids == ()


def test_quotes_scope_and_failed_turns_cannot_supply_triggers():
    assert decide_route(request('解释“继续论文实验”这句话，代码是 `接着说`'), index()).route == "independent"
    other = index(tasks=[dict(id="a", title="论文实验", workspace_id="other")])
    assert decide_route(request("继续论文实验"), other).route == "independent"
    assert decide_route(request("Redis", task_ids=["a"]), other).task_ids == ("a",)
    failed = index(turns=[dict(id="t", status="failed", assistant="错误")])
    assert decide_route(request("接着说"), failed).needs_clarification
