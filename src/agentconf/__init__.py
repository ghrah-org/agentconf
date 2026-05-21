# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agentconf - 多Agent LLM配置管理SDK

使用示例::

    from agentconf import AgentsConfig, ProviderType
    from agentconf.models import LLMCapabilities

    with AgentsConfig() as config:
        # 创建 Provider
        provider = config.create_provider(
            name="openai-main",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-xxxxx",
        )

        # 创建 Model Instance（带能力标记）
        model = config.create_model(
            provider_name="openai-main",
            name="gpt4o",
            model_name="gpt-4o",
            model_type="llm",
            capabilities=LLMCapabilities(
                supports_vision=True,
                supports_function_calling=True,
                max_context_length=128000,
            ),
        )

        # 创建 Agent
        agent = config.create_agent(
            name="code-reviewer",
            model_name="gpt4o",
            temperature=0.3,
            top_p=0.9,
        )

        # 解析完整配置
        resolved = config.resolve_agent("code-reviewer")
        print(resolved.model_dump())
"""

__version__ = "0.2.0"

from agentconf.client import AgentsConfig
from agentconf.models import (
    Agent,
    AgentCreate,
    ModelInstance,
    ModelInstanceCreate,
    Provider,
    ProviderCreate,
    ProviderType,
    Modality,
    BaseCapabilities,
    LLMCapabilities,
    EmbeddingCapabilities,
    RerankCapabilities,
    TTSCapabilities,
    STTCapabilities,
    ImageGenCapabilities,
    GenericCapabilities,
    ResolvedModelConfig,
    ResolvedAgentConfig,
)
from agentconf.exceptions import (
    AgentsConfigError,
    ProviderNotFoundError,
    ProviderAlreadyExistsError,
    ModelNotFoundError,
    ModelAlreadyExistsError,
    AgentNotFoundError,
    AgentAlreadyExistsError,
)

__all__ = [
    # Client
    "AgentsConfig",
    # Models
    "ProviderType",
    "Modality",
    "Provider",
    "ProviderCreate",
    "ModelInstance",
    "ModelInstanceCreate",
    "Agent",
    "AgentCreate",
    # Capabilities
    "BaseCapabilities",
    "LLMCapabilities",
    "EmbeddingCapabilities",
    "RerankCapabilities",
    "TTSCapabilities",
    "STTCapabilities",
    "ImageGenCapabilities",
    "GenericCapabilities",
    # Resolved
    "ResolvedModelConfig",
    "ResolvedAgentConfig",
    # Exceptions
    "AgentsConfigError",
    "ProviderNotFoundError",
    "ProviderAlreadyExistsError",
    "ModelNotFoundError",
    "ModelAlreadyExistsError",
    "AgentNotFoundError",
    "AgentAlreadyExistsError",
]
