# SPDX-License-Identifier: Apache-2.0
"""模型请求标识、开放接口格式与能力引用。"""

from datetime import datetime, timezone
from pydantic import Field, StrictInt, field_validator
from agentconf.models.base import generate_uuid
from agentconf.models.capability_profile import normalize_model_type
from agentconf.models.secure import SafeModel


class EndpointFields(SafeModel):
    name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_type: str = Field(default="llm", min_length=1)
    api_format: str = Field(min_length=1)
    capabilities_key: str | None = None
    _normalize = field_validator("model_type", mode="before")(normalize_model_type)

    @field_validator("name", "model_name", "api_format")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称、请求模型及接口格式不得为空")
        return value


class ModelEndpointCreate(EndpointFields):
    provider_name: str = Field(min_length=1)


class ModelEndpoint(EndpointFields):
    id: str = Field(default_factory=generate_uuid)
    provider_id: str
    revision: StrictInt = Field(default=1, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ModelEndpointUpdate(SafeModel):
    """部分端点修改；实际绑定由 SDK 在事务内完整校验。"""

    name: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    model_type: str | None = None
    api_format: str | None = None
    capabilities_key: str | None = None
