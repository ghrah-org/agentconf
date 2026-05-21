# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Provider 数据模型"""

from pydantic import BaseModel, Field, SecretStr

from agentconf.models.base import ProviderType, TimestampMixin, generate_uuid


class ProviderCreate(BaseModel):
    """创建 Provider 的输入模型"""
    name: str = Field(..., min_length=1, max_length=255, description="提供商名称")
    provider_type: ProviderType = Field(..., description="提供商类型")
    base_url: str = Field(..., min_length=1, description="API 基础 URL")
    api_key: SecretStr = Field(..., description="API Key")


class Provider(TimestampMixin):
    """Provider 完整模型（数据库映射）"""
    id: str = Field(default_factory=generate_uuid, description="UUID 主键")
    name: str = Field(..., min_length=1, max_length=255, description="提供商名称")
    provider_type: ProviderType = Field(..., description="提供商类型")
    base_url: str = Field(..., min_length=1, description="API 基础 URL")
    api_key: SecretStr = Field(..., description="API Key")
