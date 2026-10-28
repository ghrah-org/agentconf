# SPDX-License-Identifier: Apache-2.0
"""四表 schema；初始化离线，已有旧库只允许显式迁移。"""

from __future__ import annotations

import json
from importlib.resources import files

from agentconf.exceptions import MigrationError, MigrationRequiredError
from agentconf.storage.legacy import LegacyDatabase, catalog_sql
from agentconf.storage.catalog_repo import PROFILE_COLUMNS, insert_profile, read_profile
from agentconf.models.capability_profile import CapabilityProfile
from agentconf.storage.resources import ResourceRepository, SPEC

SCHEMA_VERSION = 5
PROVIDERS_SQL = """CREATE TABLE providers (
 id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE CHECK(length(trim(name))>0),
 provider_type TEXT NOT NULL, base_url TEXT NOT NULL, api_key TEXT,
 connection_options TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)"""
MODELS_SQL = """CREATE TABLE model_endpoints (
 id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE CHECK(length(trim(name))>0),
 provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 model_name TEXT NOT NULL, model_type TEXT NOT NULL, api_format TEXT NOT NULL CHECK(length(trim(api_format))>0),
 capabilities_key TEXT REFERENCES model_capabilities(model_key) ON DELETE RESTRICT ON UPDATE RESTRICT,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)"""
MCP_SQL = """CREATE TABLE mcp_servers (
 id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE CHECK(length(trim(name))>0),
 transport TEXT NOT NULL CHECK(length(trim(transport))>0), settings_json TEXT NOT NULL DEFAULT '{}',
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)"""
CATALOG_SQL = catalog_sql()
SCHEMA_SQL = ";\n".join((PROVIDERS_SQL, CATALOG_SQL, MODELS_SQL, MCP_SQL)) + ";"


class Database(LegacyDatabase):
    def _preflight(self) -> bool:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        tables = self._tables()
        if not tables and version == 0:
            return True
        if version == 5:
            expected = {
                "providers",
                "model_endpoints",
                "model_capabilities",
                "mcp_servers",
            }
            columns = {
                "providers": set(SPEC["providers"][0].model_fields),
                "model_endpoints": set(SPEC["model_endpoints"][0].model_fields),
                "mcp_servers": (set(SPEC["mcp_servers"][0].model_fields) - {"settings"})
                | {"settings_json"},
                "model_capabilities": set(PROFILE_COLUMNS),
            }
            if tables != expected or any(
                self._columns(table) != cols for table, cols in columns.items()
            ):
                raise MigrationError("schema 版本与表结构不一致")
            return False
        legacy = LegacyDatabase(self.db_path)
        legacy._connection = self.conn
        legacy._preflight()
        raise MigrationRequiredError(
            "旧库需要显式迁移，请使用 agentconf migrate prepare/apply"
        )

    def _init_schema(self) -> None:
        if not self._preflight():
            return
        conn = self.conn
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not self._preflight():
                conn.rollback()
                return
            for sql in (PROVIDERS_SQL, CATALOG_SQL, MODELS_SQL, MCP_SQL):
                conn.execute(sql)
            self._seed()
            self._validate_rows()
            conn.execute("PRAGMA user_version=5")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

    def _seed(self):
        seed = json.loads(
            files("agentconf")
            .joinpath("data/catalog_seed.json")
            .read_text(encoding="utf-8")
        )
        if seed["schema_version"] != SCHEMA_VERSION:
            raise MigrationError("种子版本不匹配")
        for values in seed["profiles"]:
            insert_profile(self.conn, CapabilityProfile.model_validate(values))

    def _validate_rows(self):
        if self.conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("迁移外键校验失败")
        for table in SPEC:
            ResourceRepository(self.conn, table).list()
        for row in self.conn.execute("SELECT * FROM model_capabilities"):
            profile = read_profile(row)
            if profile.derived_from:
                parent = self.conn.execute(
                    "SELECT origin,model_type,source FROM model_capabilities WHERE model_key=?",
                    (profile.derived_from,),
                ).fetchone()
                if not parent or tuple(parent) != (
                    "upstream",
                    profile.model_type,
                    profile.source,
                ):
                    raise MigrationError("派生档案的父项、用途或来源不一致")
        if self.conn.execute(
            "SELECT 1 FROM model_endpoints m JOIN model_capabilities c ON m.capabilities_key=c.model_key WHERE m.model_type<>c.model_type"
        ).fetchone():
            raise MigrationError("模型端点与能力档案用途不一致")
