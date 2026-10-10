from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from research_agent.core.budget import Budget, BudgetExceeded, budget_scope
from research_agent.core.config import Settings
from research_agent.models.gateway import ModelGateway, ModelOutputError


class Transport:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []
        self.messages = []

    def invoke(self, messages, **kwargs):
        self.calls.append(kwargs)
        self.messages.append(list(messages))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(content=result, usage_metadata={'total_tokens': 5})


def gateway(transport):
    return ModelGateway(transport=transport, settings=Settings(_env_file=None, llm_retry_delay=0))


def test_retry_accounts_for_each_attempt_and_settles_success():
    transport = Transport(TimeoutError(), 'ok')
    budget = Budget({'max_calls': 3, 'max_tokens': 10000})
    with budget_scope(budget):
        assert gateway(transport).invoke([ModelGateway.human('hello')], max_tokens=20) == 'ok'
    assert budget.usage == {'calls': 2, 'tokens': 62}
    assert len(transport.calls) == 2
    assert transport.calls[0]['max_tokens'] == 20


def test_schema_failure_retries_within_same_budget():
    class Answer(BaseModel):
        count: int

    transport = Transport('{"count":"invalid"}', '{"count":2}')
    budget = Budget({'max_calls': 3, 'max_tokens': 20000})
    with budget_scope(budget):
        assert gateway(transport).invoke_structured([], Answer).count == 2
    assert budget.usage == {'calls': 2, 'tokens': 10}


def test_budget_exhaustion_prevents_retry():
    transport = Transport(TimeoutError(), 'ok')
    with budget_scope(Budget({'max_calls': 1})):
        with pytest.raises(BudgetExceeded):
            gateway(transport).invoke([])
    assert len(transport.calls) == 1


def test_invalid_output_has_bounded_retries_and_configuration_errors_do_not_retry():
    transport = Transport('', '', '')
    with pytest.raises(ModelOutputError):
        gateway(transport).invoke([])
    assert len(transport.calls) == 2
    transport = Transport(ValueError('invalid configuration'))
    with pytest.raises(ValueError):
        gateway(transport).invoke([])
    assert len(transport.calls) == 1


def test_mixed_protocol_failure_gets_explicit_feedback_once():
    class Answer(BaseModel):
        count: int
    transport = Transport('{"count":1}\n<｜｜DSML｜｜ calls>private</｜｜DSML｜｜ calls>', '{"count":2}')
    assert gateway(transport).invoke_structured([], Answer).count == 2
    assert transport.messages[0] != transport.messages[1]
    feedback = transport.messages[1][-1].content
    assert 'JSONDecodeError' in feedback
    assert 'DSML' in feedback
    assert 'private' not in feedback


def test_format_repair_limit_is_separate_from_network_retries():
    class Answer(BaseModel):
        count: int
    transport = Transport(TimeoutError(), 'invalid', 'still invalid', '{"count":2}')
    with pytest.raises(__import__('json').JSONDecodeError):
        gateway(transport).invoke_structured([], Answer)
    assert len(transport.calls) == 3
    assert transport.messages[0] == transport.messages[1]
    assert transport.messages[1] != transport.messages[2]


@pytest.mark.parametrize('mode,expected', [('prompt', None), ('json_object', {'type': 'json_object'})])
def test_structured_mode_is_explicit_and_plain_calls_remain_text(mode, expected):
    class Answer(BaseModel):
        count: int
    transport = Transport('{"count":2}', 'hello')
    client = ModelGateway(transport=transport, settings=Settings(_env_file=None,
        llm_structured_output_mode=mode, llm_retry_delay=0))
    assert client.invoke_structured([], Answer).count == 2
    assert transport.calls[0].get('response_format') == expected
    assert client.invoke([]) == 'hello'
    assert 'response_format' not in transport.calls[1]


def test_repair_feedback_is_reserved_in_next_input_budget():
    class Answer(BaseModel):
        count: int
    reserved = []
    class RecordingBudget(Budget):
        def reserve(self, input_tokens, output_tokens):
            reserved.append(input_tokens)
            return super().reserve(input_tokens, output_tokens)
    transport = Transport('invalid', '{"count":2}')
    with budget_scope(RecordingBudget({'max_calls': 5, 'max_tokens': 50000})):
        assert gateway(transport).invoke_structured([], Answer).count == 2
    assert reserved[1] > reserved[0]
