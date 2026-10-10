"""LLM 封装层：统一 OpenAI 兼容接口，带超时、重试与结构化输出。"""

from __future__ import annotations

import json
import time
from typing import Any, Optional, Type, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError
from openai import APIConnectionError, APIStatusError

from research_agent.core.config import get_settings
from research_agent.core.budget import BudgetExceeded, current_budget, remaining_timeout
from research_agent.core.observability import observed, trace_span, finish_trace
from research_agent.context.budget import estimate_messages, current_input_limit, ContextCapacityError

T = TypeVar("T", bound=BaseModel)


class ModelOutputError(ValueError):
    """模型返回的文本或结构不满足调用契约。"""


class ModelGateway:
    """基于 langchain-openai 的轻量封装，所有 Agent 共用。"""

    def __init__(self, model: str | None = None, *, transport=None, settings=None) -> None:
        s = self.settings = settings or get_settings()
        self._llm = transport if transport is not None else ChatOpenAI(
            model=model or s.llm_model,
            api_key=s.openai_api_key or 'local-no-key',
            base_url=s.openai_base_url,
            temperature=s.llm_temperature,
            timeout=s.request_timeout,
            max_retries=0,
            max_tokens=s.max_output_tokens,
        )

    @observed('model_text', inputs=lambda self, messages, **kw: {
        'messages': [{'role': message.type, 'content': message.content} for message in messages]})
    def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> str:
        """普通文本补全，返回字符串。"""
        return self._call(messages, self._text, **kwargs)

    @staticmethod
    def _text(response):
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise ModelOutputError('模型未返回有效文本')
        return content

    def _call(self, messages, validate, *, output_contract='text', **kwargs):
        s = self.settings
        output_tokens = kwargs.pop('max_tokens', s.max_output_tokens)
        if type(output_tokens) is not int or not 0 < output_tokens <= s.max_output_tokens:
            raise ValueError('max_tokens 必须为正整数且不能超过配置上限')
        if any(key in kwargs for key in ('max_completion_tokens', 'max_output_tokens', 'stream')):
            raise ValueError('请通过 max_tokens 设置非流式输出预算')
        timeout = kwargs.pop('timeout', s.request_timeout)
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= s.request_timeout:
            raise ValueError('timeout 必须为正数且不能超过配置上限')
        budget = current_budget.get()
        # Conservative byte bound avoids model-specific tokenizer downloads.
        messages = list(messages)
        network_failures, repairs, attempt = 0, 0, 0
        while True:
            attempt += 1
            input_tokens = estimate_messages(messages)
            input_limit = current_input_limit.get()
            if input_limit is not None and input_tokens > input_limit:
                raise ContextCapacityError('模型请求（含格式修复消息）超过上下文容量，未继续发送')
            if budget:
                budget.reserve(input_tokens, output_tokens)
            try:
                resp = self._llm.invoke(messages, timeout=remaining_timeout(timeout), max_tokens=output_tokens, **kwargs)
            except Exception as exc:
                retryable = isinstance(exc, (APIConnectionError, TimeoutError, ConnectionError)) or (
                    isinstance(exc, APIStatusError) and (exc.status_code in (408, 429) or exc.status_code >= 500))
                network_failures += 1
                if not retryable or network_failures >= s.llm_max_attempts:
                    raise
            else:
                if budget:
                    usage = getattr(resp, 'usage_metadata', None) or {}
                    actual = usage.get('total_tokens')
                    if type(actual) is not int or actual < 0:
                        actual = None
                    budget.settle(input_tokens + output_tokens, actual)
                try:
                    with trace_span('output_validation', inputs={'attempt': attempt, 'contract': output_contract}) as validation:
                        value = validate(resp)
                        finish_trace(validation, {'valid': True})
                    return value
                except (ModelOutputError, ValidationError, json.JSONDecodeError) as exc:
                    if repairs >= s.llm_max_format_repairs:
                        raise
                    repairs += 1
                    instruction = ('只返回一个完整合法且符合原有 Schema 的 JSON 对象。禁止 Markdown、解释文字、DSML 或其他工具调用标记；工具请求必须通过原有 JSON 字段表达。'
                                   if output_contract == 'json' else '请按原有要求返回非空的实际文本内容。')
                    messages.append(self.human(f'输出校验失败（{type(exc).__name__}）。{instruction}请重新输出。'))
                    try:
                        from langsmith.run_helpers import get_current_run_tree
                        run = get_current_run_tree()
                        if run:
                            run.add_metadata({'output_format_repairs': repairs})
                    except Exception:
                        pass
                    continue
            delay = min(s.llm_retry_delay * 2 ** (network_failures - 1), 30)
            if budget:
                budget.check()
                if time.time() + delay >= budget.limits.get('deadline', float('inf')):
                    raise BudgetExceeded('重试等待超过任务时间预算')
            time.sleep(delay)

    @observed('model_structured', inputs=lambda self, messages, schema: {
        'messages': [{'role': message.type, 'content': message.content} for message in messages],
        'schema': schema.__name__}, outputs=lambda result, *a, **kw: {'result': result.model_dump()})
    def invoke_structured(
        self,
        messages: list[BaseMessage],
        schema: Type[T],
    ) -> T:
        """使用结构化输出约束，返回指定 Pydantic 模型。"""
        def validate(response):
            raw = self._text(response).strip()
            if raw.startswith('```') and raw.endswith('```'):
                raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            return schema.model_validate(json.loads(raw))

        options = {}
        if self.settings.llm_structured_output_mode == 'json_object':
            options['response_format'] = {'type': 'json_object'}
        instruction = structured_instruction(schema)
        return self._call(messages + [self.human(instruction)], validate, output_contract='json', **options)

    @staticmethod
    def system(content: str) -> SystemMessage:
        return SystemMessage(content=content)

    @staticmethod
    def human(content: str) -> HumanMessage:
        return HumanMessage(content=content)


# Existing agents retain their public import and constructor contract.
LLMClient = ModelGateway


def structured_instruction(schema):
    return '只输出一个符合此 JSON Schema 的 JSON 对象，不附加 Markdown、解释或 DSML 工具标记：' + json.dumps(schema.model_json_schema(), ensure_ascii=False)


def parse_json_block(raw: str) -> Any:
    """尽力从 LLM 输出中提取 JSON（兼容 ```json 代码块）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}
