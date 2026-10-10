"""Provider settings and an explicit, bounded connection probe."""
from typing import Any
from fastapi import APIRouter, Body, HTTPException
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from research_agent.core.config import get_settings
from research_agent.models.provider_settings import (
    ProviderConfig, ProviderConfigError, public_config, save_provider, with_existing_key,
)

router = APIRouter(prefix='/settings/provider', tags=['provider'])


def current_settings():
    try:
        return get_settings()
    except ProviderConfigError as error:
        raise HTTPException(503, str(error)) from None


def parse_config(body):
    try:
        return ProviderConfig.model_validate(body)
    except ValidationError:
        # Default Pydantic errors echo input, which may contain credentials.
        raise HTTPException(422, '请填写有效的 HTTP(S) API 地址和模型名称；角色模型最长 255 字符。') from None


@router.get('')
def read_provider():
    return public_config(current_settings())


@router.put('')
def update_provider(body: Any = Body(...)):
    config = parse_config(body)
    settings = current_settings()
    config = with_existing_key(config, settings)
    try:
        save_provider(config, settings)
    except ProviderConfigError as error:
        raise HTTPException(503, str(error)) from None
    return public_config(current_settings())


@router.post('/test')
def test_provider(body: Any = Body(...)):
    config = with_existing_key(parse_config(body), current_settings())
    return probe(config)


def probe(config):
    client = None
    try:
        client = OpenAI(base_url=config.base_url,
                        api_key=config.api_key.get_secret_value() or 'local-no-key',
                        timeout=15, max_retries=0)
        response = client.chat.completions.create(
            model=config.model, messages=[{'role': 'user', 'content': 'Reply OK.'}],
            max_tokens=16,
        )
        if not response.choices or not response.choices[0].message.content:
            return {'ok': False, 'message': '服务已响应，但未返回文本；请检查模型是否支持聊天补全。'}
        return {'ok': True, 'message': '连接成功，模型已返回有效响应。'}
    except APITimeoutError:
        message = '连接超时，请检查 API 地址和网络。'
    except APIConnectionError:
        message = '无法连接服务，请检查 API 地址、网络或证书。'
    except APIStatusError as error:
        message = {
            401: '认证失败，请检查 API Key。', 403: '权限不足，请检查模型访问权限。',
            404: '接口或模型不存在，请检查 API 地址和模型名称。',
            429: '请求限流或额度不足，请检查服务额度。',
        }.get(error.status_code, f'服务返回 HTTP {error.status_code}，请检查接口配置。')
    except Exception:
        message = '测试失败，请检查服务是否支持 OpenAI 兼容聊天接口。'
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return {'ok': False, 'message': message}


from research_agent.models.provider_registry import (
    ProviderInput, ModelSelection, registry, public_registry, find_provider,
    update_entry, delete_entry, set_default,
)

providers_router = APIRouter(prefix='/settings/providers', tags=['providers'])


def registry_action(operation):
    try:
        return operation()
    except ProviderConfigError as error:
        raise HTTPException(422, str(error)) from None


def parse_model(model, body):
    try:
        return model.model_validate(body)
    except ValidationError:
        raise HTTPException(422, '请填写服务名称、有效 API 地址及模型列表，默认和角色模型须在列表中。') from None


@providers_router.get('')
def list_providers():
    return registry_action(public_registry)


@providers_router.post('')
def create_provider(body: Any = Body(...)):
    config = parse_model(ProviderInput, body)
    return registry_action(lambda: update_entry(config))


@providers_router.put('/default/selection')
def default_provider(body: Any = Body(...)):
    selection = parse_model(ModelSelection, body)
    return registry_action(lambda: set_default(selection))


@providers_router.post('/test')
def test_provider_draft(body: Any = Body(...)):
    if not isinstance(body, dict):
        raise HTTPException(422, 'Provider 配置须为 JSON 对象')
    values = dict(body)
    identifier = values.pop('provider_id', None)
    if identifier is not None and not isinstance(identifier, str):
        raise HTTPException(422, '模型服务 ID 无效')
    config = parse_model(ProviderInput, values)
    if identifier:
        existing = registry_action(lambda: find_provider(registry(), identifier))
        if not config.api_key.get_secret_value().strip():
            config = config.model_copy(update={'api_key': existing.api_key})
    return probe(config)


@providers_router.put('/{identifier}')
def edit_provider(identifier: str, body: Any = Body(...)):
    config = parse_model(ProviderInput, body)
    return registry_action(lambda: update_entry(config, identifier))


@providers_router.delete('/{identifier}')
def remove_provider(identifier: str):
    return registry_action(lambda: delete_entry(identifier))
