# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""解析结果模型 - 配置继承解析后的最终结果"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentconf.models.base import ProviderType
from agentconf.models.capabilities import Capabilities


class ResolvedModelConfig(BaseModel):
    """Model 配置的最终解析结果，所有继承已处理"""
    provider_name: str = Field(..., description="提供商名称")
    provider_type: ProviderType = Field(..., description="提供商类型")
    base_url: str = Field(..., description="API 基础 URL（已解析覆盖）")
    api_key: str = Field(..., description="API Key（已解密）")
    model_name: str = Field(..., description="模型标识")
    model_type: str = Field(default="llm", description="模型类型")
    capabilities: Capabilities | None = Field(None, description="模型能力标记")


class ResolvedAgentConfig(BaseModel):
    """Agent 配置的最终解析结果"""
    agent_name: str = Field(..., description="Agent 名称")
    model: ResolvedModelConfig = Field(..., description="解析后的 Model 配置")
    temperature: float | None = Field(None, description="温度参数")
    top_p: float | None = Field(None, description="Top-P 参数")
    top_k: int | None = Field(None, description="Top-K 参数")
    max_tokens: int | None = Field(None, description="最大输出 token 数")
    extra_params: dict | None = Field(None, description="扩展参数")
