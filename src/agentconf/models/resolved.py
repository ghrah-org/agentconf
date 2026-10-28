# SPDX-License-Identifier: Apache-2.0
"""两个独立的只读解析契约，不把 MCP 当作可空模型。"""

from pydantic import Field, SecretStr, model_validator
from agentconf.drivers import SDKHint
from agentconf.models.capabilities import Capabilities
from agentconf.models.capability_profile import Origin
from agentconf.models.mcp_server import (
    MCPConnection,
    StdioConnection,
    StreamableHTTPConnection,
    GenericMCPConnection,
)
from agentconf.models.secure import SafeModel, SecretJSON


class ModelConnection(SafeModel):
    provider_id: str
    provider_name: str
    provider_revision: int
    provider_type: str
    api_format: str
    base_url: SecretStr
    api_key: SecretStr | None
    connection_options: SecretJSON
    sdk_hint: SDKHint | None = None


class ModelInfo(SafeModel):
    model_name: str
    model_type: str


class ResolvedMetadata(SafeModel):
    model_key: str
    origin: Origin
    source: str
    revision: int
    derived_from: str | None = None
    base_fingerprint: str | None = None
    stale: bool = False


class ResolvedModelEndpoint(SafeModel):
    endpoint_id: str
    name: str
    revision: int
    connection: ModelConnection
    model: ModelInfo
    metadata: ResolvedMetadata | None = None
    capabilities: Capabilities
    capability_sources: dict[str, str] = Field(default_factory=dict)


class ResolvedMCPServer(SafeModel):
    server_id: str
    name: str
    revision: int
    transport: str
    connection: MCPConnection

    @model_validator(mode="after")
    def transport_matches(self):
        expected = {
            "stdio": StdioConnection,
            "streamable_http": StreamableHTTPConnection,
        }.get(self.transport, GenericMCPConnection)
        if not isinstance(self.connection, expected):
            raise ValueError("MCP transport 与解析连接类型不匹配")
        return self
