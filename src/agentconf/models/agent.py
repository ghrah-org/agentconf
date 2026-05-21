# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Agent 数据模型"""

from pydantic import BaseModel, Field

from agentconf.models.base import TimestampMixin, generate_uuid


class AgentCreate(BaseModel):
    """创建 Agent 的输入模型"""
    name: str = Field(..., min_length=1, max_length=255, description="Agent 名称")
    model_name: str = Field(..., min_length=1, description="关联的 Model Instance 名称")
    temperature: float | None = Field(None, ge=0.0, le=2.0, description="温度参数")
    top_p: float | None = Field(None, ge=0.0, le=1.0, description="Top-P 参数")
    top_k: int | None = Field(None, ge=1, description="Top-K 参数")
    max_tokens: int | None = Field(None, ge=1, description="最大输出 token 数")
    extra_params: dict | None = Field(None, description="扩展参数 JSON")


class Agent(TimestampMixin):
    """Agent 完整模型（数据库映射）"""
    id: str = Field(default_factory=generate_uuid, description="UUID 主键")
    name: str = Field(..., min_length=1, max_length=255, description="Agent 名称")
    model_instance_id: str = Field(..., description="外键 → ModelInstance.id")
    temperature: float | None = Field(None, ge=0.0, le=2.0, description="温度参数")
    top_p: float | None = Field(None, ge=0.0, le=1.0, description="Top-P 参数")
    top_k: int | None = Field(None, ge=1, description="Top-K 参数")
    max_tokens: int | None = Field(None, ge=1, description="最大输出 token 数")
    extra_params: dict | None = Field(None, description="扩展参数 JSON")
