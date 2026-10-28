# SPDX-License-Identifier: Apache-2.0
"""静态连接的安全展示、显式导出与掩码编辑。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    Secret,
    SecretStr,
    model_serializer,
)

MASK = "**********"
SecretJSON = Secret[dict[str, JsonValue]]


def reveal(value: Any) -> Any:
    """显式读取原始值，仅用于仓储或调用者主动请求的敏感导出。"""
    if isinstance(value, (Secret, SecretStr)):
        return reveal(value.get_secret_value())
    if isinstance(value, BaseModel):
        return {key: reveal(getattr(value, key)) for key in type(value).model_fields}
    if isinstance(value, dict):
        return {key: reveal(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [reveal(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


class SafeModel(BaseModel):
    """严格字段；默认序列化隐藏秘密，显式 context 才导出。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )

    @model_serializer(mode="wrap")
    def serialize(self, handler, info):
        data = handler(self)
        show = info.context and info.context.get("show_secrets")
        for key in type(self).model_fields:
            if key in data and isinstance(getattr(self, key), (Secret, SecretStr)):
                data[key] = reveal(getattr(self, key)) if show else MASK
        return data


def merge_masked(payload: dict, raw: dict, safe: dict) -> dict:
    """掩码表示保留旧秘密；None 表示清空，实际新值表示替换。"""
    result = {}
    for key, value in payload.items():
        if value == MASK:
            if safe.get(key) != MASK or key not in raw:
                raise ValueError("掩码只能保留已有敏感字段")
            result[key] = raw[key]
        elif (
            isinstance(value, dict)
            and isinstance(raw.get(key), dict)
            and isinstance(safe.get(key), dict)
        ):
            result[key] = merge_masked(value, raw[key], safe[key])
        else:
            if contains_mask(value):
                raise ValueError("新值中的嵌套掩码不能作为真实配置写入")
            result[key] = value
    return result


def contains_mask(value):
    if isinstance(value, str):
        return value == MASK
    if isinstance(value, dict):
        return any(contains_mask(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_mask(v) for v in value)
    return False
