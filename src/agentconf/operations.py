# SPDX-License-Identifier: Apache-2.0
"""CLI/TUI 共用的资源入口映射，只调用公开 SDK。"""

from agentconf.models import ProviderCreate, ModelEndpointCreate, MCPServerCreate
from agentconf.storage.resources import editable_data
from agentconf.models.secure import merge_masked

DOMAINS = {
    "provider": ("provider", ProviderCreate),
    "endpoint": ("model_endpoint", ModelEndpointCreate),
    "mcp": ("mcp_server", MCPServerCreate),
}


def get_resource(cfg, domain, name):
    return getattr(cfg, "get_" + DOMAINS[domain][0])(name)


def list_resources(cfg, domain, **filters):
    suffix = {
        "provider": "providers",
        "endpoint": "model_endpoints",
        "mcp": "mcp_servers",
    }[domain]
    return getattr(cfg, "list_" + suffix)(**filters)


def resource_input(cfg, domain, record, *, show_secrets=False):
    provider_name = (
        cfg.get_provider_by_id(record.provider_id).name
        if domain == "endpoint"
        else None
    )
    return editable_data(record, provider_name=provider_name, show_secrets=show_secrets)


def validate_input(cfg, domain, payload, old=None):
    raw = resource_input(cfg, domain, old, show_secrets=True) if old else {}
    safe = resource_input(cfg, domain, old) if old else {}
    patch = merge_masked(payload, raw, safe)
    values = {**raw, **patch}
    candidate = DOMAINS[domain][1].model_validate(values)
    # 外键与用途校验只读；写入时 SDK 在写事务内再次校验。
    if domain == "endpoint" and cfg:
        cfg.get_provider(candidate.provider_name)
        if candidate.capabilities_key:
            profile = cfg.catalog_get(candidate.capabilities_key)
            if profile.model_type != candidate.model_type:
                raise ValueError("模型用途与能力引用不一致")
    return candidate, patch


def apply_resource(cfg, domain, payload, *, old=None):
    if old is None:
        return getattr(cfg, "create_" + DOMAINS[domain][0])(**payload)
    return getattr(cfg, "update_" + DOMAINS[domain][0])(
        old.name, expected_revision=old.revision, expected_id=old.id, **payload
    )


def delete_resource(cfg, domain, name, revision, expected_id=None):
    return getattr(cfg, "delete_" + DOMAINS[domain][0])(
        name, expected_revision=revision
    )


def resolve_resource(cfg, domain, name):
    return getattr(cfg, "resolve_" + DOMAINS[domain][0])(name)
