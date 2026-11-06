# SPDX-License-Identifier: Apache-2.0
"""独立 MCP 静态连接；校验不会启动进程、联网或读取凭据引用。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit
from pydantic import (
    Field,
    Secret,
    SecretStr,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from agentconf.models.base import generate_uuid
from agentconf.models.mcp_provider import (
    MCPAuth,
    NoAuth,
    ProviderAuth,
    ExternalAuth,
    CredentialBinding,
)
from agentconf.models.secure import SafeModel, SecretJSON

Timeout = Annotated[float, Field(gt=0, strict=True)]


class StdioConnection(SafeModel):
    command: SecretStr
    args: Secret[list[StrictStr]] = Field(default_factory=lambda: Secret([]))
    env: Secret[dict[str, StrictStr]] = Field(default_factory=lambda: Secret({}))
    cwd: SecretStr | None = None
    connect_timeout: Timeout | None = None

    @field_validator("command")
    @classmethod
    def nonblank(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("command 不得为空")
        return value


class StreamableHTTPConnection(SafeModel):
    url: SecretStr
    headers: Secret[dict[str, StrictStr]] = Field(default_factory=lambda: Secret({}))
    connect_timeout: Timeout | None = None
    protocol_version_hint: str | None = None

    @field_validator("url")
    @classmethod
    def http_url(cls, value):
        try:
            parts = urlsplit(value.get_secret_value())
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or any(
                    c.isspace() or ord(c) < 32 or ord(c) == 127
                    for c in value.get_secret_value()
                )
            ):
                raise ValueError
            _ = parts.port
        except ValueError:
            raise ValueError("MCP HTTP 地址必须是合法 HTTP(S) URL") from None
        return value


class GenericMCPConnection(SafeModel):
    options: SecretJSON


MCPConnection = StdioConnection | StreamableHTTPConnection | GenericMCPConnection


class MCPServerCreate(SafeModel):
    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    settings: MCPConnection
    auth: MCPAuth = Field(default_factory=NoAuth)

    @model_validator(mode="before")
    @classmethod
    def dispatch(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        transport, settings = data.get("transport"), data.get("settings")
        target = {
            "stdio": StdioConnection,
            "streamable_http": StreamableHTTPConnection,
        }.get(transport, GenericMCPConnection)
        if isinstance(
            settings, (StdioConnection, StreamableHTTPConnection, GenericMCPConnection)
        ):
            if not isinstance(settings, target):
                raise ValueError("transport 与设置类型不匹配")
        elif isinstance(settings, dict):
            # 已知 transport 的错误直接拒绝，绝不回退到 Generic。
            data["settings"] = target.model_validate(
                {"options": settings} if target is GenericMCPConnection else settings
            )
        else:
            raise ValueError("settings 必须为连接设置对象")
        return data

    @model_validator(mode="after")
    def validate_auth(self):
        binding = (
            self.auth.binding
            if isinstance(self.auth, (ProviderAuth, ExternalAuth))
            else None
        )
        validate_binding(self.transport, self.settings, binding)
        values = (
            self.settings.headers
            if isinstance(self.settings, StreamableHTTPConnection)
            else self.settings.env
            if isinstance(self.settings, StdioConnection)
            else None
        )
        if values and any("{{auth}}" in v for v in values.get_secret_value().values()):
            raise ValueError("凭据模板应放在认证绑定中")
        return self

    @field_validator("name", "transport")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称和 transport 不得为空")
        return value


class MCPServer(MCPServerCreate):
    id: str = Field(default_factory=generate_uuid)
    revision: StrictInt = Field(default=1, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MCPServerUpdate(SafeModel):
    """部分 MCP 修改；settings 是完整设置对象，按最终 transport 严格校验。"""

    name: str | None = None
    transport: str | None = None
    settings: SecretJSON | None = None
    auth: MCPAuth | None = None


def validate_binding(
    transport: str, settings: MCPConnection, binding: CredentialBinding | None
) -> None:
    """验证有效认证绑定的传输类型与字面连接参数冲突。"""
    if binding is None:
        return
    expected = {"http_header": "streamable_http", "stdio_env": "stdio"}[binding.target]
    if transport != expected:
        raise ValueError("认证绑定与 transport 不匹配")
    if isinstance(settings, StreamableHTTPConnection):
        if binding.name.lower() in {
            name.lower() for name in settings.headers.get_secret_value()
        }:
            raise ValueError("认证请求头与连接 headers 冲突")
    elif (
        isinstance(settings, StdioConnection)
        and binding.name in settings.env.get_secret_value()
    ):
        raise ValueError("认证环境变量与连接 env 冲突")
