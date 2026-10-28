"""四个起点的单事务迁移、私密 Agent 导出及跨文件中断重试。"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
from agentconf import (
    AgentsConfig,
    MigrationError,
    MigrationRequiredError,
    prepare_migration,
    apply_migration,
    MigrationPlan,
)
from agentconf.migrations import checksum
from test_migration import legacy

SECRET = "EXPORT-PRIVATE-SENTINEL"


def source(path, version=4, *, provider="openai", model_type="llm"):
    if version == 0:
        conn = legacy(path)
        conn.execute("UPDATE providers SET provider_type=?", (provider,))
        conn.execute("UPDATE model_instances SET model_type=?", (model_type,))
        conn.execute(
            "UPDATE agents SET temperature=0,top_p=0,extra_params=?",
            (json.dumps({"flag": False, "secret": SECRET}),),
        )
        conn.commit()
        conn.close()
        return
    with sqlite3.connect(path) as conn:
        conn.executescript(
            (Path(__file__).parent / f"fixtures/schema_v{version}.sql").read_text()
        )
        conn.execute(
            "INSERT INTO providers VALUES ('p','provider',?,'https://host.invalid',?,'2026-01-01','2026-01-02')",
            (provider, SECRET),
        )
        conn.execute(
            "INSERT INTO model_instances VALUES ('m','p','binding','request',?,NULL,'2026-01-01','2026-01-02')",
            (model_type,),
        )
        conn.execute(
            "INSERT INTO agents VALUES ('a','agent','m',0,0,2,256,?,'2026-01-01','2026-01-02')",
            (json.dumps({"flag": False, "secret": SECRET}),),
        )
        conn.execute(
            "INSERT INTO agents VALUES ('b','other-agent','m',0.7,NULL,NULL,NULL,NULL,'2026-01-01','2026-01-02')"
        )


@pytest.mark.parametrize("version", [0, 2, 3, 4])
def test_all_starting_versions_export_before_removing_agent_and_preserve_identity(
    tmp_path, version
):
    path = tmp_path / "old.db"
    source(path, version)
    before = path.read_bytes()
    with pytest.raises(MigrationRequiredError):
        AgentsConfig(path)
    plan = prepare_migration(path)
    assert path.read_bytes() == before
    assert SECRET not in plan.model_dump_json()
    assert plan.api_formats == {"binding": "openai.chat_completions"}
    f = tmp_path / "plan.json"
    plan.save(f)
    result = apply_migration(MigrationPlan.load(f))
    exported = json.loads(Path(result["agent_export_path"]).read_text())
    assert exported["source_fingerprint"] == plan.fingerprint
    assert exported["checksum"] == checksum(
        {k: v for k, v in exported.items() if k != "checksum"}
    )
    agent = exported["agents"][0]
    assert agent["agent_name"] == "agent" and agent["endpoint_name"] == "binding"
    assert agent["endpoint_id"] == "m" and agent["agent_id"] == "a"
    assert agent["request_params"]["temperature"] == 0.0
    assert agent["request_params"]["top_p"] == 0.0
    assert agent["request_params"]["extra_params"] == {"flag": False, "secret": SECRET}
    if version != 0:
        assert exported["agents"][1]["endpoint_name"] == "binding"
        assert exported["agents"][1]["request_params"]["temperature"] == 0.7
    with AgentsConfig(path) as cfg:
        assert cfg.get_provider("provider").id == "p"
        assert cfg.get_model_endpoint("binding").id == "m"
        assert cfg.resolve_model_endpoint("binding").model.model_name == (
            "deployment" if version == 0 else "request"
        )
        assert cfg._db._tables() == {
            "providers",
            "model_endpoints",
            "model_capabilities",
            "mcp_servers",
        }
        assert cfg.catalog_list() == []
        assert cfg._db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for suffix in ("agents", "prepared", "result"):
        assert plan.artifact(suffix).stat().st_mode & 0o777 == 0o600
    assert Path(result["backup_path"]).stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(result["backup_path"]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version
        assert conn.execute("SELECT count(*) FROM agents").fetchone()[0] == len(
            exported["agents"]
        )
    assert apply_migration(plan)["already_applied"]


@pytest.mark.parametrize(
    "provider,model_type,expected",
    [
        ("openai", "llm", "openai.chat_completions"),
        ("anthropic", "llm", "anthropic.messages"),
        ("deepseek", "llm", "openai.chat_completions"),
        ("openai", "tts", None),
        ("custom", "llm", None),
    ],
)
def test_format_mapping_is_proven_or_explicit(tmp_path, provider, model_type, expected):
    path = tmp_path / "old.db"
    source(path, provider=provider, model_type=model_type)
    plan = prepare_migration(path)
    if expected:
        assert plan.api_formats["binding"] == expected
    else:
        assert plan.unresolved == ["binding"]
        with pytest.raises(MigrationError):
            apply_migration(plan)
        plan = prepare_migration(path, api_formats={"binding": "future.api"})
    apply_migration(plan)
    with AgentsConfig(path) as cfg:
        result = cfg.resolve_model_endpoint("binding")
        assert result.connection.provider_type == provider
        assert result.connection.api_format == (expected or "future.api")


def test_export_write_failure_keeps_original_agent_and_schema(tmp_path):
    import agentconf.migrations as m

    path = tmp_path / "old.db"
    source(path)
    plan = prepare_migration(path)
    before = path.read_bytes()
    with patch.object(m, "write_private", side_effect=OSError("export disk full")):
        with pytest.raises(OSError):
            apply_migration(plan)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM agents").fetchone()[0] == 2
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


@pytest.mark.parametrize("stage", ["after_export", "before_commit", "after_commit"])
def test_interruptions_are_retriable_without_losing_or_overwriting_export(
    tmp_path, stage
):
    import agentconf.migrations as m

    path = tmp_path / "old.db"
    source(path)
    plan = prepare_migration(path)
    original = m.write_private

    def writing(target, payload):
        if stage == "before_commit" and target == plan.artifact("prepared"):
            original(target, payload)
            raise RuntimeError("stop before commit")
        if stage == "after_commit" and target == plan.artifact("result"):
            raise OSError("stop after commit")
        return original(target, payload)

    if stage == "after_export":
        context = patch.object(
            m, "_convert", side_effect=RuntimeError("stop after export")
        )
    else:
        context = patch.object(m, "write_private", side_effect=writing)
    with context:
        with pytest.raises((RuntimeError, MigrationError)):
            apply_migration(plan)
    export = plan.artifact("agents").read_bytes()
    with sqlite3.connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == (5 if stage == "after_commit" else 4)
    result = apply_migration(plan)
    assert result["already_applied"] == (stage == "after_commit")
    assert plan.artifact("agents").read_bytes() == export
    assert plan.artifact("result").exists()


def test_changed_plan_source_or_target_and_corrupt_export_stop_safely(tmp_path):
    path = tmp_path / "old.db"
    source(path)
    plan = prepare_migration(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE agents SET temperature=0.2 WHERE name='agent'")
    with pytest.raises(MigrationError, match="已变化"):
        apply_migration(plan)
    assert not plan.artifact("agents").exists()
    plan = prepare_migration(path)
    apply_migration(plan)
    saved = plan.artifact("agents").read_bytes()
    plan.artifact("agents").write_text("{}")
    with pytest.raises(MigrationError, match="导出"):
        apply_migration(plan)
    plan.artifact("agents").write_bytes(saved)
    with AgentsConfig(path) as cfg:
        cfg.update_provider("provider", expected_revision=1, provider_type="changed")
    with pytest.raises(MigrationError, match="不匹配"):
        apply_migration(plan)


def test_migration_can_relocate_old_manual_capability_binding(tmp_path):
    path = tmp_path / "old.db"
    source(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO model_capabilities (model_key,origin,display_name,model_type,max_context_length,source,created_at,updated_at) VALUES ('v/m','local','Manual','llm',64000,'models.dev','2026-01-01','2026-01-01')"
        )
        conn.execute("UPDATE model_instances SET capabilities_key='v/m'")
    plan = prepare_migration(path, capability_relocations={"v/m": "local/keep"})
    apply_migration(plan)
    with AgentsConfig(path) as cfg:
        assert cfg.get_model_endpoint("binding").capabilities_key == "local/keep"
        assert (
            cfg.resolve_model_endpoint("binding").capabilities.max_context_length
            == 64000
        )
        assert cfg.catalog_get("local/keep").origin == "local"


@pytest.mark.parametrize("version", [2, 3, 4])
def test_custom_schema_objects_rejected_before_any_artifact(tmp_path, version):
    path = tmp_path / "old.db"
    source(path, version)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE INDEX custom_lookup ON agents(name)")
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="自定义"):
        prepare_migration(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.backup-*"))


@pytest.mark.parametrize("extra", ["agents", "model_instances", "providers_new"])
def test_prepare_latest_schema_reports_legacy_table_pollution(tmp_path, extra):
    path = tmp_path / "config.db"
    with AgentsConfig(path):
        pass
    with sqlite3.connect(path) as conn:
        conn.execute(f'CREATE TABLE "{extra}" (id TEXT PRIMARY KEY)')
    before = path.read_bytes()
    with pytest.raises(MigrationError, match=f"unexpected_tables=.*{extra}"):
        prepare_migration(path)
    with pytest.raises(MigrationError, match="表结构不一致"):
        AgentsConfig(path, read_only=True)
    assert path.read_bytes() == before


def test_prepare_latest_schema_validates_columns_and_rows_before_noop(tmp_path):
    path = tmp_path / "config.db"
    with AgentsConfig(path):
        pass
    with pytest.raises(MigrationError, match="已经是 schema 5"):
        prepare_migration(path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE providers ADD COLUMN unexpected TEXT")
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="column_differences=.*unexpected"):
        prepare_migration(path)
    assert path.read_bytes() == before


def test_prepare_latest_schema_checks_broken_foreign_keys(tmp_path):
    path = tmp_path / "invalid.db"
    with AgentsConfig(path) as cfg:
        cfg.create_provider("p", "openai", "https://example.invalid", "TEST_ONLY")
        cfg.create_model_endpoint("p", "e", "m", "openai.chat_completions")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM providers")
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="外键"):
        prepare_migration(path)
    assert path.read_bytes() == before
