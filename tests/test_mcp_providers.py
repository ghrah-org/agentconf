"""共享 MCP 认证的身份、事务、凭据读取与跨层删除保护。"""

import json
import sqlite3
from uuid import uuid4
import pytest
from agentconf import (
    AgentsConfig,
    CredentialBinding,
    ResourceInUseError,
    RevisionConflictError,
    MCPProviderNotFoundError,
    MCPCredentialMissingError,
)
from agentconf.operations import delete_resource
from agentconf.migrations import prepare_migration, apply_migration
from agentconf.storage.schema_v5 import Database as Schema5

BINDING = dict(
    target="http_header", name="Authorization", value_template="Bearer {{auth}}"
)
SECRET = "SHARED-MCP-PRIVATE-SENTINEL"


@pytest.fixture
def cfg(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as c:
        yield c


def provider(c):
    return c.create_mcp_provider(
        "vendor/project", vendor="vendor", api_key=SECRET, default_binding=BINDING
    )


def server(c, p, name="query", **auth):
    return c.create_mcp_server(
        name,
        "streamable_http",
        {"url": f"https://example.invalid/{name}"},
        auth={
            "mode": "provider",
            "provider_id": p.id,
            "binding_mode": "inherit",
            **auth,
        },
    )


def test_shared_identity_rename_delete_rotation_and_no_secret_in_resolution(cfg):
    p = provider(cfg)
    a, b = server(cfg, p), server(cfg, p, "fetch")
    statements = []
    cfg._db.conn.set_trace_callback(statements.append)
    resolved = cfg.resolve_mcp_server(a.name)
    cfg._db.conn.set_trace_callback(None)
    assert SECRET not in resolved.model_dump_json(context={"show_secrets": True})
    assert not any("SELECT api_key FROM" in sql for sql in statements)
    assert resolved.auth.provider_id == p.id and resolved.auth.key_configured
    assert len(cfg.mcp_provider_references(p.name)) == 2
    with pytest.raises(ResourceInUseError):
        cfg.delete_mcp_provider(p.name, expected_revision=1)
    cfg.update_mcp_provider(
        p.name, expected_revision=1, name="renamed", api_key="rotated"
    )
    assert cfg.resolve_mcp_server(b.name).auth.provider_id == p.id
    with pytest.raises(RevisionConflictError):
        cfg.read_mcp_credential(
            a.id,
            expected_server_revision=1,
            expected_provider_id=p.id,
            expected_provider_revision=1,
        )
    assert (
        cfg.read_mcp_credential(
            a.id,
            expected_server_revision=1,
            expected_provider_id=p.id,
            expected_provider_revision=2,
        ).get_secret_value()
        == "rotated"
    )
    cfg.create_mcp_provider(p.name, api_key="different", default_binding=BINDING)
    assert cfg.resolve_mcp_server(a.name).auth.provider_name == "renamed"


def test_override_is_independent_and_inherited_changes_are_validated(cfg):
    p = provider(cfg)
    a = server(cfg, p)
    custom = dict(target="http_header", name="X-Key", value_template="{{auth}}")
    b = server(cfg, p, "fetch", binding_mode="override", binding=custom)
    cfg.update_mcp_provider(p.name, expected_revision=1, default_binding=custom)
    assert cfg.resolve_mcp_server(a.name).auth.binding.name == "X-Key"
    assert cfg.resolve_mcp_server(b.name).auth.binding.name == "X-Key"
    with pytest.raises(ValueError):
        cfg.update_mcp_provider(
            p.name,
            expected_revision=2,
            default_binding=dict(
                target="stdio_env", name="KEY", value_template="{{auth}}"
            ),
        )
    assert cfg.get_mcp_provider(p.name).revision == 2


def test_provider_missing_key_and_clear_never_become_no_auth(cfg):
    p = cfg.create_mcp_provider("empty", default_binding=BINDING)
    s = server(cfg, p)
    with pytest.raises(MCPCredentialMissingError):
        cfg.read_mcp_credential(
            s.id,
            expected_server_revision=1,
            expected_provider_id=p.id,
            expected_provider_revision=1,
        )
    assert cfg.resolve_mcp_server(s.name).auth.mode == "provider"


def test_foreign_reference_and_auth_collision_rejected(cfg):
    cfg.create_provider("model-only", "openai", "https://llm.invalid", "model-key")
    model = cfg.get_provider("model-only")
    with pytest.raises(MCPProviderNotFoundError):
        server(cfg, model)
    p = provider(cfg)
    with pytest.raises(ValueError, match="冲突"):
        cfg.create_mcp_server(
            "collision",
            "streamable_http",
            {"url": "https://example.invalid", "headers": {"authorization": "literal"}},
            auth={"mode": "provider", "provider_id": p.id, "binding_mode": "inherit"},
        )
    assert cfg.list_mcp_servers() == []


@pytest.mark.parametrize(
    "template",
    [
        "plain",
        "{{auth}}{{auth}}",
        "{{auth}}\r\nX: bad",
        "${OTHER}{{auth}}",
        "{{secret}}{{auth}}",
    ],
)
def test_templates_are_explicit_and_bounded(template):
    with pytest.raises(ValueError):
        CredentialBinding(
            target="http_header", name="Authorization", value_template=template
        )


def test_common_delete_does_not_delete_recreated_resource(cfg):
    old = cfg.create_mcp_server("tools", "stdio", {"command": "python"})
    cfg.delete_mcp_server(old.name, expected_revision=1, expected_id=old.id)
    new = cfg.create_mcp_server("tools", "stdio", {"command": "different"})
    with pytest.raises(RevisionConflictError):
        delete_resource(cfg, "mcp", old.name, 1, expected_id=old.id)
    assert cfg.get_mcp_server(new.name).id == new.id


def old_db(path):
    db = Schema5(path)
    db.connect()
    db.conn.execute(
        "INSERT INTO providers VALUES ('model-id','old-name','openai','https://llm.invalid',?, '{}',1,'2026-01-01','2026-01-01')",
        (SECRET,),
    )
    db.conn.execute(
        "INSERT INTO mcp_servers VALUES ('server-id','search','streamable_http',?,1,'2026-01-01','2026-01-01')",
        (
            json.dumps(
                {
                    "url": "https://tools.invalid/mcp",
                    "headers": {"Authorization": "Bearer {{auth}}"},
                    "credential_ref": "old-name",
                }
            ),
        ),
    )
    db.conn.commit()
    db.close()


def test_schema5_requires_explicit_mapping_copies_only_selected_key(tmp_path):
    path = tmp_path / "old.db"
    old_db(path)
    before = path.read_bytes()
    pending = prepare_migration(path)
    assert pending.unresolved and path.read_bytes() == before
    p_id = str(uuid4())
    mapping = {
        "providers": [
            {
                "id": p_id,
                "name": "vendor/project",
                "source_model_provider_id": "model-id",
                "default_binding": BINDING,
            }
        ],
        "servers": {
            "server-id": {
                "auth": {
                    "mode": "provider",
                    "provider_id": p_id,
                    "binding_mode": "inherit",
                },
                "remove_fields": [["headers", "Authorization"]],
            }
        },
    }
    plan = prepare_migration(path, mcp_map=mapping)
    assert not plan.unresolved and SECRET not in plan.model_dump_json()
    assert path.read_bytes() == before
    result = apply_migration(plan)
    assert result["agent_export_path"] is None
    with AgentsConfig(path) as c:
        assert c.get_provider("old-name").api_key.get_secret_value() == SECRET
        assert c.get_mcp_provider_by_id(p_id).api_key.get_secret_value() == SECRET
        assert c.get_mcp_server("search").id == "server-id"
        assert c.resolve_mcp_server("search").auth.provider_id == p_id
        c.update_mcp_provider("vendor/project", expected_revision=1, api_key="new")
        assert c.get_provider("old-name").api_key.get_secret_value() == SECRET
    with pytest.raises(Exception):
        # 迁移后有写入，不允许把同一旧计划当成任意可重放操作。
        apply_migration(plan)


def test_schema5_static_key_extraction_and_old_plan_rejection(tmp_path):
    path = tmp_path / "old.db"
    old_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE mcp_servers SET settings_json=?",
            (
                json.dumps(
                    {
                        "url": "https://tools.invalid/mcp",
                        "headers": {"Authorization": "Bearer " + SECRET},
                    }
                ),
            ),
        )
    identifier = str(uuid4())
    mapping = {
        "providers": [
            {
                "id": identifier,
                "name": "extracted",
                "source_server_id": "server-id",
                "source_field": ["headers", "Authorization"],
                "strip_prefix": "Bearer ",
                "default_binding": BINDING,
            }
        ],
        "servers": {
            "server-id": {
                "auth": {
                    "mode": "provider",
                    "provider_id": identifier,
                    "binding_mode": "inherit",
                },
                "remove_fields": [["headers", "Authorization"]],
            }
        },
    }
    plan = prepare_migration(path, mcp_map=mapping)
    assert SECRET not in plan.model_dump_json()
    apply_migration(plan)
    with AgentsConfig(path) as c:
        assert c.get_mcp_provider("extracted").api_key.get_secret_value() == SECRET
    from agentconf import MigrationError, MigrationPlan

    old_plan = tmp_path / "v2.json"
    old_plan.write_text('{"plan_version":2,"target_version":5}')
    with pytest.raises(MigrationError, match="重新 prepare"):
        MigrationPlan.load(old_plan)


def test_mcp_provider_cli_preview_apply_and_references(tmp_path, capsys):
    from agentconf.cli import main

    path = tmp_path / "cli.db"
    payload = tmp_path / "provider.json"
    payload.write_text(
        json.dumps(
            {"name": "vendor/account", "api_key": SECRET, "default_binding": BINDING}
        )
    )
    base = ["--db-path", str(path), "mcp-provider"]
    main(base + ["create", "--file", str(payload)])
    assert SECRET not in capsys.readouterr().out and not path.exists()
    main(base + ["create", "--file", str(payload), "--apply"])
    assert SECRET not in capsys.readouterr().out
    with AgentsConfig(path) as c:
        p = c.get_mcp_provider("vendor/account")
        server(c, p)
    main(base + ["references", "vendor/account"])
    assert json.loads(capsys.readouterr().out)["references"][0]["name"] == "query"
