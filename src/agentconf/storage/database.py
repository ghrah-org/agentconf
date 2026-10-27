# SPDX-License-Identifier: Apache-2.0
"""版本化 SQLite schema；拒绝丢弃旧覆盖，备份后事务迁移。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from agentconf.exceptions import MigrationError
from agentconf.models.capability_profile import (
    CapabilityProfile,
    JSON_FIELDS,
    SCALAR_FIELDS,
)
from agentconf.storage.catalog_repo import PROFILE_COLUMNS, insert_profile

SCHEMA_VERSION = 2
PROVIDERS_SQL = """CREATE TABLE providers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider_type TEXT NOT NULL,
    base_url TEXT NOT NULL, api_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""
MODELS_SQL = """CREATE TABLE model_instances (
    id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    name TEXT NOT NULL UNIQUE, model_name TEXT NOT NULL, model_type TEXT NOT NULL DEFAULT 'llm',
    capabilities_key TEXT REFERENCES model_capabilities(model_key) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""
AGENTS_SQL = """CREATE TABLE agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    model_instance_id TEXT NOT NULL REFERENCES model_instances(id) ON DELETE CASCADE,
    temperature REAL, top_p REAL, top_k INTEGER, max_tokens INTEGER, extra_params TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""
_scalar_sql = [
    f"{key} INTEGER CHECK ({key} IS NULL OR {key} {'IN (0, 1)' if key.startswith('supports_') else '> 0'})"
    for key in SCALAR_FIELDS
]
CATALOG_SQL = (
    """CREATE TABLE model_capabilities (
    model_key TEXT PRIMARY KEY, entry_kind TEXT NOT NULL CHECK(entry_kind IN ('model','example')),
    display_name TEXT NOT NULL, family TEXT, model_type TEXT NOT NULL,
    """
    + ", ".join(_scalar_sql + [f"{key} TEXT" for key in JSON_FIELDS])
    + """,
    source TEXT NOT NULL, source_ref TEXT, source_fetched_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK ((entry_kind='example') = (substr(model_key,1,8)='example-')),
    CHECK ((entry_kind='example') = (source='example'))
)"""
)
SCHEMA_SQL = ";\n".join((PROVIDERS_SQL, CATALOG_SQL, MODELS_SQL, AGENTS_SQL)) + ";"
LEGACY_MODEL_COLUMNS = {
    "id",
    "provider_id",
    "name",
    "model_name",
    "model_type",
    "base_url",
    "api_key",
    "capabilities",
    "created_at",
    "updated_at",
}
REMOVED_COLUMNS = {
    "base_url",
    "api_key",
    "capabilities",
    "metadata_provider",
    "metadata_model",
}


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._connection: sqlite3.Connection | None = None
        self.backup_path: Path | None = None

    def connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            self._connection = conn
            try:
                self._init_schema()
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                conn.close()
                self._connection = None
                raise
        return self._connection

    def _tables(self) -> set[str]:
        return {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _preflight(self) -> bool:
        conn = self.conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = self._tables()
        if version == SCHEMA_VERSION:
            required = {"providers", "agents", "model_instances", "model_capabilities"}
            if (
                tables != required
                or self._columns("model_capabilities") != set(PROFILE_COLUMNS)
                or self._columns("model_instances")
                != {
                    "id",
                    "provider_id",
                    "name",
                    "model_name",
                    "model_type",
                    "capabilities_key",
                    "created_at",
                    "updated_at",
                }
            ):
                raise MigrationError("schema 版本与实际表结构不一致")
            return False
        if version != 0:
            raise MigrationError(f"不支持 schema 版本 {version}，本程序仅支持 0 → 2")
        if not tables:
            return True
        if not {"providers", "model_instances", "agents"} <= tables or tables - {
            "providers",
            "model_instances",
            "agents",
            "metadata_sources",
            "model_metadata",
            "llm_instances",
        }:
            raise MigrationError("未知旧库表结构，拒绝自动迁移")
        columns = self._columns("model_instances")
        if columns not in (
            LEGACY_MODEL_COLUMNS,
            LEGACY_MODEL_COLUMNS | {"metadata_provider", "metadata_model"},
        ):
            raise MigrationError("未知旧模型列，拒绝自动迁移")
        expected = {
            "providers": {
                "id",
                "name",
                "provider_type",
                "base_url",
                "api_key",
                "created_at",
                "updated_at",
            },
            "agents": {
                "id",
                "name",
                "model_instance_id",
                "temperature",
                "top_p",
                "top_k",
                "max_tokens",
                "extra_params",
                "created_at",
                "updated_at",
            },
        }
        if any(self._columns(t) != cols for t, cols in expected.items()):
            raise MigrationError("未知旧配置列，拒绝自动迁移")
        if "llm_instances" in tables:
            expected_llm = LEGACY_MODEL_COLUMNS - {"model_type", "capabilities"}
            if self._columns("llm_instances") != expected_llm:
                raise MigrationError("未知历史 llm_instances 列，拒绝自动迁移")
            if conn.execute("SELECT 1 FROM llm_instances LIMIT 1").fetchone():
                raise MigrationError(
                    "历史 llm_instances 仍有记录，需要先人工处理；不会丢弃历史配置"
                )
        custom_objects = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('index','trigger','view') AND sql IS NOT NULL"
        ).fetchall()
        if custom_objects:
            raise MigrationError(
                "旧库有自定义索引、触发器或视图，需要人工处理: "
                + ", ".join(r[0] for r in custom_objects)
            )
        issues = []
        for row in conn.execute("SELECT * FROM model_instances"):
            nonnull = sorted(k for k in REMOVED_COLUMNS & columns if row[k] is not None)
            if nonnull:
                issues.append(
                    f"id={row['id']} name={row['name']} 非空列={','.join(nonnull)}"
                )
        for row in conn.execute(
            "SELECT name, group_concat(id) FROM model_instances GROUP BY name HAVING count(*)>1"
        ):
            issues.append(f"重名绑定 name={row[0]} ids={row[1]}")
        if issues:
            raise MigrationError(
                "迁移预检失败，原库未改动。先人工处理以下配置（不显示凭据值）:\n"
                + "\n".join(issues)
            )
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("旧库存在无效外键引用")
        return True

    def _backup(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.db_path.with_name(
            f"{self.db_path.name}.backup-{stamp}-{uuid4().hex[:8]}"
        )
        # 独立只读连接读取一致快照；当前连接保留写锁，阻止配置并发修改。
        try:
            with closing(
                sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
            ) as source:
                with closing(sqlite3.connect(path)) as destination:
                    source.backup(destination)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.backup_path = path

    def _seed(self) -> None:
        seed = json.loads(
            files("agentconf")
            .joinpath("data/catalog_seed.json")
            .read_text(encoding="utf-8")
        )
        if seed["schema_version"] != SCHEMA_VERSION:
            raise MigrationError("种子版本不匹配")
        for row in seed["profiles"]:
            insert_profile(self.conn, CapabilityProfile.model_validate(row))

    def _init_schema(self) -> None:
        conn = self.conn
        if not self._preflight():
            return
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 获取写锁后重查，处理两个进程同时第一次打开的情形。
            if not self._preflight():
                conn.rollback()
                return
            legacy = bool(self._tables())
            counts = (
                {
                    t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("providers", "model_instances", "agents")
                }
                if legacy
                else {}
            )
            if legacy:
                self._backup()
                conn.execute(
                    PROVIDERS_SQL.replace(
                        "CREATE TABLE providers", "CREATE TABLE providers_new", 1
                    )
                )
                provider_columns = (
                    "id,name,provider_type,base_url,api_key,created_at,updated_at"
                )
                conn.execute(
                    f"INSERT INTO providers_new ({provider_columns}) SELECT {provider_columns} FROM providers"
                )
                conn.execute(CATALOG_SQL)
                conn.execute(
                    MODELS_SQL.replace(
                        "CREATE TABLE model_instances",
                        "CREATE TABLE model_instances_new",
                        1,
                    )
                )
                conn.execute("""INSERT INTO model_instances_new (id,provider_id,name,model_name,model_type,created_at,updated_at)
                    SELECT id,provider_id,name,model_name,CASE model_type WHEN 'asr' THEN 'stt' ELSE model_type END,created_at,updated_at FROM model_instances""")
                conn.execute("DROP TABLE model_instances")
                conn.execute("DROP TABLE providers")
                conn.execute("ALTER TABLE providers_new RENAME TO providers")
                conn.execute(
                    "ALTER TABLE model_instances_new RENAME TO model_instances"
                )
                for table in ("model_metadata", "metadata_sources", "llm_instances"):
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
            else:
                for sql in (PROVIDERS_SQL, CATALOG_SQL, MODELS_SQL, AGENTS_SQL):
                    conn.execute(sql)
            self._seed()
            from agentconf.models import Agent, ModelInstance, Provider

            for table, cls in (
                ("providers", Provider),
                ("model_instances", ModelInstance),
                ("agents", Agent),
            ):
                for row in conn.execute(f"SELECT * FROM {table}"):
                    try:
                        values = dict(row)
                        if table == "agents" and values["extra_params"] is not None:
                            values["extra_params"] = json.loads(values["extra_params"])
                        cls.model_validate(values)
                    except ValueError:
                        raise MigrationError(
                            f"迁移后的配置行无法解析: {table} id={row['id']}"
                        ) from None
            if any(
                conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] != count
                for t, count in counts.items()
            ):
                raise MigrationError("迁移行数校验失败")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise MigrationError("迁移外键校验失败")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
