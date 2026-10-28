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

from agentconf.exceptions import MigrationError, MigrationRequiredError
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime as DateTime
from agentconf.models.capability_profile import (
    CapabilityProfile,
    JSON_FIELDS,
    SCALAR_FIELDS,
)
from agentconf.storage.catalog_repo import PROFILE_COLUMNS, insert_profile, read_profile


class LegacyBase(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)


class LegacyProvider(LegacyBase):
    id: str
    name: str
    provider_type: str
    base_url: str
    api_key: str
    created_at: DateTime
    updated_at: DateTime


class LegacyBinding(LegacyBase):
    id: str
    provider_id: str
    name: str
    model_name: str
    model_type: str
    capabilities_key: str | None = None
    created_at: DateTime
    updated_at: DateTime


class LegacyAgent(LegacyBase):
    id: str
    name: str
    model_instance_id: str
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, ge=0, le=1)
    top_k: int | None = Field(None, ge=1)
    max_tokens: int | None = Field(None, ge=1)
    extra_params: dict | None = None
    created_at: DateTime
    updated_at: DateTime


SCHEMA_VERSION = 4
PROVIDERS_SQL = """CREATE TABLE providers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider_type TEXT NOT NULL,
    base_url TEXT NOT NULL, api_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""
MODEL_INSTANCE_COLUMNS = {
    "id",
    "provider_id",
    "name",
    "model_name",
    "model_type",
    "capabilities_key",
    "created_at",
    "updated_at",
}
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
_capability_sql = ", ".join(_scalar_sql + [f"{key} TEXT" for key in JSON_FIELDS])


def catalog_sql(table: str = "model_capabilities") -> str:
    """生成能力表 SQL；表名参数仅用于迁移期临时表。"""
    return f"""CREATE TABLE {table} (
    model_key TEXT PRIMARY KEY,
    origin TEXT NOT NULL CHECK(origin IN ('upstream','derived','local','example')),
    display_name TEXT NOT NULL, family TEXT, model_type TEXT NOT NULL,
    {_capability_sql},
    annotations TEXT,
    source TEXT NOT NULL, source_ref TEXT, source_fetched_at TEXT,
    derived_from TEXT REFERENCES {table}(model_key) ON DELETE RESTRICT,
    base_fingerprint TEXT CHECK (base_fingerprint IS NULL OR length(base_fingerprint)=64),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK ((origin='example') = (substr(model_key,1,8)='example-')),
    CHECK ((origin='example') = (source='example')),
    CHECK ((origin='derived') = (derived_from IS NOT NULL)),
    CHECK ((origin='derived') = (base_fingerprint IS NOT NULL)),
    CHECK (origin='derived' OR (derived_from IS NULL AND base_fingerprint IS NULL)),
    CHECK (derived_from IS NULL OR derived_from <> model_key),
    CHECK (origin='example' OR source <> 'example')
)"""


CATALOG_SQL = catalog_sql()
SCHEMA_SQL = ";\n".join((PROVIDERS_SQL, CATALOG_SQL, MODELS_SQL, AGENTS_SQL)) + ";"
V3_COLUMNS = (
    set(PROFILE_COLUMNS)
    - {"origin", "derived_from", "base_fingerprint", "annotations", "revision"}
) | {"entry_kind", "managed_by"}
V2_COLUMNS = V3_COLUMNS - {"managed_by"}
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


class LegacyDatabase:
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
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'"
            )
        }

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _preflight(self) -> bool:
        """校验版本与真实表结构；返回是否需要执行迁移。"""
        conn = self.conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = self._tables()
        expected_tables = {
            "providers",
            "agents",
            "model_instances",
            "model_capabilities",
        }
        Provider, Agent = LegacyProvider, LegacyAgent

        if any(
            table in tables and self._columns(table) != set(cls.model_fields)
            for table, cls in (("providers", Provider), ("agents", Agent))
        ):
            raise MigrationError("未知旧配置列，拒绝迁移")
        if (
            version in {2, 3, 4}
            and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('index','trigger','view') AND sql IS NOT NULL"
            ).fetchone()
        ):
            raise MigrationError("旧库有自定义索引、触发器或视图，需要人工处理")
        if version == 4:
            if (
                tables != expected_tables
                or self._columns("model_capabilities") != set(PROFILE_COLUMNS)
                or self._columns("model_instances") != MODEL_INSTANCE_COLUMNS
            ):
                raise MigrationError("schema 版本与实际表结构不一致")
            return False
        if version == 3:
            if (
                tables != expected_tables
                or self._columns("model_capabilities") != V3_COLUMNS
                or self._columns("model_instances") != MODEL_INSTANCE_COLUMNS
            ):
                raise MigrationError("schema 版本与实际表结构不一致")
            return True
        if version == 2:
            if (
                tables != expected_tables
                or self._columns("model_capabilities") != V2_COLUMNS
                or self._columns("model_instances") != MODEL_INSTANCE_COLUMNS
            ):
                raise MigrationError("schema 版本与实际表结构不一致")
            return True
        if version != 0:
            raise MigrationError(
                f"不支持 schema 版本 {version}，本程序仅支持 0/2/3 → 4"
            )
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
        if self.backup_path is not None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.db_path.with_name(
            f"{self.db_path.name}.backup-{stamp}-{uuid4().hex[:8]}"
        )
        # 独立只读连接读取一致快照；当前连接保留写锁，阻止配置并发修改。
        created = False
        try:
            path.touch(mode=0o600, exist_ok=False)
            created = True
            with closing(
                sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
            ) as source:
                with closing(sqlite3.connect(path)) as destination:
                    source.backup(destination)
        except Exception:
            if created:
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

    def _init_schema(
        self,
        *,
        allow_upgrade: bool = False,
        expected_digest: str | None = None,
        manage_transaction: bool = True,
    ) -> None:
        conn = self.conn
        if not self._preflight():
            if expected_digest is not None:
                raise MigrationError("预览后数据库已变化，请重新 prepare")
            return
        if self._tables() and not allow_upgrade:
            raise MigrationRequiredError(
                "旧库需要显式迁移，请使用 agentconf migrate prepare/apply"
            )
        if manage_transaction:
            conn.execute("PRAGMA foreign_keys=OFF")
        try:
            if manage_transaction:
                conn.execute("BEGIN IMMEDIATE")
            if expected_digest is not None:
                from agentconf.migrations import database_digest

                if database_digest(conn) != expected_digest:
                    raise MigrationError("预览后数据库已变化，请重新 prepare")
            # 获取写锁后重查，处理两个进程同时第一次打开的情形。
            if not self._preflight():
                conn.rollback()
                return
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                self._backup()
                conn.execute(
                    "ALTER TABLE model_capabilities ADD COLUMN managed_by TEXT CHECK (managed_by IS NULL OR (length(managed_by)>0 AND managed_by NOT IN ('manual','example') AND entry_kind='model' AND managed_by=source))"
                )
                if conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise MigrationError("迁移外键校验失败")
                version = 3
            if version == 3:
                self._backup()
                self._migrate_v3_to_v4()
                version = 4
            if version == 0:
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
                    for table in (
                        "model_metadata",
                        "metadata_sources",
                        "llm_instances",
                    ):
                        conn.execute(f"DROP TABLE IF EXISTS {table}")
                else:
                    for sql in (PROVIDERS_SQL, CATALOG_SQL, MODELS_SQL, AGENTS_SQL):
                        conn.execute(sql)
                if not legacy:
                    self._seed()
                Agent, ModelInstance, Provider = (
                    LegacyAgent,
                    LegacyBinding,
                    LegacyProvider,
                )

                for table, cls in (
                    ("providers", Provider),
                    ("model_instances", ModelInstance),
                    ("agents", Agent),
                ):
                    for row in conn.execute(f"SELECT * FROM {table}"):
                        try:
                            values = dict(row)
                            if table == "agents" and values["extra_params"] is not None:
                                values["extra_params"] = json.loads(
                                    values["extra_params"]
                                )
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
                version = 4
            if version != 4:
                raise MigrationError(f"迁移未到达 schema {SCHEMA_VERSION}")
            self._validate_rows()
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if manage_transaction:
                conn.commit()
        except Exception:
            if manage_transaction:
                conn.rollback()
            raise
        finally:
            if manage_transaction:
                conn.execute("PRAGMA foreign_keys=ON")

    def _migrate_v3_to_v4(self) -> None:
        """重建能力表：entry_kind/managed_by → origin，并拆分说明字段。"""
        conn = self.conn
        rows = conn.execute(
            "SELECT * FROM model_capabilities ORDER BY model_key"
        ).fetchall()
        temp = "model_capabilities_v4"
        conn.execute(catalog_sql(temp))
        for row in rows:
            insert_profile(conn, read_profile(row), table=temp)
        conn.execute("DROP TABLE model_capabilities")
        conn.execute(f"ALTER TABLE {temp} RENAME TO model_capabilities")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("迁移外键校验失败")

    def _validate_rows(self) -> None:
        Agent, ModelInstance, Provider = LegacyAgent, LegacyBinding, LegacyProvider

        if self.conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("迁移外键校验失败")
        for table, cls in (
            ("providers", Provider),
            ("model_instances", ModelInstance),
            ("agents", Agent),
        ):
            for row in self.conn.execute(f"SELECT * FROM {table}"):
                values = dict(row)
                if table == "agents" and values["extra_params"] is not None:
                    values["extra_params"] = json.loads(values["extra_params"])
                try:
                    cls.model_validate(values)
                except ValueError:
                    raise MigrationError(
                        f"配置行无法解析: {table} id={row['id']}"
                    ) from None
        for row in self.conn.execute("SELECT * FROM model_capabilities"):
            profile = read_profile(row)
            if profile.derived_from:
                parent = self.conn.execute(
                    "SELECT origin, model_type, source FROM model_capabilities WHERE model_key=?",
                    (profile.derived_from,),
                ).fetchone()
                if not parent or tuple(parent) != (
                    "upstream",
                    profile.model_type,
                    profile.source,
                ):
                    raise MigrationError("派生档案的父项、用途或来源不匹配")
        if self.conn.execute(
            "SELECT 1 FROM model_instances m JOIN model_capabilities c ON m.capabilities_key=c.model_key WHERE m.model_type<>c.model_type"
        ).fetchone():
            raise MigrationError("模型绑定与能力档案类型不一致")

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
