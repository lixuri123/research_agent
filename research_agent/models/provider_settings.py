"""Shared, validated OpenAI-compatible provider overrides (never expose secrets)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ProviderConfigError(ValueError):
    pass


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=255)
    api_key: SecretStr = SecretStr('')
    planner_model: str = Field(default='', max_length=255)
    reviewer_model: str = Field(default='', max_length=255)
    writer_model: str = Field(default='', max_length=255)
    summary_model: str = Field(default='', max_length=255)

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        try:
            url = urlsplit(value)
            _ = url.port  # Reject malformed ports as well as malformed hosts.
        except ValueError:
            raise ValueError('API 地址无效') from None
        if (url.scheme not in ('http', 'https') or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or any(c.isspace() for c in value)):
            raise ValueError('API 地址须为不含凭证、查询参数和片段的 HTTP(S) 地址')
        return value.rstrip('/')

    def overrides(self):
        values = self.model_dump(include={'planner_model', 'reviewer_model', 'writer_model', 'summary_model'})
        values.update(openai_base_url=self.base_url, llm_model=self.model,
                      openai_api_key=self.api_key.get_secret_value())
        return values


_write_lock = threading.RLock()


def read_overrides(path: str) -> dict:
    from research_agent.models.provider_registry import load_registry, find_provider
    state = load_registry(path)
    if state is None or state.active_provider_id is None:
        return {}
    provider = find_provider(state, state.active_provider_id)
    values = provider.overrides()
    # Entry metadata is storage/UI state, never Settings fields.
    return {key: value for key, value in values.items() if key not in ('id', 'revision', 'name', 'models')}


def public_config(settings):
    return dict(base_url=settings.openai_base_url, model=settings.llm_model,
                has_api_key=bool(settings.openai_api_key and settings.openai_api_key != 'sk-xxxxxxxxxxxxxxxx'),
                **{field: getattr(settings, field) for field in
                   ('planner_model', 'reviewer_model', 'writer_model', 'summary_model')})


def with_existing_key(config: ProviderConfig, settings) -> ProviderConfig:
    key = config.api_key.get_secret_value().strip()
    if not key:
        key = settings.openai_api_key
    if key == 'sk-xxxxxxxxxxxxxxxx':
        key = ''
    return config.model_copy(update={'api_key': SecretStr(key)})


def save_provider(config: ProviderConfig, settings):
    from research_agent.models.provider_registry import load_registry, find_provider, update_entry, ProviderInput
    with _write_lock:
        state = load_registry(settings.provider_config_path)
        if state is not None and state.snapshots:
            old = find_provider(state, state.active_provider_id) if state.active_provider_id is not None else None
            if old is None:
                names = list(dict.fromkeys([config.model, config.planner_model,
                           config.reviewer_model, config.writer_model, config.summary_model]))
                draft = ProviderInput(**config.model_dump(), name='默认服务', models=[name for name in names if name])
                update_entry(draft)
                return
            names = list(dict.fromkeys([config.model, *old.models, config.planner_model,
                        config.reviewer_model, config.writer_model, config.summary_model]))
            draft = ProviderInput(**config.model_dump(), name=old.name, models=[name for name in names if name])
            update_entry(draft, old.id)
            return
        values = config.model_dump(exclude={'api_key'})
        values['api_key'] = config.api_key.get_secret_value()
        write_values(values, settings.provider_config_path)


def write_values(values, target):
    path = Path(target)
    temporary = None
    with _write_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.provider-', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(values, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError:
            raise ProviderConfigError('无法保存 Provider 配置文件') from None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
