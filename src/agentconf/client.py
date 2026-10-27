# SPDX-License-Identifier: Apache-2.0
"""配置 SDK：Provider 连接、能力档案、模型绑定与 Agent 采样参数。"""

from __future__ import annotations

from pathlib import Path

from agentconf.catalog import (
    CatalogSnapshot,
    CatalogSource,
    ImportPlan,
    ModelsDevSource,
    prepare_import,
)
from agentconf.models import (
    Agent,
    AgentCreate,
    ModelInstance,
    ModelInstanceCreate,
    Provider,
    ProviderCreate,
    ProviderType,
    ResolvedAgentConfig,
    ResolvedModelConfig,
)
from agentconf.models.base import generate_uuid
from agentconf.models.capability_profile import (
    CapabilityProfile,
    build_capabilities,
    matches_capability,
    normalize_model_type,
    validate_capability_filter,
)
from agentconf.storage import (
    AgentRepository,
    Database,
    ModelRepository,
    ProviderRepository,
)
from agentconf.storage.catalog_repo import CatalogRepository


def _check_fields(values: dict, allowed: set[str]) -> None:
    invalid = set(values) - allowed
    if invalid:
        raise TypeError(f"不支持的配置字段: {', '.join(sorted(invalid))}")


class AgentsConfig:
    """打开配置库，仅首次初始化或升级时写入随包种子；初始化不访问网络。"""

    DEFAULT_DB_PATH = "~/.agentconf/config.db"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db = Database(db_path or self.DEFAULT_DB_PATH)
        conn = self._db.conn
        self._provider_repo = ProviderRepository(conn)
        self._model_repo = ModelRepository(conn)
        self._agent_repo = AgentRepository(conn)
        self._catalog_repo = CatalogRepository(conn)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> AgentsConfig:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def catalog_list(
        self, *, model_type: str | None = None, entry_kind: str | None = None
    ) -> list[CapabilityProfile]:
        return self._catalog_repo.list(model_type=model_type, entry_kind=entry_kind)

    def catalog_get(self, key: str) -> CapabilityProfile:
        return self._catalog_repo.get(key)

    def catalog_create(self, profile: CapabilityProfile) -> CapabilityProfile:
        return self._catalog_repo.create(profile)

    def catalog_references(self, key: str) -> list[str]:
        return self._catalog_repo.references(key)

    def catalog_edit(
        self, profile: CapabilityProfile, *, expected_revision: str
    ) -> CapabilityProfile:
        return self._catalog_repo.edit(profile, expected_revision=expected_revision)

    def catalog_delete(self, key: str) -> None:
        self._catalog_repo.delete(key)

    def catalog_preview(
        self, source: CatalogSource | CatalogSnapshot | None = None
    ) -> ImportPlan:
        snapshot = (
            source
            if isinstance(source, CatalogSnapshot)
            else (source or ModelsDevSource()).fetch()
        )
        return prepare_import(snapshot, self.catalog_list())

    def catalog_apply(
        self,
        plan: ImportPlan,
        keys: list[str] | None = None,
        *,
        types: list[str] | None = None,
    ) -> dict:
        return self._catalog_repo.apply(plan, keys, types=types)

    def create_provider(
        self, name: str, provider_type: ProviderType | str, base_url: str, api_key: str
    ) -> Provider:
        data = ProviderCreate(
            name=name, provider_type=provider_type, base_url=base_url, api_key=api_key
        )
        return self._provider_repo.create(
            id=generate_uuid(),
            name=data.name,
            provider_type=data.provider_type.value,
            base_url=data.base_url,
            api_key=data.api_key.get_secret_value(),
        )

    def get_provider(self, name: str) -> Provider:
        return self._provider_repo.get_by_name(name)

    def get_provider_by_id(self, provider_id: str) -> Provider:
        return self._provider_repo.get_by_id(provider_id)

    def list_providers(self) -> list[Provider]:
        return self._provider_repo.list_all()

    def update_provider(self, name: str, **kwargs) -> Provider:
        _check_fields(kwargs, {"provider_type", "base_url", "api_key"})
        old = self.get_provider(name).model_dump()
        old.update(kwargs)
        data = ProviderCreate.model_validate(old)
        values = data.model_dump(mode="python")
        values["api_key"] = data.api_key.get_secret_value()
        values["provider_type"] = data.provider_type.value
        return self._provider_repo.update(name, **{k: values[k] for k in kwargs})

    def delete_provider(self, name: str) -> None:
        self._provider_repo.delete(name)

    def create_model(
        self,
        provider_name: str,
        name: str,
        model_name: str,
        model_type: str = "llm",
        capabilities_key: str | None = None,
    ) -> ModelInstance:
        data = ModelInstanceCreate(
            provider_name=provider_name,
            name=name,
            model_name=model_name,
            model_type=model_type,
            capabilities_key=capabilities_key,
        )
        provider = self.get_provider(data.provider_name)
        return self._model_repo.create(
            id=generate_uuid(),
            provider_id=provider.id,
            **data.model_dump(exclude={"provider_name"}),
        )

    def get_model(self, name: str) -> ModelInstance:
        return self._model_repo.get_by_name(name)

    def get_model_by_id(self, model_id: str) -> ModelInstance:
        return self._model_repo.get_by_id(model_id)

    def list_models(
        self, provider_name: str | None = None, model_type: str | None = None
    ) -> list[ModelInstance]:
        models = (
            self._model_repo.list_by_provider(self.get_provider(provider_name).id)
            if provider_name
            else self._model_repo.list_all()
        )
        return (
            [m for m in models if m.model_type == normalize_model_type(model_type)]
            if model_type
            else models
        )

    def find_models_by_capability(self, capability_filter: str) -> list[ModelInstance]:
        validate_capability_filter(capability_filter)
        return [
            m
            for m in self.list_models()
            if matches_capability(
                self.resolve_model(m.name).capabilities, capability_filter
            )
        ]

    def update_model(self, name: str, **kwargs) -> ModelInstance:
        return self._model_repo.update(name, **kwargs)

    def delete_model(self, name: str) -> None:
        self._model_repo.delete(name)

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
        data = AgentCreate(
            name=name,
            model_name=model_name,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            extra_params=extra_params,
        )
        model = self.get_model(model_name)
        return self._agent_repo.create(
            id=generate_uuid(),
            model_instance_id=model.id,
            **data.model_dump(exclude={"model_name"}),
        )

    def get_agent(self, name: str) -> Agent:
        return self._agent_repo.get_by_name(name)

    def get_agent_by_id(self, agent_id: str) -> Agent:
        return self._agent_repo.get_by_id(agent_id)

    def list_agents(self) -> list[Agent]:
        return self._agent_repo.list_all()

    def update_agent(self, name: str, **kwargs) -> Agent:
        _check_fields(
            kwargs,
            {
                "model_name",
                "temperature",
                "top_p",
                "top_k",
                "max_tokens",
                "extra_params",
            },
        )
        old = self.get_agent(name)
        values = old.model_dump(
            exclude={"id", "model_instance_id", "created_at", "updated_at"}
        )
        values["model_name"] = self.get_model_by_id(old.model_instance_id).name
        values.update(kwargs)
        AgentCreate.model_validate(values)
        if "model_name" in kwargs:
            kwargs["model_instance_id"] = self.get_model(kwargs.pop("model_name")).id
        return self._agent_repo.update(name, **kwargs)

    def delete_agent(self, name: str) -> None:
        self._agent_repo.delete(name)

    def resolve_model(self, name: str) -> ResolvedModelConfig:
        binding = self.get_model(name)
        provider = self.get_provider_by_id(binding.provider_id)
        profile = (
            self.catalog_get(binding.capabilities_key)
            if binding.capabilities_key is not None
            else None
        )
        capabilities, sources = build_capabilities(binding.model_type, profile)
        return ResolvedModelConfig(
            provider_name=provider.name,
            provider_type=provider.provider_type,
            base_url=provider.base_url,
            api_key=provider.api_key.get_secret_value(),
            model_name=binding.model_name,
            model_type=binding.model_type,
            capabilities_key=binding.capabilities_key,
            capabilities=capabilities,
            capability_sources=sources,
        )

    def resolve_agent(self, name: str) -> ResolvedAgentConfig:
        agent = self.get_agent(name)
        model = self.get_model_by_id(agent.model_instance_id)
        return ResolvedAgentConfig(
            agent_name=agent.name,
            model=self.resolve_model(model.name),
            **agent.model_dump(
                exclude={"id", "name", "model_instance_id", "created_at", "updated_at"}
            ),
        )
