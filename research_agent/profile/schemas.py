"""Fixed provenance envelope around open-ended attribute names and values."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class SourceReference(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=255)


class AttributeDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: list[str] = Field(min_length=1, max_length=12)
    value: JsonValue
    basis: Literal['user_stated', 'user_confirmed', 'inferred'] = 'user_stated'
    source_refs: list[SourceReference] = Field(min_length=1, max_length=20)
    scope: Literal['person', 'project', 'session'] = 'person'
    scope_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator('path')
    @classmethod
    def valid_path(cls, values):
        values = [part.strip() for part in values]
        if any(not part or len(part) > 120 or any(ord(c) < 32 for c in part) for part in values):
            raise ValueError('属性路径的每一层须为 1–120 个可见字符')
        return values

    @field_validator('value')
    @classmethod
    def bounded_json(cls, value):
        def depth(item, level=0):
            if level > 12:
                raise ValueError('属性值层次不能超过 12 层')
            if isinstance(item, dict):
                for nested in item.values():
                    depth(nested, level + 1)
            elif isinstance(item, list):
                for nested in item:
                    depth(nested, level + 1)
        depth(value)
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 32768:
            raise ValueError('单个属性值不能超过 32 KiB')
        return value

    @field_validator('scope_id')
    @classmethod
    def trim_scope_id(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError('范围 ID 不能为空')
        return value

    @model_validator(mode='after')
    def scoped(self):
        if (self.scope == 'person') != (self.scope_id is None):
            raise ValueError('个人属性不填范围 ID；项目和会话属性须填写范围 ID')
        return self


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)


class AttributeUpdate(AttributeDraft):
    expected_version: int = Field(ge=1)
