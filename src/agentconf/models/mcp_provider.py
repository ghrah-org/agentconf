# SPDX-License-Identifier: Apache-2.0
"""独立 MCP 认证身份与无密钥的认证绑定。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field, SecretStr, StrictInt, field_validator, model_validator
from agentconf.models.base import generate_uuid
from agentconf.models.secure import SafeModel

AUTH_PLACEHOLDER = "{{auth}}"


class CredentialBinding(SafeModel):
    """共享 Key 的放置位置和格式，不包含 Key 本身。"""

    target: Literal["http_header", "stdio_env"]
    name: str
    value_template: str

    @model_validator(mode="after")
    def valid_binding(self):
        pattern = (
            r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
            if self.target == "http_header"
            else r"[A-Za-z_][A-Za-z0-9_]*"
        )
        if not re.fullmatch(pattern, self.name):
            raise ValueError("认证目标名称格式无效")
        remaining = self.value_template.replace(AUTH_PLACEHOLDER, "")
        if (
            self.value_template.count(AUTH_PLACEHOLDER) != 1
            or any(token in remaining for token in ("{{", "}}", "${"))
            or any(ord(c) < 32 or ord(c) == 127 for c in self.value_template)
        ):
            raise ValueError(
                "认证模板必须恰含一个 {{auth}}，且不得含其他模板或控制字符"
            )
        return self


class NoAuth(SafeModel):
    mode: Literal["none"] = "none"


class InlineAuth(SafeModel):
    mode: Literal["inline"] = "inline"


class ProviderAuth(SafeModel):
    mode: Literal["provider"] = "provider"
    provider_id: str = Field(min_length=1)
    binding_mode: Literal["inherit", "override"]
    binding: CredentialBinding | None = None

    @field_validator("provider_id")
    @classmethod
    def stable_id(cls, value):
        try:
            return str(UUID(value))
        except ValueError:
            raise ValueError("MCP Provider 引用必须为稳定 UUID") from None

    @model_validator(mode="after")
    def explicit_override(self):
        if (self.binding_mode == "override") != (self.binding is not None):
            raise ValueError("仅 override 模式必须填写完整认证绑定")
        return self


class ExternalAuth(SafeModel):
    mode: Literal["external"] = "external"
    reference: SecretStr
    binding: CredentialBinding

    @field_validator("reference")
    @classmethod
    def nonblank(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("外部凭据引用不得为空")
        return value


MCPAuth = Annotated[
    NoAuth | InlineAuth | ProviderAuth | ExternalAuth, Field(discriminator="mode")
]


class MCPProviderCreate(SafeModel):
    name: str = Field(min_length=1)
    vendor: str | None = None
    api_key: SecretStr | None = None
    default_binding: CredentialBinding | None = None

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不得为空")
        return value


class MCPProvider(MCPProviderCreate):
    id: str = Field(default_factory=generate_uuid)
    revision: StrictInt = Field(default=1, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("id")
    @classmethod
    def stable_id(cls, value):
        try:
            return str(UUID(value))
        except ValueError:
            raise ValueError("MCP Provider ID 必须为 UUID") from None


class MCPProviderUpdate(SafeModel):
    name: str | None = None
    vendor: str | None = None
    api_key: SecretStr | None = None
    default_binding: CredentialBinding | None = None


class ResolvedMCPAuth(SafeModel):
    """连接解析只返回此描述，不加载 Provider Key。"""

    mode: Literal["none", "inline", "provider", "external"]
    provider_id: str | None = None
    provider_name: str | None = None
    provider_revision: int | None = None
    key_configured: bool = False
    binding_mode: Literal["inherit", "override"] | None = None
    binding: CredentialBinding | None = None
    reference: SecretStr | None = None
