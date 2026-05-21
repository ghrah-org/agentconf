# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ModelInstance 数据模型 — 统一的模型实例定义"""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr

from agentconf.models.base import TimestampMixin, generate_uuid
from agentconf.models.capabilities import Capabilities


class ModelInstanceCreate(BaseModel):
    """创建 ModelInstance 的输入模型"""
    provider_name: str = Field(..., min_length=1, description="关联的 Provider 名称")
    name: str = Field(..., min_length=1, max_length=255, description="实例名称")
    model_name: str = Field(..., min_length=1, description="模型标识，如 gpt-4o")
    model_type: str = Field(default="llm", description="模型类型：llm / embedding / rerank / tts / stt / asr / image_gen 等")
    base_url: str | None = Field(None, description="覆盖 Provider 的 base_url")
    api_key: SecretStr | None = Field(None, description="覆盖 Provider 的 api_key")
    capabilities: Capabilities | None = Field(None, description="模型能力标记")


class ModelInstance(TimestampMixin):
    """ModelInstance 完整模型（数据库映射）"""
    id: str = Field(default_factory=generate_uuid, description="UUID 主键")
    provider_id: str = Field(..., description="外键 → Provider.id")
    name: str = Field(..., min_length=1, max_length=255, description="实例名称")
    model_name: str = Field(..., min_length=1, description="模型标识")
    model_type: str = Field(default="llm", description="模型类型")
    base_url: str | None = Field(None, description="覆盖 Provider 的 base_url")
    api_key: SecretStr | None = Field(None, description="覆盖 Provider 的 api_key")
    capabilities: Capabilities | None = Field(None, description="模型能力标记")
