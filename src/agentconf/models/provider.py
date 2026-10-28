# SPDX-License-Identifier: Apache-2.0
"""模型服务共享连接；服务商名称开放，凭据只显式解包。"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, StrictInt, field_validator
from agentconf.models.base import generate_uuid
from agentconf.models.secure import SafeModel, SecretJSON


class ProviderCreate(SafeModel):
    name: str = Field(min_length=1)
    provider_type: str = Field(min_length=1)
    base_url: SecretStr
    api_key: SecretStr | None = None
    connection_options: SecretJSON = Field(default_factory=lambda: SecretJSON({}))

    @field_validator("name", "provider_type")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称与类型不得为空")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        try:
            parts = urlsplit(value.get_secret_value())
            if (
                not parts.scheme
                or not parts.hostname
                or any(
                    c.isspace() or ord(c) < 32 or ord(c) == 127
                    for c in value.get_secret_value()
                )
            ):
                raise ValueError
            _ = parts.port
        except ValueError:
            raise ValueError("服务地址必须是合法的绝对 URL") from None
        return value

    @field_validator("connection_options")
    @classmethod
    def connection_only(cls, value):
        forbidden = {
            "model",
            "model_name",
            "base_url",
            "api_key",
            "temperature",
            "max_tokens",
            "top_p",
            "top_k",
            "extra_params",
        }
        if forbidden & value.get_secret_value().keys():
            raise ValueError("连接选项不接受模型身份、连接覆盖或请求采样参数")
        return value


class Provider(ProviderCreate):
    id: str = Field(default_factory=generate_uuid)
    revision: StrictInt = Field(default=1, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderUpdate(SafeModel):
    """部分修改；exclude_unset 区分未提供字段和显式清空。完整连接由 SDK 合并后校验。"""

    name: str | None = None
    provider_type: str | None = None
    base_url: SecretStr | None = None
    api_key: SecretStr | None = None
    connection_options: SecretJSON | None = None
