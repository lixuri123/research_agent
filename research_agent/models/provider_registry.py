"""Multiple providers and immutable configuration revisions for queued tasks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from research_agent.models.provider_settings import ProviderConfig, ProviderConfigError, _write_lock, write_values


class ProviderInput(ProviderConfig):
    name: str = Field(min_length=1, max_length=100)
    models: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid_models(self):
        self.models = list(dict.fromkeys(value.strip() for value in self.models))
        if any(not value or len(value) > 255 for value in self.models) or self.model not in self.models:
            raise ValueError('默认模型必须在非空模型列表中')
        if any(value and value not in self.models for value in
               (self.planner_model, self.reviewer_model, self.writer_model, self.summary_model)):
            raise ValueError('角色模型必须在模型列表中')
        return self


class ProviderEntry(ProviderInput):
    id: str = Field(min_length=1, max_length=64)
    revision: str = Field(min_length=1, max_length=64)


class ProviderRegistry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[2] = 2
    providers: list[ProviderEntry] = Field(default_factory=list, max_length=30)
    active_provider_id: str | None = None
    snapshots: dict[str, ProviderEntry] = Field(default_factory=dict)

    @model_validator(mode='after')
    def consistent(self):
        ids = [provider.id for provider in self.providers]
        if len(ids) != len(set(ids)) or (self.active_provider_id is not None and self.active_provider_id not in ids):
            raise ValueError('Provider 注册表无效')
        if any(key != entry.revision for key, entry in self.snapshots.items()):
            raise ValueError('Provider 版本无效')
        return self


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)


def _values(config):
    return {**config.model_dump(exclude={'api_key'}), 'api_key': config.api_key.get_secret_value()}


def load_registry(path, base=None):
    try:
        values = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        if base is None:
            return None
        values = dict(base_url=base.openai_base_url, api_key=base.openai_api_key,
                      model=base.llm_model, **{field: getattr(base, field) for field in
                       ('planner_model', 'reviewer_model', 'writer_model', 'summary_model')})
        revision, name = 'environment', '默认服务'
    except (OSError, ValueError):
        raise ProviderConfigError('Provider 配置文件无法读取或无效') from None
    else:
        revision, name = 'legacy', '原有服务'
    try:
        if isinstance(values, dict) and values.get('version') == 2:
            return ProviderRegistry.model_validate(values)
        legacy = ProviderConfig.model_validate(values)
        model_names = [legacy.model, legacy.planner_model, legacy.reviewer_model,
                       legacy.writer_model, legacy.summary_model]
        provider = ProviderEntry(**_values(legacy), name=name,
                                 models=list(dict.fromkeys(name for name in model_names if name)),
                                 id='default', revision=revision)
        return ProviderRegistry(providers=[provider], active_provider_id=provider.id)
    except ValueError:
        raise ProviderConfigError('Provider 配置文件无效') from None


def registry():
    from research_agent.core.config import _environment_settings
    base = _environment_settings()
    return load_registry(base.provider_config_path, base)


def persist(state):
    from research_agent.core.config import _environment_settings
    state = ProviderRegistry.model_validate(state.model_dump())
    values = state.model_dump(exclude={'providers', 'snapshots'})
    values['providers'] = [_values(provider) for provider in state.providers]
    values['snapshots'] = {key: _values(value) for key, value in state.snapshots.items()}
    write_values(values, _environment_settings().provider_config_path)


def archive(state):
    for provider in state.providers:
        state.snapshots.setdefault(provider.revision, provider.model_copy(deep=True))


def find_provider(state, identifier):
    provider = next((item for item in state.providers if item.id == identifier), None)
    if provider is None:
        raise ProviderConfigError('模型服务不存在，请重新选择')
    return provider


def public_entry(provider):
    result = provider.model_dump(exclude={'api_key'})
    key = provider.api_key.get_secret_value()
    result['has_api_key'] = bool(key and key != 'sk-xxxxxxxxxxxxxxxx')
    return result


def public_registry():
    state = registry()
    return {'providers': [public_entry(item) for item in state.providers],
            'active_provider_id': state.active_provider_id}


def update_entry(draft, identifier=None):
    with _write_lock:
        state = registry()
        archive(state)
        existing = find_provider(state, identifier) if identifier else None
        if not existing and len(state.providers) >= 30:
            raise ProviderConfigError('最多配置 30 个模型服务')
        values = _values(draft)
        key = values['api_key'].strip()
        values['api_key'] = key or (existing.api_key.get_secret_value() if existing else '')
        provider = ProviderEntry(**values, id=identifier or uuid4().hex, revision=uuid4().hex)
        state.providers = [provider if item.id == identifier else item for item in state.providers]
        if existing is None:
            state.providers.append(provider)
        if state.active_provider_id is None:
            state.active_provider_id = provider.id
        archive(state)
        persist(state)
        return public_entry(provider)


def delete_entry(identifier):
    with _write_lock:
        state = registry()
        find_provider(state, identifier)
        archive(state)
        state.providers = [item for item in state.providers if item.id != identifier]
        if state.active_provider_id == identifier:
            state.active_provider_id = state.providers[0].id if state.providers else None
        persist(state)
        return {'ok': True}


def set_default(selection):
    with _write_lock:
        state = registry()
        provider = find_provider(state, selection.provider_id)
        if selection.model not in provider.models:
            raise ProviderConfigError('模型不在服务的模型列表中')
        archive(state)
        provider = provider.model_copy(update={'model': selection.model, 'revision': uuid4().hex})
        state.providers = [provider if item.id == provider.id else item for item in state.providers]
        state.active_provider_id = provider.id
        archive(state)
        persist(state)
        return public_registry()


def pin_selection(selection):
    # Legacy API callers without a selection retain their original routing.
    if selection is None:
        return None
    selection = ModelSelection.model_validate(selection)
    with _write_lock:
        state = registry()
        provider = find_provider(state, selection.provider_id)
        if selection.model not in provider.models:
            raise ProviderConfigError('模型不在服务的模型列表中')
        if provider.revision not in state.snapshots:
            archive(state)
            persist(state)
        return {**selection.model_dump(), 'revision': provider.revision}


def selection_overrides(selection):
    if not selection:
        return None
    state = registry()
    provider = state.snapshots.get(selection.get('revision'))
    if provider is None or provider.id != selection.get('provider_id') or selection.get('model') not in provider.models:
        raise ProviderConfigError('任务引用的模型配置版本不可用')
    values = provider.overrides()
    values['llm_model'] = selection['model']
    for field in ('planner_model', 'reviewer_model', 'writer_model', 'summary_model'):
        values[field] = ''  # An explicit task model applies to every task role.
    return values
