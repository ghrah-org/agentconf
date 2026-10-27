# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Pydantic 数据模型"""

from agentconf.models.capability_profile import CapabilityProfile
from agentconf.models.base import Modality, ProviderType
from agentconf.models.capabilities import (
    BaseCapabilities,
    EmbeddingCapabilities,
    GenericCapabilities,
    ImageGenCapabilities,
    LLMCapabilities,
    RerankCapabilities,
    STTCapabilities,
    TTSCapabilities,
    get_capabilities_class,
)
from agentconf.models.provider import Provider, ProviderCreate
from agentconf.models.model_instance import ModelInstance, ModelInstanceCreate
from agentconf.models.agent import Agent, AgentCreate
from agentconf.models.resolved import ResolvedModelConfig, ResolvedAgentConfig

__all__ = [
    "CapabilityProfile",
    # 基础类型
    "Modality",
    "ProviderType",
    # Capabilities
    "BaseCapabilities",
    "LLMCapabilities",
    "EmbeddingCapabilities",
    "RerankCapabilities",
    "TTSCapabilities",
    "STTCapabilities",
    "ImageGenCapabilities",
    "GenericCapabilities",
    "get_capabilities_class",
    # Provider
    "Provider",
    "ProviderCreate",
    # Model Instance
    "ModelInstance",
    "ModelInstanceCreate",
    # Agent
    "Agent",
    "AgentCreate",
    # Resolved
    "ResolvedModelConfig",
    "ResolvedAgentConfig",
]
