# SPDX-License-Identifier: Apache-2.0
"""具名模型端点、能力目录及独立 MCP 的配置 SDK。"""

from __future__ import annotations
import json
from contextlib import contextmanager
from pathlib import Path
from agentconf.catalog import (
    CatalogSnapshot,
    CatalogSource,
    ImportPlan,
    UpdatePlan,
    ModelsDevSource,
    prepare_import,
    prepare_update,
)
from agentconf.models import (
    Provider,
    ModelEndpoint,
    MCPServer,
    ModelConnection,
    ModelInfo,
    ResolvedMetadata,
    ResolvedModelEndpoint,
    ResolvedMCPServer,
)
from agentconf.models.capability_profile import (
    CapabilityProfile,
    build_capabilities,
    matches_capability,
    normalize_model_type,
    validate_capability_filter,
)
from agentconf.drivers import sdk_hint
from agentconf.storage.database import Database
from agentconf.storage.resources import ResourceRepository
from agentconf.storage.catalog_repo import CatalogRepository
from agentconf.exceptions import (
    LegacyAPIError,
    EndpointNotFoundError,
    MCPServerNotFoundError,
)


class AgentsConfig:
    """新库离线初始化并灌种子一次，已有旧库须显式迁移。"""

    DEFAULT_DB_PATH = "~/.agentconf/config.db"

    def __init__(
        self, db_path: str | Path | None = None, *, read_only: bool = False
    ) -> None:
        self._db = Database(db_path or self.DEFAULT_DB_PATH)
        if read_only:
            import sqlite3

            conn = sqlite3.connect(self._db.db_path.as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            self._db._connection = conn
            try:
                self._db._preflight()
            except BaseException:
                self._db.close()
                raise
        else:
            conn = self._db.conn
        self._provider_repo = ResourceRepository(conn, "providers")
        self._endpoint_repo = ResourceRepository(conn, "model_endpoints")
        self._mcp_repo = ResourceRepository(conn, "mcp_servers")
        self._mcp_provider_repo = ResourceRepository(conn, "mcp_providers")
        self._catalog_repo = CatalogRepository(conn)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> AgentsConfig:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def catalog_list(
        self, *, model_type: str | None = None, origin: str | None = None
    ) -> list[CapabilityProfile]:
        return self._catalog_repo.list(model_type=model_type, origin=origin)

    def catalog_get(self, key: str) -> CapabilityProfile:
        return self._catalog_repo.get(key)

    def catalog_create(self, profile: CapabilityProfile) -> CapabilityProfile:
        return self._catalog_repo.create(profile)

    def catalog_references(self, key: str) -> list[str]:
        return self._catalog_repo.references(key)

    def catalog_edit(
        self, profile: CapabilityProfile, *, expected_revision: int
    ) -> CapabilityProfile:
        return self._catalog_repo.edit(profile, expected_revision=expected_revision)

    def catalog_delete(self, key: str, *, expected_revision: int) -> None:
        self._catalog_repo.delete(key, expected_revision=expected_revision)

    def catalog_derive(
        self,
        parent_key: str,
        key: str,
        *,
        display_name: str | None = None,
        expected_revision: int | None = None,
    ) -> CapabilityProfile:
        return self._catalog_repo.derive(
            parent_key,
            key,
            display_name=display_name,
            expected_revision=expected_revision,
        )

    def catalog_clone(
        self,
        source_key: str,
        key: str,
        *,
        display_name: str | None = None,
        expected_revision: int | None = None,
    ) -> CapabilityProfile:
        return self._catalog_repo.clone(
            source_key,
            key,
            display_name=display_name,
            expected_revision=expected_revision,
        )

    def catalog_variants(self, key: str) -> list[CapabilityProfile]:
        return self._catalog_repo.variants(key)

    def catalog_stale(self, key: str) -> bool:
        return self._catalog_repo.stale(key)

    def catalog_diff(self, key: str) -> dict:
        return self._catalog_repo.diff(key)

    def catalog_rebuild(
        self,
        key: str,
        *,
        expected_revision: int,
        expected_parent_fingerprint: str | None = None,
    ) -> CapabilityProfile:
        return self._catalog_repo.rebuild(
            key,
            expected_revision=expected_revision,
            expected_parent_fingerprint=expected_parent_fingerprint,
        )

    def catalog_relocate(
        self,
        key: str,
        new_key: str,
        *,
        expected_revision: int,
        expected_references: list[str] | None = None,
    ) -> CapabilityProfile:
        return self._catalog_repo.relocate(
            key,
            new_key,
            expected_revision=expected_revision,
            expected_references=expected_references,
        )

    def catalog_preview(
        self,
        source: CatalogSource | CatalogSnapshot | None = None,
        *,
        relocations: dict[str, str] | None = None,
    ) -> ImportPlan:
        snapshot = (
            source
            if isinstance(source, CatalogSnapshot)
            else (source or ModelsDevSource()).fetch()
        )
        return prepare_import(
            snapshot,
            self.catalog_list(),
            relocations=self._catalog_repo.prepare_relocations(relocations),
        )

    def catalog_apply(
        self,
        plan: ImportPlan,
        keys: list[str] | None = None,
        *,
        types: list[str] | None = None,
    ) -> dict:
        return self._catalog_repo.apply(plan, keys, types=types)

    def catalog_update_preview(
        self,
        source: CatalogSource | CatalogSnapshot | None = None,
        *,
        relocations: dict[str, str] | None = None,
    ) -> UpdatePlan:
        """显式下载最新版目录，预览新增、托管更新、人工保护和冲突。"""
        snapshot = (
            source
            if isinstance(source, CatalogSnapshot)
            else (source or ModelsDevSource()).fetch()
        )
        plan = prepare_update(
            snapshot,
            self.catalog_list(),
            relocations=self._catalog_repo.prepare_relocations(relocations),
        )
        plan.references = {key: self.catalog_references(key) for key in plan.updatable}
        plan.reference_fingerprints = {
            key: self._catalog_repo.reference_fingerprint(key) for key in plan.updatable
        }
        return plan

    def catalog_update_apply(
        self,
        plan: UpdatePlan,
        keys: list[str] | None = None,
        *,
        types: list[str] | None = None,
    ) -> dict:
        """应用已预览快照，仅新增和更新由同来源维护的档案。"""
        return self._catalog_repo.apply_update(plan, keys, types=types)

    def catalog_set_management(self, *args, **kwargs) -> CapabilityProfile:
        """0.2 维护权交接入口；0.3 由 origin/derive/clone 取代。"""
        return self._catalog_repo.set_management(*args, **kwargs)

    def create_provider(
        self,
        name: str,
        provider_type: str,
        base_url,
        api_key=None,
        *,
        connection_options=None,
    ):
        return self._provider_repo.create(
            dict(
                name=name,
                provider_type=provider_type,
                base_url=base_url,
                api_key=api_key,
                connection_options={}
                if connection_options is None
                else connection_options,
            )
        )

    def get_provider(self, name: str) -> Provider:
        return self._provider_repo.get(name)

    def get_provider_by_id(self, provider_id: str) -> Provider:
        return self._provider_repo.get_by_id(provider_id)

    def list_providers(self) -> list[Provider]:
        return self._provider_repo.list()

    def update_provider(
        self,
        provider_name,
        *,
        expected_revision: int,
        expected_id: str | None = None,
        **changes,
    ):
        return self._provider_repo.update(
            provider_name, changes, expected_revision, expected_id
        )

    def delete_provider(
        self, name, *, expected_revision: int, expected_id: str | None = None
    ):
        return self._provider_repo.delete(name, expected_revision, expected_id)

    def provider_references(self, name: str) -> list[str]:
        return self._provider_repo.provider_references(self.get_provider(name).id)

    def create_model_endpoint(
        self,
        provider_name,
        name,
        model_name,
        api_format,
        *,
        model_type="llm",
        capabilities_key=None,
    ):
        return self._endpoint_repo.create(
            dict(
                provider_name=provider_name,
                name=name,
                model_name=model_name,
                api_format=api_format,
                model_type=model_type,
                capabilities_key=capabilities_key,
            )
        )

    def get_model_endpoint(self, name: str) -> ModelEndpoint:
        return self._endpoint_repo.get(name)

    def get_model_endpoint_by_id(self, endpoint_id: str) -> ModelEndpoint:
        return self._endpoint_repo.get_by_id(endpoint_id)

    def list_model_endpoints(
        self,
        *,
        provider_name=None,
        model_type=None,
        api_format=None,
        capability_filter=None,
    ):
        if capability_filter:
            validate_capability_filter(capability_filter)
        with self._snapshot():
            provider = self.get_provider(provider_name).id if provider_name else None
            endpoints = self._endpoint_repo.list()
            return [
                e
                for e in endpoints
                if (not provider or e.provider_id == provider)
                and (not model_type or e.model_type == normalize_model_type(model_type))
                and (not api_format or e.api_format == api_format)
                and (
                    not capability_filter
                    or matches_capability(
                        self._resolve_model(e.name).capabilities, capability_filter
                    )
                )
            ]

    def update_model_endpoint(
        self,
        endpoint_name,
        *,
        expected_revision: int,
        expected_id: str | None = None,
        **changes,
    ):
        return self._endpoint_repo.update(
            endpoint_name, changes, expected_revision, expected_id
        )

    def delete_model_endpoint(
        self, name, *, expected_revision: int, expected_id: str | None = None
    ):
        return self._endpoint_repo.delete(name, expected_revision, expected_id)

    def create_mcp_provider(
        self, name, *, vendor=None, api_key=None, default_binding=None
    ):
        """创建可供多台 MCP 服务共享的认证身份。"""
        return self._mcp_provider_repo.create(
            dict(
                name=name,
                vendor=vendor,
                api_key=api_key,
                default_binding=default_binding,
            )
        )

    def get_mcp_provider(self, name):
        """按可读名称取 MCP 认证身份。"""
        return self._mcp_provider_repo.get(name)

    def get_mcp_provider_by_id(self, provider_id):
        """按稳定 ID 取 MCP 认证身份。"""
        return self._mcp_provider_repo.get_by_id(provider_id)

    def list_mcp_providers(self):
        """列出独立的 MCP 认证身份。"""
        return self._mcp_provider_repo.list()

    def update_mcp_provider(
        self, provider_name, *, expected_revision, expected_id=None, **changes
    ):
        """以 revision 与可选 ID 核对后更新共享认证。"""
        return self._mcp_provider_repo.update(
            provider_name, changes, expected_revision, expected_id
        )

    def delete_mcp_provider(self, name, *, expected_revision, expected_id=None):
        """拒绝删除仍被 MCP 服务引用的认证身份。"""
        return self._mcp_provider_repo.delete(name, expected_revision, expected_id)

    def mcp_provider_references(self, name):
        """查询共享认证身份的服务引用。"""
        return self._mcp_provider_repo.mcp_provider_references(
            self.get_mcp_provider(name).id
        )

    def validate_mcp_provider(self, provider, *, provider_id):
        """预览共享认证修改对继承服务的影响；写事务再次核对。"""
        for ref in self._mcp_provider_repo.mcp_provider_references(provider_id):
            self._mcp_repo.validate_mcp(
                self.get_mcp_server_by_id(ref["id"]), provider=provider
            )

    def validate_mcp_server(self, server):
        """只读校验有效认证绑定；保存事务还会再次校验。"""
        self._mcp_repo.validate_mcp(server)

    def create_mcp_server(self, name, transport, settings, *, auth=None):
        return self._mcp_repo.create(
            dict(
                name=name,
                transport=transport,
                settings=settings,
                **({"auth": auth} if auth is not None else {}),
            )
        )

    def get_mcp_server(self, name: str) -> MCPServer:
        return self._mcp_repo.get(name)

    def get_mcp_server_by_id(self, server_id: str) -> MCPServer:
        return self._mcp_repo.get_by_id(server_id)

    def list_mcp_servers(self, *, transport=None):
        return [
            s
            for s in self._mcp_repo.list()
            if transport is None or s.transport == transport
        ]

    def update_mcp_server(
        self,
        server_name,
        *,
        expected_revision: int,
        expected_id: str | None = None,
        **changes,
    ):
        return self._mcp_repo.update(
            server_name, changes, expected_revision, expected_id
        )

    def delete_mcp_server(
        self, name, *, expected_revision: int, expected_id: str | None = None
    ):
        return self._mcp_repo.delete(name, expected_revision, expected_id)

    @contextmanager
    def _snapshot(self):
        self._db.conn.execute("BEGIN")
        try:
            yield
        finally:
            self._db.conn.rollback()

    def _resolve_model(self, name):
        endpoint = self.get_model_endpoint(name)
        provider = self.get_provider_by_id(endpoint.provider_id)
        profile = (
            self.catalog_get(endpoint.capabilities_key)
            if endpoint.capabilities_key
            else None
        )
        capabilities, sources = build_capabilities(endpoint.model_type, profile)
        metadata = None
        if profile:
            metadata = ResolvedMetadata(
                model_key=profile.model_key,
                origin=profile.origin,
                source=profile.source,
                revision=profile.revision,
                derived_from=profile.derived_from,
                base_fingerprint=profile.base_fingerprint,
                stale=self.catalog_stale(profile.model_key)
                if profile.origin == "derived"
                else False,
            )
        return ResolvedModelEndpoint(
            endpoint_id=endpoint.id,
            name=endpoint.name,
            revision=endpoint.revision,
            connection=ModelConnection(
                provider_id=provider.id,
                provider_name=provider.name,
                provider_revision=provider.revision,
                provider_type=provider.provider_type,
                api_format=endpoint.api_format,
                base_url=provider.base_url,
                api_key=provider.api_key,
                connection_options=provider.connection_options,
                sdk_hint=sdk_hint(endpoint.api_format),
            ),
            model=ModelInfo(
                model_name=endpoint.model_name, model_type=endpoint.model_type
            ),
            metadata=metadata,
            capabilities=capabilities,
            capability_sources=sources,
        )

    def resolve_model_endpoint(self, name: str) -> ResolvedModelEndpoint:
        with self._snapshot():
            return self._resolve_model(name)

    def _resolve_mcp(self, name):
        server = self.get_mcp_server(name)
        return ResolvedMCPServer(
            server_id=server.id,
            name=server.name,
            revision=server.revision,
            transport=server.transport,
            connection=server.settings,
            auth=self._resolve_mcp_auth(server),
        )

    def _resolve_mcp_auth(self, server):
        from agentconf.models.mcp_provider import ResolvedMCPAuth, CredentialBinding

        if server.auth.mode != "provider":
            return ResolvedMCPAuth(
                mode=server.auth.mode,
                binding=getattr(server.auth, "binding", None),
                reference=getattr(server.auth, "reference", None),
            )
        row = self._db.conn.execute(
            "SELECT id,name,revision,default_binding_json,"
            "(api_key IS NOT NULL AND length(api_key)>0) AS key_configured "
            "FROM mcp_providers WHERE id=?",
            (server.auth.provider_id,),
        ).fetchone()
        if row is None:
            from agentconf.exceptions import MCPProviderNotFoundError

            raise MCPProviderNotFoundError("MCP Provider 不存在")
        binding = server.auth.binding
        if server.auth.binding_mode == "inherit":
            raw = json.loads(row["default_binding_json"] or "null")
            binding = CredentialBinding.model_validate(raw) if raw is not None else None
        if binding is None:
            raise ValueError("MCP Provider 没有可继承的认证方式")
        from agentconf.models.mcp_server import validate_binding

        validate_binding(server.transport, server.settings, binding)
        return ResolvedMCPAuth(
            mode="provider",
            provider_id=row["id"],
            provider_name=row["name"],
            provider_revision=row["revision"],
            key_configured=bool(row["key_configured"]),
            binding_mode=server.auth.binding_mode,
            binding=binding,
        )

    def read_mcp_credential(
        self,
        server_id,
        *,
        expected_server_revision,
        expected_provider_id,
        expected_provider_revision,
    ):
        """按确定连接快照读取 Key；调用授权由宿主在此之前完成。"""
        from pydantic import SecretStr
        from agentconf.exceptions import (
            RevisionConflictError,
            MCPCredentialMissingError,
        )

        with self._snapshot():
            server = self.get_mcp_server_by_id(server_id)
            auth = self._resolve_mcp_auth(server)
            if (server.revision, auth.provider_id, auth.provider_revision) != (
                expected_server_revision,
                expected_provider_id,
                expected_provider_revision,
            ) or auth.mode != "provider":
                raise RevisionConflictError("MCP 凭据来源或版本已变化")
            row = self._db.conn.execute(
                "SELECT api_key FROM mcp_providers WHERE id=?", (auth.provider_id,)
            ).fetchone()
            if row is None or not row[0]:
                raise MCPCredentialMissingError("MCP Provider 缺少密钥")
            return SecretStr(row[0])

    def read_mcp_inline_headers(self, server_id, *, expected_server_revision):
        """实际调用前读取静态 HTTP 认证；静态解析不解包这些值。"""
        from agentconf.exceptions import RevisionConflictError
        from agentconf.models.mcp_server import StreamableHTTPConnection

        with self._snapshot():
            server = self.get_mcp_server_by_id(server_id)
            if (
                server.revision != expected_server_revision
                or server.auth.mode != "inline"
            ):
                raise RevisionConflictError("MCP 静态认证配置已变化")
            if not isinstance(server.settings, StreamableHTTPConnection):
                raise ValueError("静态 HTTP 认证只适用于 streamable_http")
            return server.settings.headers

    def resolve_mcp_server(self, name: str) -> ResolvedMCPServer:
        with self._snapshot():
            return self._resolve_mcp(name)

    def _resolve_many(self, names, resolver, missing_type):
        if isinstance(names, (str, bytes)):
            raise ValueError("批量解析需要名称列表，不接受单个字符串")
        result, missing = {}, []
        with self._snapshot():
            for name in names:
                try:
                    result[name] = resolver(name)
                except missing_type:
                    missing.append(name)
            if missing:
                raise missing_type("资源未找到: " + ", ".join(missing))
        return result

    def resolve_model_endpoints(
        self, names: list[str]
    ) -> dict[str, ResolvedModelEndpoint]:
        return self._resolve_many(names, self._resolve_model, EndpointNotFoundError)

    def resolve_mcp_servers(self, names: list[str]) -> dict[str, ResolvedMCPServer]:
        return self._resolve_many(names, self._resolve_mcp, MCPServerNotFoundError)

    def __getattr__(self, name):
        old_models = {
            "create_model",
            "get_model",
            "get_model_by_id",
            "list_models",
            "update_model",
            "delete_model",
            "resolve_model",
            "find_models_by_capability",
        }
        old_agents = {
            "create_agent",
            "get_agent",
            "get_agent_by_id",
            "list_agents",
            "update_agent",
            "delete_agent",
            "resolve_agent",
        }
        if name in old_models | old_agents:

            def removed(*args, **kwargs):
                raise LegacyAPIError(
                    "旧模型/Agent 聚合 API 已移除；使用 model_endpoint 系列 API，在下游组合 Agent 和请求参数，并通过 migrate prepare/apply 导出旧配置"
                )

            return removed
        raise AttributeError(name)
