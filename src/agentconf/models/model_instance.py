# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型绑定：连接 Provider 与能力档案，不拥有连接或能力覆盖值。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentconf.models.base import TimestampMixin, generate_uuid
from agentconf.models.capability_profile import normalize_model_type


class ModelInstanceCreate(BaseModel):
    """创建模型绑定的校验入口。"""

    model_config = ConfigDict(extra="forbid")
    provider_name: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1)
    model_type: str = "llm"
    capabilities_key: str | None = None
    _normalize_type = field_validator("model_type", mode="before")(normalize_model_type)


class ModelInstance(TimestampMixin):
    """数据库中的具名绑定；能力由 resolve_model 单独投影。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=generate_uuid)
    provider_id: str
    name: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1)
    model_type: str = "llm"
    capabilities_key: str | None = None
    _normalize_type = field_validator("model_type", mode="before")(normalize_model_type)
