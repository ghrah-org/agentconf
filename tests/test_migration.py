"""历史库升级：预检、WAL 一致性备份、原子回滚与引用保留。"""

import sqlite3
from unittest.mock import patch

import pytest

from agentconf import AgentsConfig, MigrationError
from agentconf.storage.database import Database

LEGACY_SCHEMA = """
CREATE TABLE providers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 provider_type TEXT NOT NULL CHECK(provider_type IN ('openai','anthropic','custom')),
 base_url TEXT NOT NULL, api_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE model_instances (
 id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
 name TEXT NOT NULL, model_name TEXT NOT NULL, model_type TEXT NOT NULL DEFAULT 'llm',
 base_url TEXT, api_key TEXT, capabilities TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(provider_id,name));
CREATE TABLE agents (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 model_instance_id TEXT NOT NULL REFERENCES model_instances(id) ON DELETE CASCADE,
 temperature REAL, top_p REAL, top_k INTEGER, max_tokens INTEGER, extra_params TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def legacy(path, metadata=False):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    if metadata:
        conn.execute("ALTER TABLE model_instances ADD COLUMN metadata_provider TEXT")
        conn.execute("ALTER TABLE model_instances ADD COLUMN metadata_model TEXT")
        conn.execute(
            "CREATE TABLE metadata_sources(source_id TEXT PRIMARY KEY, url TEXT)"
        )
        conn.execute(
            "CREATE TABLE model_metadata(source_id TEXT, provider_id TEXT, model_id TEXT, capabilities TEXT, raw TEXT)"
        )
    conn.execute(
        "INSERT INTO providers VALUES ('p','provider','openai','https://test.invalid','private-test-value','2026-01-01','2026-01-02')"
    )
    conn.execute(
        "INSERT INTO model_instances (id,provider_id,name,model_name,model_type,created_at,updated_at) VALUES ('m','p','binding','deployment','llm','2026-01-01','2026-01-02')"
    )
    conn.execute(
        "INSERT INTO agents VALUES ('a','agent','m',0.3,0.9,10,100,'{\"custom\": true}','2026-01-01','2026-01-02')"
    )
    conn.commit()
    return conn


@pytest.mark.parametrize("metadata", [False, True])
def test_migrate_keeps_ids_references_parameters_and_null_binding(tmp_path, metadata):
    path = tmp_path / "config.db"
    legacy(path, metadata).close()
    with AgentsConfig(path) as cfg:
        assert cfg.get_provider("provider").id == "p"
        assert cfg.get_model("binding").id == "m"
        assert cfg.get_model("binding").capabilities_key is None
        agent = cfg.get_agent("agent")
        assert agent.id == "a" and agent.model_instance_id == "m"
        assert agent.temperature == 0.3 and agent.max_tokens == 100
        assert agent.extra_params == {"custom": True}
        assert (
            cfg.resolve_agent("agent").model.capabilities.max_context_length == 1000000
        )
        assert cfg.get_model("binding").created_at.isoformat().startswith("2026-01-01")
        assert cfg._db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert cfg._db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert cfg._db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not {"model_metadata", "metadata_sources"} & cfg._db._tables()
        # 新协议由 Python 校验，旧 providers CHECK 不再阻塞。
        cfg.create_provider("deep", "deepseek", "https://deep.invalid", "test")
        backup = cfg._db.backup_path
        assert backup and backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT model_instance_id FROM agents").fetchone()[0] == "m"
    with AgentsConfig(path) as cfg:
        assert cfg._db.backup_path is None


@pytest.mark.parametrize(
    "column,value",
    [
        ("base_url", ""),
        ("api_key", "private-test-value"),
        ("capabilities", "{}"),
        ("metadata_provider", ""),
        ("metadata_model", "gpt"),
    ],
)
def test_preflight_rejects_any_nonnull_removed_field_without_showing_credentials(
    tmp_path, column, value
):
    path = tmp_path / "config.db"
    conn = legacy(path, metadata=True)
    conn.execute(f"UPDATE model_instances SET {column}=?", (value,))
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(MigrationError) as caught:
        AgentsConfig(path)
    message = str(caught.value)
    assert "binding" in message and "id=m" in message and column in message
    assert "private-test-value" not in message
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.backup-*")) == []


def test_global_name_conflicts_abort_before_backup(tmp_path):
    path = tmp_path / "config.db"
    conn = legacy(path)
    conn.execute(
        "INSERT INTO providers SELECT 'p2','provider2',provider_type,base_url,api_key,created_at,updated_at FROM providers"
    )
    conn.execute(
        "INSERT INTO model_instances SELECT 'm2','p2',name,model_name,model_type,base_url,api_key,capabilities,created_at,updated_at FROM model_instances"
    )
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError, match="重名绑定.*binding"):
        AgentsConfig(path)
    assert not list(tmp_path.glob("*.backup-*"))


def test_failed_migration_rolls_back_all_schema_and_data(tmp_path):
    path = tmp_path / "config.db"
    legacy(path, metadata=True).close()
    with patch.object(Database, "_seed", side_effect=RuntimeError("injected failure")):
        with pytest.raises(RuntimeError, match="injected failure"):
            AgentsConfig(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "capabilities" in {
            r[1] for r in conn.execute("PRAGMA table_info(model_instances)")
        }
        assert conn.execute("SELECT model_instance_id FROM agents").fetchone()[0] == "m"
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='model_metadata'"
        ).fetchone()
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name='model_capabilities'"
            ).fetchone()
            is None
        )
    with AgentsConfig(path) as cfg:
        assert cfg.get_agent("agent").id == "a"


def test_wal_backup_includes_latest_committed_rows(tmp_path):
    path = tmp_path / "config.db"
    writer = legacy(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("UPDATE agents SET max_tokens=987")
    writer.commit()
    assert path.with_name(path.name + "-wal").exists()
    try:
        with AgentsConfig(path) as cfg:
            assert cfg.get_agent("agent").max_tokens == 987
            backup = cfg._db.backup_path
        with sqlite3.connect(backup) as conn:
            assert conn.execute("SELECT max_tokens FROM agents").fetchone()[0] == 987
    finally:
        writer.close()


@pytest.mark.parametrize("version", [1, 3, 99])
def test_unknown_versions_rejected_without_mutation(tmp_path, version):
    path = tmp_path / "future.db"
    conn = legacy(path)
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="版本"):
        AgentsConfig(path)
    assert path.read_bytes() == before


def test_fk_corruption_aborts_before_migration(tmp_path):
    path = tmp_path / "broken.db"
    conn = legacy(path)
    conn.execute("UPDATE agents SET model_instance_id='missing'")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError, match="外键"):
        AgentsConfig(path)


def test_alias_migration_and_unknown_columns_protection(tmp_path):
    path = tmp_path / "config.db"
    conn = legacy(path)
    conn.execute("UPDATE model_instances SET model_type='asr'")
    conn.commit()
    conn.close()
    with AgentsConfig(path) as cfg:
        assert cfg.get_model("binding").model_type == "stt"
        assert cfg.resolve_model("binding").capabilities.capabilities_type == "stt"
    unknown = tmp_path / "unknown.db"
    conn = legacy(unknown)
    conn.execute("ALTER TABLE providers ADD COLUMN custom TEXT")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError, match="未知"):
        AgentsConfig(unknown)


@pytest.mark.parametrize("nonempty", [False, True])
def test_historical_llm_table_only_removed_when_empty(tmp_path, nonempty):
    path = tmp_path / "old-rename.db"
    conn = legacy(path)
    conn.execute("""CREATE TABLE llm_instances (
        id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
        name TEXT NOT NULL, model_name TEXT NOT NULL, base_url TEXT, api_key TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(provider_id,name))""")
    if nonempty:
        conn.execute(
            "INSERT INTO llm_instances SELECT id,provider_id,name,model_name,base_url,api_key,created_at,updated_at FROM model_instances"
        )
    conn.commit()
    conn.close()
    if nonempty:
        with pytest.raises(MigrationError, match="历史.*仍有记录"):
            AgentsConfig(path)
    else:
        with AgentsConfig(path) as cfg:
            assert cfg.get_model("binding").id == "m"
            assert "llm_instances" not in cfg._db._tables()
