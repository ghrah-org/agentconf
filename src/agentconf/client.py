# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agentconf 主客户端 - 统一API入口"""

from __future__ import annotations

from pathlib import Path

from agentconf.models import (
    Agent,
    ModelInstance,
    Provider,
    ProviderType,
    ResolvedAgentConfig,
    ResolvedModelConfig,
)
from agentconf.models.base import generate_uuid
from agentconf.models.capabilities import (
    BaseCapabilities,
    get_capabilities_class,
)
from agentconf.storage import (
    AgentRepository,
    Database,
    ModelRepository,
    ProviderRepository,
)


class AgentsConfig:
    """多Agent LLM配置管理SDK主客户端

    使用示例::

        config = AgentsConfig()
        provider = config.create_provider(
            name="openai-main",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-xxxxx",
        )
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
        agent = config.create_agent(
            name="code-reviewer",
            model_name="gpt4o",
            temperature=0.3,
        )
        resolved = config.resolve_agent("code-reviewer")
    """

    DEFAULT_DB_PATH = "~/.agentconf/config.db"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path or self.DEFAULT_DB_PATH)
        self._db = Database(self._db_path)

        conn = self._db.conn
        self._provider_repo = ProviderRepository(conn)
        self._model_repo = ModelRepository(conn)
        self._agent_repo = AgentRepository(conn)

    def close(self) -> None:
        """关闭数据库连接"""
        self._db.close()

    def __enter__(self) -> "AgentsConfig":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ─── Provider 操作 ───────────────────────────────────

    def create_provider(
        self,
        name: str,
        provider_type: ProviderType | str,
        base_url: str,
        api_key: str,
    ) -> Provider:
        """创建 Provider

        Args:
            name: 提供商名称（唯一）
            provider_type: 提供商类型
            base_url: API 基础 URL
            api_key: API Key（明文存储）

        Returns:
            Provider 实例
        """
        provider_type = ProviderType(provider_type)

        return self._provider_repo.create(
            id=generate_uuid(),
            name=name,
            provider_type=provider_type.value,
            base_url=base_url,
            api_key=api_key,
        )

    def get_provider(self, name: str) -> Provider:
        """获取 Provider"""
        return self._provider_repo.get_by_name(name)

    def get_provider_by_id(self, provider_id: str) -> Provider:
        """通过 ID 获取 Provider"""
        return self._provider_repo.get_by_id(provider_id)

    def list_providers(self) -> list[Provider]:
        """列出所有 Provider"""
        return self._provider_repo.list_all()

    def update_provider(self, name: str, **kwargs) -> Provider:
        """更新 Provider

        可更新字段: base_url, api_key, provider_type
        """
        return self._provider_repo.update(name, **kwargs)

    def delete_provider(self, name: str) -> None:
        """删除 Provider（级联删除关联的 Model Instance 和 Agent）"""
        self._provider_repo.delete(name)

    # ─── Model Instance 操作 ───────────────────────────────

    def create_model(
        self,
        provider_name: str,
        name: str,
        model_name: str,
        model_type: str = "llm",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: BaseCapabilities | dict | None = None,
    ) -> ModelInstance:
        """创建 Model Instance

        Args:
            provider_name: 关联的 Provider 名称
            name: 实例名称
            model_name: 模型标识
            model_type: 模型类型（llm / embedding / rerank / tts / stt / asr / image_gen）
            base_url: 可选，覆盖 Provider 的 base_url
            api_key: 可选，覆盖 Provider 的 api_key
            capabilities: 可选，模型能力标记（ModelCapabilities 对象或 dict）

        Returns:
            ModelInstance 实例
        """
        provider = self._provider_repo.get_by_name(provider_name)

        # 处理 capabilities：如果是 dict，则根据 model_type 反序列化
        caps = self._resolve_capabilities(capabilities, model_type)

        return self._model_repo.create(
            id=generate_uuid(),
            provider_id=provider.id,
            name=name,
            model_name=model_name,
            model_type=model_type,
            base_url=base_url,
            api_key=api_key,
            capabilities=caps,
        )

    def get_model(self, name: str) -> ModelInstance:
        """获取 Model Instance"""
        return self._model_repo.get_by_name(name)

    def get_model_by_id(self, model_id: str) -> ModelInstance:
        """通过 ID 获取 Model Instance"""
        return self._model_repo.get_by_id(model_id)

    def list_models(
        self,
        provider_name: str | None = None,
        model_type: str | None = None,
    ) -> list[ModelInstance]:
        """列出 Model Instance

        Args:
            provider_name: 可选，筛选指定 Provider 下的实例
            model_type: 可选，筛选指定模型类型
        """
        if provider_name:
            provider = self._provider_repo.get_by_name(provider_name)
            models = self._model_repo.list_by_provider(provider.id)
        else:
            models = self._model_repo.list_all()

        if model_type:
            models = [m for m in models if m.model_type == model_type]

        return models

    def find_models_by_capability(self, capability_filter: str) -> list[ModelInstance]:
        """按能力筛选 Model Instance

        Args:
            capability_filter: 能力筛选条件
                - "supports_vision" — 支持视觉理解
                - "supports_function_calling" — 支持函数调用
                - "supports_streaming" — 支持流式输出
                - "input_modality:image" — 输入包含图像模态
                - "output_modality:audio" — 输出包含音频模态
        """
        return self._model_repo.list_by_capability(capability_filter)

    def update_model(self, name: str, **kwargs) -> ModelInstance:
        """更新 Model Instance

        可更新字段: model_name, model_type, base_url, api_key, capabilities
        """
        # 处理 capabilities
        if "capabilities" in kwargs and isinstance(kwargs["capabilities"], dict):
            model_type = kwargs.get("model_type", "llm")
            kwargs["capabilities"] = self._resolve_capabilities(
                kwargs["capabilities"], model_type
            )
        return self._model_repo.update(name, **kwargs)

    def delete_model(self, name: str) -> None:
        """删除 Model Instance（级联删除关联的 Agent）"""
        self._model_repo.delete(name)

    # ─── Agent 操作 ──────────────────────────────────────

    def create_agent(
        self,
        name: str,
        model_name: str,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        extra_params: dict | None = None,
    ) -> Agent:
        """创建 Agent

        Args:
            name: Agent 名称（唯一）
            model_name: 关联的 Model Instance 名称
            temperature: 温度参数
            top_p: Top-P 参数
            top_k: Top-K 参数
            max_tokens: 最大输出 token 数
            extra_params: 扩展参数

        Returns:
            Agent 实例
        """
        model = self._model_repo.get_by_name(model_name)

        return self._agent_repo.create(
            id=generate_uuid(),
            name=name,
            model_instance_id=model.id,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            extra_params=extra_params,
        )

    def get_agent(self, name: str) -> Agent:
        """获取 Agent"""
        return self._agent_repo.get_by_name(name)

    def get_agent_by_id(self, agent_id: str) -> Agent:
        """通过 ID 获取 Agent"""
        return self._agent_repo.get_by_id(agent_id)

    def list_agents(self) -> list[Agent]:
        """列出所有 Agent"""
        return self._agent_repo.list_all()

    def update_agent(self, name: str, **kwargs) -> Agent:
        """更新 Agent

        可更新字段: model_name, temperature, top_p, top_k, max_tokens, extra_params
        """
        if "model_name" in kwargs:
            model = self._model_repo.get_by_name(kwargs.pop("model_name"))
            kwargs["model_instance_id"] = model.id

        return self._agent_repo.update(name, **kwargs)

    def delete_agent(self, name: str) -> None:
        """删除 Agent"""
        self._agent_repo.delete(name)

    # ─── 配置解析 ────────────────────────────────────────

    def resolve_model(self, name: str) -> ResolvedModelConfig:
        """解析 Model 配置（处理继承和覆盖）

        Returns:
            ResolvedModelConfig 包含完整的 Model 配置
        """
        model = self._model_repo.get_by_name(name)
        provider = self._provider_repo.get_by_id(model.provider_id)

        # base_url: Model 覆盖 > Provider
        base_url = model.base_url or provider.base_url

        # api_key: Model 覆盖 > Provider
        api_key_secret = model.api_key or provider.api_key
        api_key = api_key_secret.get_secret_value()

        return ResolvedModelConfig(
            provider_name=provider.name,
            provider_type=provider.provider_type,
            base_url=base_url,
            api_key=api_key,
            model_name=model.model_name,
            model_type=model.model_type,
            capabilities=model.capabilities,
        )

    def resolve_agent(self, name: str) -> ResolvedAgentConfig:
        """解析 Agent 配置（完整继承链）

        Returns:
            ResolvedAgentConfig 包含完整的 Agent 配置
        """
        agent = self._agent_repo.get_by_name(name)
        resolved_model = self.resolve_model(
            self._model_repo.get_by_id(agent.model_instance_id).name
        )

        return ResolvedAgentConfig(
            agent_name=agent.name,
            model=resolved_model,
            temperature=agent.temperature,
            top_p=agent.top_p,
            top_k=agent.top_k,
            max_tokens=agent.max_tokens,
            extra_params=agent.extra_params,
        )

    # ─── 工具方法 ────────────────────────────────────────

    @staticmethod
    def _resolve_capabilities(
        capabilities: BaseCapabilities | dict | None,
        model_type: str,
    ) -> BaseCapabilities | None:
        """将 capabilities 参数解析为正确的 BaseCapabilities 子类实例"""
        if capabilities is None:
            return None
        if isinstance(capabilities, BaseCapabilities):
            return capabilities
        if isinstance(capabilities, dict):
            caps_cls = get_capabilities_class(model_type)
            return caps_cls.model_validate(capabilities)
        return None
